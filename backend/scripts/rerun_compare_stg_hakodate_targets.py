#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import traceback
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

from PIL import Image

BACKEND_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = BACKEND_ROOT / "src" / "hakodate_best_method_runtime"
sys.path.insert(0, str(BACKEND_ROOT))
sys.path.insert(0, str(RUNTIME_DIR))

from src.services import hakodate_ocr_evidence_service, master_order_form_template_service, sheet_week_service  # noqa: E402
from src.services.hakodate_cell_ocr_batch_service import build_hakodate_best_method_for_manifest_item  # noqa: E402
from src.services.hakodate_fixed_quad_registration_service import (  # noqa: E402
    build_fixed_quad_template_registration,
    canonical_template_axes_from_workbook,
    render_pdf_page_to_bgr,
)
from src.services.order_service import _estimate_hakodate_template_bbox_from_rendered_image  # noqa: E402
from src.services.ocr_week_rerun_service import compose_api_root  # noqa: E402
from src.services.storage_service import load_bytes_from_uri  # noqa: E402
from src.services.workbook_pdf_renderer import render_workbook_path_to_pdf  # noqa: E402


class HttpClient:
    def __init__(self, *, api_root: str, authorization: str, timeout_seconds: float) -> None:
        self.api_root = api_root.rstrip("/")
        self.authorization = authorization
        self.timeout_seconds = max(float(timeout_seconds), 1.0)

    def request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        query = f"?{urlencode(params, doseq=True)}" if params else ""
        normalized_path = path if path.startswith("/") else f"/{path}"
        payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self.api_root}{normalized_path}{query}",
            data=payload,
            method=method.upper(),
            headers={
                "Authorization": self.authorization,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
                return json.loads(raw.decode("utf-8")) if raw else {}
        except HTTPError as exc:
            raw = exc.read()
            try:
                parsed = json.loads(raw.decode("utf-8")) if raw else {}
            except Exception:
                parsed = {}
            detail = parsed.get("detail") if isinstance(parsed, dict) else None
            raise RuntimeError(f"{method} {path} failed: {exc.code} {detail or exc.reason}") from exc

    def get_json(self, path: str, params: dict[str, Any] | None = None):
        try:
            return _Result(True, 200, self.request_json("GET", path, params=params), None)
        except Exception as exc:  # noqa: BLE001
            return _Result(False, 0, None, str(exc))

    def post_json(self, path: str, body: dict[str, Any] | None = None):
        try:
            return _Result(True, 202, self.request_json("POST", path, body=body), None)
        except Exception as exc:  # noqa: BLE001
            return _Result(False, 0, None, str(exc))


class _Result:
    def __init__(self, ok: bool, status_code: int, data: Any | None, error: str | None) -> None:
        self.ok = ok
        self.status_code = status_code
        self.data = data
        self.error = error


def _operator_auth_header_from_gcloud() -> str:
    raw = subprocess.check_output(
        [
            "gcloud",
            "run",
            "services",
            "describe",
            "worker-stg",
            "--project=sawahospitalsystem",
            "--region=asia-northeast2",
            "--format=json",
        ],
        text=True,
    )
    service = json.loads(raw)
    env: dict[str, str] = {}
    for container in service.get("spec", {}).get("template", {}).get("spec", {}).get("containers", []):
        for item in container.get("env", []):
            if "value" in item:
                env[str(item.get("name"))] = str(item.get("value"))
    user = os.getenv("OPERATOR_USER") or env.get("OPERATOR_USER")
    password = os.getenv("OPERATOR_PASSWORD") or env.get("OPERATOR_PASSWORD")
    if not user or not password:
        raise RuntimeError("OPERATOR_USER/OPERATOR_PASSWORD are unavailable")
    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def _week_sheet_name_from_week_value(value: str) -> str:
    _month_id, start_date, end_date = sheet_week_service.parse_sheet_week_value(value)
    if isinstance(start_date, date) and isinstance(end_date, date):
        return f"{start_date.month}月{start_date.day}日～{end_date.month}月{end_date.day}日"
    return str(value or "").strip()


def _date_range(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def _load_uri_bytes(uri: str) -> bytes:
    try:
        return load_bytes_from_uri(uri)
    except Exception:
        if str(uri or "").startswith("gs://"):
            return subprocess.check_output(["gcloud", "storage", "cat", uri])
        raise


def _collect_orders(
    client: HttpClient,
    *,
    explicit_order_ids: list[str],
    week_start: date,
    week_end: date,
) -> list[dict[str, Any]]:
    if explicit_order_ids:
        return [
            client.request_json("GET", f"/orders/{order_id}")
            for order_id in explicit_order_ids
            if order_id.strip()
        ]
    payload = client.request_json(
        "GET",
        "/orders",
        params={"include_archived": "false", "include_ocr": "false", "limit": 1000},
    )
    raw_orders = payload.get("orders") if isinstance(payload, dict) else []
    orders = [item for item in raw_orders if isinstance(item, dict)]
    week_start_text = week_start.isoformat()
    week_end_text = week_end.isoformat()
    filtered: list[dict[str, Any]] = []
    for order in orders:
        week_value = str(order.get("week_value") or order.get("week") or "").strip()
        if week_start_text in week_value and week_end_text in week_value:
            filtered.append(order)
    return filtered


def _wait_for_workflow_ocr_terminal(
    client: HttpClient,
    *,
    order_id: str,
    timeout_seconds: float,
    poll_seconds: float,
) -> dict[str, Any]:
    started = time.monotonic()
    attempts: list[dict[str, Any]] = []
    terminal_states = {
        "ocr_completed",
        "ocr_failed",
        "ocr_selected",
        "sheet_saved",
        "bagging_ready",
        "output_review",
        "confirmed",
        "facility_template_unresolved",
        "template_version_required",
        "template_version_mismatch",
    }
    while True:
        workflow = client.request_json("GET", f"/orders/{order_id}/workflow-v2")
        state = str(workflow.get("state") or "").strip()
        blockers = list(workflow.get("blockers") or [])
        attempts.append({"state": state, "blockers": blockers, "headline": workflow.get("headline")})
        if state in terminal_states:
            return {
                "terminal": True,
                "timeout": False,
                "state": state,
                "blockers": blockers,
                "workflow": workflow,
                "attempts": attempts,
            }
        if time.monotonic() - started >= timeout_seconds:
            return {
                "terminal": False,
                "timeout": True,
                "state": state,
                "blockers": blockers,
                "workflow": workflow,
                "attempts": attempts,
            }
        time.sleep(max(float(poll_seconds), 0.5))


def _build_local_manifest_item_from_stg(
    *,
    client: HttpClient,
    order: dict[str, Any],
    output_dir: Path,
    render_width: int,
) -> dict[str, Any]:
    order_id = str(order.get("id") or "").strip()
    facility_id = str(order.get("facility") or order.get("facility_code") or "").strip()
    document_uri = str(order.get("document") or order.get("document_uri") or "").strip()
    week_value = str(order.get("week_value") or order.get("week") or "").strip()
    if not order_id or not facility_id or not document_uri:
        raise RuntimeError(f"order context incomplete: {order_id} {facility_id} {document_uri}")

    workflow = client.request_json("GET", f"/orders/{order_id}/workflow-v2")
    facility_payload = client.request_json("GET", f"/facilities/{facility_id}")
    resolved_config = facility_payload.get("resolved_config")
    if not isinstance(resolved_config, dict):
        raise RuntimeError(f"resolved facility config missing: {facility_id}")
    fax_template = resolved_config.get("fax_template") if isinstance(resolved_config.get("fax_template"), dict) else {}
    selected_template_id = str(workflow.get("template_id") or fax_template.get("template_id") or "").strip() or None
    month_id, _week_start, _week_end = sheet_week_service.parse_sheet_week_value(week_value)
    stg_menu_entries: list[dict[str, Any]] | None = None
    if month_id:
        try:
            menu_payload = client.request_json(
                "GET",
                f"/monthly-menus/{month_id}",
                params={"facility_id": facility_id},
            )
            entries = menu_payload.get("entries") if isinstance(menu_payload, dict) else None
            if isinstance(entries, list):
                stg_menu_entries = [item for item in entries if isinstance(item, dict)]
        except Exception:
            stg_menu_entries = None

    case_dir = output_dir / f"{facility_id}_{order_id}" / "local_inputs"
    case_dir.mkdir(parents=True, exist_ok=True)
    fax_pdf = case_dir / f"{order_id}_fax.pdf"
    if not fax_pdf.exists():
        fax_pdf.write_bytes(_load_uri_bytes(document_uri))

    structure_xlsx = master_order_form_template_service.build_facility_template_xlsx(
        facility_config=resolved_config,
        output_path=case_dir / f"{facility_id}_{order_id}_facility_template.xlsx",
        week_value=week_value,
        week_menu_entries=stg_menu_entries,
    )
    structure_pdf = case_dir / f"{structure_xlsx.stem}.pdf"
    if not structure_pdf.exists():
        render_workbook_path_to_pdf(
            structure_xlsx,
            output_path=structure_pdf,
            sheet_name=master_order_form_template_service.FACILITY_TEMPLATE_SHEET_NAME,
        )
    local_template_diagnostics = master_order_form_template_service.build_facility_template_diagnostics(
        facility_config=resolved_config,
        week_value=week_value,
        week_menu_entries=stg_menu_entries,
    )

    accepted_canvas_width = 2362
    accepted_canvas_height = 4273
    template_image = render_pdf_page_to_bgr(str(structure_pdf), width=accepted_canvas_width)
    template_bbox = _estimate_hakodate_template_bbox_from_rendered_image(template_image)
    template_axes_x, template_axes_y = canonical_template_axes_from_workbook(
        structure_xlsx,
        sheet_name=master_order_form_template_service.FACILITY_TEMPLATE_SHEET_NAME,
        canvas_width=accepted_canvas_width,
        canvas_height=accepted_canvas_height,
        table_bbox=template_bbox,
    )
    registration, _images = build_fixed_quad_template_registration(
        facility_code=facility_id,
        order_id=order_id,
        fax_pdf=str(fax_pdf),
        template_pdf=str(structure_pdf),
        quad_px=None,
        manifest_template_bbox=template_bbox,
        canvas_width=accepted_canvas_width,
        canvas_height=accepted_canvas_height,
        render_width=render_width,
        quad_source=None,
        output_dir=case_dir / "fixed_quad",
        template_axes_x=template_axes_x,
        template_axes_y=template_axes_y,
    )
    return {
        "order_id": order_id,
        "facility_code": facility_id,
        "facility_id": facility_id,
        "fax_pdf": str(fax_pdf),
        "template_xlsx": str(structure_xlsx),
        "template_pdf": str(structure_pdf),
        "template_sheet_name": master_order_form_template_service.FACILITY_TEMPLATE_SHEET_NAME,
        "fax_template": fax_template,
        "fax_template_id": selected_template_id,
        "template_id": selected_template_id,
        "local_template_diagnostics": local_template_diagnostics,
        "step2_png": str(Path(registration.outputs["step2"])),
        "template_bbox": template_bbox,
        "template_axes_x": template_axes_x,
        "template_axes_y": template_axes_y,
        "quad_px": registration.quad_px,
        "quad_source": registration.quad_source,
        "week_sheet_name": _week_sheet_name_from_week_value(week_value),
        "source": "stg_order_resolved_config_local_rebuild",
    }


def _round_bbox(value: Any) -> list[float]:
    if not isinstance(value, list) or len(value) != 4:
        return []
    return [round(float(item), 3) for item in value]


def _region_key(region: dict[str, Any]) -> tuple[Any, ...]:
    key = _region_identity_key(region)
    return (*key, tuple(_round_bbox(region.get("bbox"))))


def _region_identity_key(region: dict[str, Any]) -> tuple[Any, ...]:
    metadata = region.get("metadata") if isinstance(region.get("metadata"), dict) else {}
    return (
        str(region.get("sheet_cell") or "").strip(),
        int(region.get("worksheet_row") or 0),
        int(region.get("worksheet_col") or 0),
        str(region.get("semantic_field") or region.get("field") or "").strip(),
        str(metadata.get("field_label") or region.get("field_label") or "").strip(),
    )


def _region_signature(regions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for region in regions:
        if not isinstance(region, dict):
            continue
        metadata = region.get("metadata") if isinstance(region.get("metadata"), dict) else {}
        result.append(
            {
                "sheet_cell": str(region.get("sheet_cell") or "").strip(),
                "worksheet_row": int(region.get("worksheet_row") or 0),
                "worksheet_col": int(region.get("worksheet_col") or 0),
                "field": str(region.get("semantic_field") or region.get("field") or "").strip(),
                "field_label": str(metadata.get("field_label") or region.get("field_label") or "").strip(),
                "bbox": _round_bbox(region.get("bbox")),
                "ocr_candidate": bool(metadata.get("ocr_candidate") if "ocr_candidate" in metadata else region.get("ocr_candidate")),
            }
        )
    return sorted(result, key=lambda item: (item["worksheet_row"], item["worksheet_col"], item["sheet_cell"]))


def _diff_region_signatures(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> dict[str, Any]:
    left_by_key = {_region_identity_key(item): item for item in left}
    right_by_key = {_region_identity_key(item): item for item in right}
    left_keys = set(left_by_key)
    right_keys = set(right_by_key)
    common_keys = left_keys & right_keys
    red_left = {key for key, item in left_by_key.items() if bool(_region_signature([item])[0]["ocr_candidate"])}
    red_right = {key for key, item in right_by_key.items() if bool(_region_signature([item])[0]["ocr_candidate"])}
    bbox_deltas: list[float] = []
    for key in sorted(common_keys):
        left_bbox = _round_bbox(left_by_key[key].get("bbox"))
        right_bbox = _round_bbox(right_by_key[key].get("bbox"))
        if len(left_bbox) == 4 and len(right_bbox) == 4:
            bbox_deltas.append(max(abs(left_value - right_value) for left_value, right_value in zip(left_bbox, right_bbox, strict=True)))
    return {
        "target_local_count": len(left_keys),
        "target_stg_count": len(right_keys),
        "target_common_count": len(common_keys),
        "target_local_only_count": len(left_keys - right_keys),
        "target_stg_only_count": len(right_keys - left_keys),
        "red_local_count": len(red_left),
        "red_stg_count": len(red_right),
        "red_common_count": len(red_left & red_right),
        "red_local_only_count": len(red_left - red_right),
        "red_stg_only_count": len(red_right - red_left),
        "target_local_only": [_serialize_key(key) for key in sorted(left_keys - right_keys)[:30]],
        "target_stg_only": [_serialize_key(key) for key in sorted(right_keys - left_keys)[:30]],
        "red_local_only": [_serialize_key(key) for key in sorted(red_left - red_right)[:30]],
        "red_stg_only": [_serialize_key(key) for key in sorted(red_right - red_left)[:30]],
        "max_common_bbox_delta_px": round(max(bbox_deltas), 6) if bbox_deltas else 0.0,
    }


def _compare_template_diagnostics(
    *,
    client: HttpClient,
    facility_id: str,
    week_value: str,
    local_diagnostics: dict[str, Any],
) -> dict[str, Any]:
    try:
        stg_diagnostics = client.request_json(
            "GET",
            f"/facilities/{facility_id}/generated-fax-template-diagnostics",
            params={"week_value": week_value},
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "unavailable",
            "error": str(exc),
            "local": {
                "master_template_sha256": local_diagnostics.get("master_template_sha256"),
                "facility_template_canonical_digest": local_diagnostics.get("facility_template_canonical_digest"),
            },
        }
    master_match = (
        str(local_diagnostics.get("master_template_sha256") or "")
        == str(stg_diagnostics.get("master_template_sha256") or "")
    )
    facility_digest_match = (
        str(local_diagnostics.get("facility_template_canonical_digest") or "")
        == str(stg_diagnostics.get("facility_template_canonical_digest") or "")
    )
    schema_digest_match = (
        str(local_diagnostics.get("schema_digest") or "")
        == str(stg_diagnostics.get("schema_digest") or "")
    )
    return {
        "status": "ok" if master_match and facility_digest_match and schema_digest_match else "ng",
        "master_template_sha256_match": master_match,
        "facility_template_canonical_digest_match": facility_digest_match,
        "schema_digest_match": schema_digest_match,
        "local": {
            "master_template_sha256": local_diagnostics.get("master_template_sha256"),
            "facility_template_canonical_digest": local_diagnostics.get("facility_template_canonical_digest"),
            "schema_digest": local_diagnostics.get("schema_digest"),
            "generated_end_letter": local_diagnostics.get("generated_end_letter"),
        },
        "stg": {
            "master_template_sha256": stg_diagnostics.get("master_template_sha256"),
            "facility_template_canonical_digest": stg_diagnostics.get("facility_template_canonical_digest"),
            "schema_digest": stg_diagnostics.get("schema_digest"),
            "generated_end_letter": stg_diagnostics.get("generated_end_letter"),
        },
    }


def _serialize_key(key: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "sheet_cell": key[0],
        "worksheet_row": key[1],
        "worksheet_col": key[2],
        "field": key[3],
        "field_label": key[4],
        "bbox": list(key[5]) if len(key) > 5 else None,
    }


def _compare_one(
    *,
    client: HttpClient,
    order: dict[str, Any],
    output_dir: Path,
    render_width: int,
    page: int,
    require_template_parity: bool,
) -> dict[str, Any]:
    order_id = str(order.get("id") or "").strip()
    facility_id = str(order.get("facility") or order.get("facility_code") or "").strip()
    case_dir = output_dir / f"{page:02d}_{facility_id}_{order_id}"
    case_dir.mkdir(parents=True, exist_ok=True)
    preview = client.request_json("GET", f"/orders/{order_id}/hakodate-overlay-preview")
    stg_assignment = preview.get("assignment") if isinstance(preview.get("assignment"), dict) else {}
    stg_targets = [item for item in (stg_assignment.get("target_cells") or []) if isinstance(item, dict)]
    manifest_item = _build_local_manifest_item_from_stg(
        client=client,
        order=order,
        output_dir=case_dir,
        render_width=render_width,
    )
    week_value = str(order.get("week_value") or order.get("week") or "").strip()
    template_parity = _compare_template_diagnostics(
        client=client,
        facility_id=facility_id,
        week_value=week_value,
        local_diagnostics=dict(manifest_item.get("local_template_diagnostics") or {}),
    )
    local_summary, _review_page = build_hakodate_best_method_for_manifest_item(
        item=manifest_item,
        page=page,
        draft_sheet={"fields": [], "rows": []},
        output_dir=case_dir / "local_best_method",
        render_width=render_width,
    )
    local_regions_path = Path(str((local_summary.outputs or {}).get("ocr_regions") or ""))
    local_regions = json.loads(local_regions_path.read_text(encoding="utf-8")) if local_regions_path.exists() else []
    local_targets = hakodate_ocr_evidence_service.target_cells_from_regions(
        [item for item in local_regions if isinstance(item, dict)]
    )
    for local_target, source_region in zip(local_targets, local_regions, strict=False):
        if not isinstance(local_target, dict) or not isinstance(source_region, dict):
            continue
        metadata = dict(local_target.get("metadata") or {})
        metadata["field_label"] = source_region.get("field_label")
        metadata["ocr_candidate"] = bool(source_region.get("ocr_candidate"))
        local_target["metadata"] = metadata
    diff = _diff_region_signatures(local_targets, stg_targets)
    diff_ok = (
        diff["target_local_only_count"] == 0
        and diff["target_stg_only_count"] == 0
        and diff["red_local_only_count"] == 0
        and diff["red_stg_only_count"] == 0
    )
    template_ok = template_parity.get("status") == "ok" or (
        template_parity.get("status") == "unavailable" and not require_template_parity
    )
    result = {
        "order_id": order_id,
        "facility_id": facility_id,
        "status": "ok" if diff_ok and template_ok else "ng",
        "stg_preview_status": preview.get("status"),
        "stg_source_evidence_run_id": preview.get("source_evidence_run_id"),
        "diff": diff,
        "template_parity": template_parity,
        "local_outputs": local_summary.outputs,
        "stg_overlay_uri": preview.get("overlay_uri"),
    }
    (case_dir / "comparison.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (case_dir / "local_target_signature.json").write_text(
        json.dumps(_region_signature(local_targets), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (case_dir / "stg_target_signature.json").write_text(
        json.dumps(_region_signature(stg_targets), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rerun stg workflow-v2 OCR and compare Hakodate target/red cells to local rebuild.")
    parser.add_argument("--base-url", default="https://web-stg-avlnzjjrca-dt.a.run.app")
    parser.add_argument("--api-prefix", default="/api")
    parser.add_argument("--week-start", default="2026-04-26")
    parser.add_argument("--week-end", default="2026-04-30")
    parser.add_argument("--orders", default="", help="Comma-separated order IDs. If empty, collect by line date.")
    parser.add_argument("--output-dir", type=Path, default=BACKEND_ROOT / "tmp" / "stg_hakodate_target_compare")
    parser.add_argument("--render-width", type=int, default=1864)
    parser.add_argument("--http-timeout", type=float, default=420.0)
    parser.add_argument("--wait-timeout", type=float, default=1800.0)
    parser.add_argument("--poll-seconds", type=float, default=8.0)
    parser.add_argument("--skip-rerun", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--require-template-parity", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = args.output_dir / datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    output_dir.mkdir(parents=True, exist_ok=True)
    client = HttpClient(
        api_root=compose_api_root(args.base_url, api_prefix=args.api_prefix),
        authorization=_operator_auth_header_from_gcloud(),
        timeout_seconds=args.http_timeout,
    )
    explicit_orders = [item.strip() for item in args.orders.split(",") if item.strip()]
    orders = _collect_orders(
        client,
        explicit_order_ids=explicit_orders,
        week_start=date.fromisoformat(args.week_start),
        week_end=date.fromisoformat(args.week_end),
    )
    if args.limit:
        orders = orders[: args.limit]
    results: list[dict[str, Any]] = []
    local_overlay_pages: list[Image.Image] = []
    for page, order in enumerate(orders, start=1):
        order_id = str(order.get("id") or "").strip()
        if not order_id:
            continue
        rerun_result: dict[str, Any] | None = None
        wait_result: dict[str, Any] | None = None
        if not args.skip_rerun:
            rerun_result = client.request_json(
                "POST",
                f"/orders/{order_id}/workflow-v2/ocr-runs",
                body={"mode": "hakodate", "force": True, "stale_action": "retry"},
            )
            wait_result = _wait_for_workflow_ocr_terminal(
                client,
                order_id=order_id,
                timeout_seconds=args.wait_timeout,
                poll_seconds=args.poll_seconds,
            )
            if not wait_result.get("terminal") or wait_result.get("state") == "ocr_failed":
                result = {
                    "order_id": order_id,
                    "facility_id": order.get("facility") or order.get("facility_code"),
                    "status": "ng",
                    "rerun": rerun_result,
                    "wait": wait_result,
                    "error": "ocr_rerun_not_terminal_or_failed",
                }
                results.append(result)
                print(json.dumps(result, ensure_ascii=False), flush=True)
                continue
        try:
            result = _compare_one(
                client=client,
                order=order,
                output_dir=output_dir,
                render_width=args.render_width,
                page=page,
                require_template_parity=bool(args.require_template_parity),
            )
            result["rerun"] = rerun_result
            result["wait"] = wait_result
            overlay_path = str(((result.get("local_outputs") or {}).get("overlay") or "")).strip()
            if overlay_path and Path(overlay_path).exists():
                local_overlay_pages.append(Image.open(overlay_path).convert("RGB"))
        except (HTTPError, URLError, RuntimeError, ValueError) as exc:
            result = {
                "order_id": order_id,
                "facility_id": order.get("facility") or order.get("facility_code"),
                "status": "ng",
                "error": str(exc),
                "traceback": traceback.format_exc(),
                "rerun": rerun_result,
                "wait": wait_result,
            }
        results.append(result)
        print(
            json.dumps(
                {
                    "order_id": result.get("order_id"),
                    "facility_id": result.get("facility_id"),
                    "status": result.get("status"),
                    "diff": result.get("diff"),
                    "template_parity": result.get("template_parity"),
                    "error": result.get("error"),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    overlay_pdf = None
    if local_overlay_pages:
        overlay_pdf_path = output_dir / "local_best_method_overlay_all_orders.pdf"
        local_overlay_pages[0].save(
            overlay_pdf_path,
            save_all=True,
            append_images=local_overlay_pages[1:],
        )
        overlay_pdf = str(overlay_pdf_path)
    summary = {
        "status": "ok" if all(item.get("status") == "ok" for item in results) else "ng",
        "order_count": len(results),
        "output_dir": str(output_dir),
        "local_overlay_pdf": overlay_pdf,
        "results": results,
    }
    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"summary": str(summary_path), "status": summary["status"], "order_count": len(results)}, ensure_ascii=False))
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
