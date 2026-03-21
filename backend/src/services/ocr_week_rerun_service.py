from __future__ import annotations

import json
import hashlib
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol


@dataclass
class ApiJsonResult:
    status_code: int
    ok: bool
    data: Any | None = None
    error: str | None = None


class OcrWeekBatchClient(Protocol):
    def get_json(self, path: str, params: dict[str, Any] | None = None) -> ApiJsonResult: ...

    def post_json(self, path: str, body: dict[str, Any] | None = None) -> ApiJsonResult: ...


def compose_api_root(base_url: str, api_prefix: str = "/api") -> str:
    normalized_base = str(base_url or "").strip().rstrip("/")
    if not normalized_base:
        raise ValueError("base_url is required")
    normalized_prefix = str(api_prefix or "").strip()
    if not normalized_prefix:
        return normalized_base
    if not normalized_prefix.startswith("/"):
        normalized_prefix = f"/{normalized_prefix}"
    if normalized_base.endswith(normalized_prefix):
        return normalized_base
    return f"{normalized_base}{normalized_prefix}"


def iter_date_range(start_date: date, end_date: date) -> list[date]:
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")
    days = (end_date - start_date).days
    return [start_date + timedelta(days=offset) for offset in range(days + 1)]


def collect_week_orders(
    client: OcrWeekBatchClient,
    *,
    start_date: date,
    end_date: date,
    facility: str | None = None,
    status: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    collected: dict[str, dict[str, Any]] = {}
    date_summaries: list[dict[str, Any]] = []
    for target_date in iter_date_range(start_date, end_date):
        params: dict[str, Any] = {"date": target_date.isoformat()}
        if facility:
            params["facility"] = facility
        if status:
            params["status"] = status
        result = client.get_json("/orders/by-line-date", params=params)
        payload = result.data if isinstance(result.data, dict) else {}
        raw_orders = payload.get("orders") if isinstance(payload, dict) else None
        orders = [item for item in (raw_orders or []) if isinstance(item, dict)]
        date_summaries.append(
            {
                "date": target_date.isoformat(),
                "status_code": result.status_code,
                "ok": result.ok,
                "count": len(orders),
                "error": result.error,
            }
        )
        for order in orders:
            order_id = str(order.get("id") or "").strip()
            if not order_id:
                continue
            existing = collected.get(order_id)
            if existing is None:
                collected[order_id] = order
                continue
            received_at = str(order.get("received_at") or "")
            existing_received_at = str(existing.get("received_at") or "")
            if received_at > existing_received_at:
                collected[order_id] = order

    orders = list(collected.values())
    orders.sort(key=lambda item: (str(item.get("received_at") or ""), str(item.get("id") or "")))
    return orders, date_summaries


def fetch_order_backup_bundle(
    client: OcrWeekBatchClient,
    *,
    order_id: str,
    history_limit: int = 200,
) -> dict[str, Any]:
    endpoints = {
        "order": (f"/orders/{order_id}", None),
        "ocr_output": (f"/orders/{order_id}/ocr-output", None),
        "ocr_pages": (f"/orders/{order_id}/ocr-pages", None),
        "ocr_sheet": (f"/orders/{order_id}/ocr-sheet", None),
        "ocr_history": (f"/orders/{order_id}/ocr-history", None),
        "order_history": (f"/orders/{order_id}/history", {"limit": history_limit}),
        "ocr_raw": (f"/orders/{order_id}/ocr-raw", None),
    }
    bundle: dict[str, Any] = {
        "order_id": order_id,
        "fetched_at": datetime.utcnow().isoformat() + "Z",
        "artifacts": {},
    }
    for name, (path, params) in endpoints.items():
        result = client.get_json(path, params=params)
        bundle["artifacts"][name] = asdict(result)
    return bundle


def build_reparse_request_body(
    *,
    llm_assist: bool = True,
    ocr_provider: str | None = None,
    ocr_prompt: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"llm_assist": bool(llm_assist)}
    normalized_provider = str(ocr_provider or "").strip().lower()
    if normalized_provider:
        body["ocr_provider"] = normalized_provider
    prompt = str(ocr_prompt or "").strip()
    if prompt:
        body["ocr_prompt"] = prompt
    return body


def trigger_reparse(
    client: OcrWeekBatchClient,
    *,
    order_id: str,
    llm_assist: bool = True,
    ocr_provider: str | None = None,
    ocr_prompt: str | None = None,
) -> ApiJsonResult:
    return client.post_json(
        f"/orders/{order_id}/reparse",
        body=build_reparse_request_body(
            llm_assist=llm_assist,
            ocr_provider=ocr_provider,
            ocr_prompt=ocr_prompt,
        ),
    )


def wait_for_ocr_terminal(
    client: OcrWeekBatchClient,
    *,
    order_id: str,
    timeout_seconds: float = 900.0,
    poll_seconds: float = 5.0,
    sleep_fn=None,
    monotonic_fn=None,
) -> dict[str, Any]:
    sleeper = sleep_fn
    if sleeper is None:
        from time import sleep as sleeper  # noqa: PLC0415
    monotonic = monotonic_fn
    if monotonic is None:
        from time import monotonic as monotonic  # noqa: PLC0415

    started_at = monotonic()
    attempts: list[dict[str, Any]] = []
    while True:
        result = client.get_json(f"/orders/{order_id}")
        status_value = None
        error_value = None
        if isinstance(result.data, dict):
            status_value = result.data.get("ocr_status")
            error_value = result.data.get("ocr_error")
        attempts.append(
            {
                "status_code": result.status_code,
                "ok": result.ok,
                "ocr_status": status_value,
                "ocr_error": error_value,
            }
        )
        normalized_status = str(status_value or "").strip().lower()
        if normalized_status and normalized_status not in {"running", "pending"}:
            return {
                "order_id": order_id,
                "terminal": True,
                "timeout": False,
                "attempts": attempts,
                "order": result.data,
            }
        if monotonic() - started_at >= max(timeout_seconds, 0.0):
            return {
                "order_id": order_id,
                "terminal": False,
                "timeout": True,
                "attempts": attempts,
                "order": result.data,
            }
        sleeper(max(poll_seconds, 0.1))


def write_json(path: str | Path, payload: Any) -> str:
    resolved_path = Path(path)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return str(resolved_path)


def write_order_backup_bundle(
    *,
    output_root: str | Path,
    bundle: dict[str, Any],
) -> dict[str, str]:
    order_id = str(bundle.get("order_id") or "").strip()
    if not order_id:
        raise ValueError("bundle.order_id is required")
    order_dir = Path(output_root) / order_id
    order_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}
    written["bundle"] = write_json(order_dir / "backup_bundle.json", bundle)
    artifacts = bundle.get("artifacts")
    if isinstance(artifacts, dict):
        for name, payload in artifacts.items():
            written[str(name)] = write_json(order_dir / f"{name}.json", payload)
    return written


def load_order_backup_bundle(*, output_root: str | Path, order_id: str) -> dict[str, Any]:
    order_dir = Path(output_root) / order_id
    bundle_path = order_dir / "backup_bundle.json"
    if bundle_path.exists():
        return json.loads(bundle_path.read_text(encoding="utf-8"))
    artifacts: dict[str, Any] = {}
    for path in sorted(order_dir.glob("*.json")):
        if path.name in {"backup_bundle.json", "reparse_request.json", "reparse_wait.json"}:
            continue
        artifacts[path.stem] = json.loads(path.read_text(encoding="utf-8"))
    return {
        "order_id": order_id,
        "fetched_at": None,
        "artifacts": artifacts,
    }


def _stable_digest(value: Any) -> str:
    normalized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _compact_order_lines(lines: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for line in lines or []:
        if not isinstance(line, dict):
            continue
        compact.append(
            {
                "date": line.get("date"),
                "daypart": line.get("daypart"),
                "menu_name": line.get("menu_name"),
                "diet_type": line.get("diet_type"),
                "area_id": line.get("area_id"),
                "quantity_original": line.get("quantity_original"),
                "quantity_corrected": line.get("quantity_corrected"),
                "change_note": line.get("change_note"),
            }
        )
    return compact


def summarize_artifact_for_compare(name: str, response: dict[str, Any] | None) -> dict[str, Any]:
    payload = response if isinstance(response, dict) else {}
    data = payload.get("data")
    summary: dict[str, Any] = {
        "status_code": payload.get("status_code"),
        "ok": payload.get("ok"),
        "error": payload.get("error"),
        "data_digest": _stable_digest(data),
    }
    if name == "order" and isinstance(data, dict):
        lines = _compact_order_lines(data.get("lines"))
        summary.update(
            {
                "ocr_status": data.get("ocr_status"),
                "ocr_error": data.get("ocr_error"),
                "week_value": data.get("week_value"),
                "line_count": len(lines),
                "line_digest": _stable_digest(lines),
            }
        )
        return summary
    if name == "ocr_output" and isinstance(data, dict):
        rows = data.get("rows") if isinstance(data.get("rows"), list) else []
        metrics = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}
        summary.update(
            {
                "provider": metrics.get("provider"),
                "row_count": metrics.get("row_count", len(rows)),
                "line_count": metrics.get("line_count"),
                "table_raw_present": bool(data.get("table_raw")),
                "rows_digest": _stable_digest(rows),
            }
        )
        return summary
    if name == "ocr_sheet" and isinstance(data, dict):
        rows = data.get("rows") if isinstance(data.get("rows"), list) else []
        summary.update(
            {
                "source": data.get("source"),
                "warning_count": len(data.get("warnings") or []),
                "row_count": len(rows),
                "rows_digest": _stable_digest(rows),
            }
        )
        return summary
    if name in {"ocr_history", "order_history"} and isinstance(data, dict):
        items = data.get("revisions") if name == "ocr_history" else data.get("items")
        if not isinstance(items, list):
            items = []
        summary.update({"entry_count": len(items), "entries_digest": _stable_digest(items)})
        return summary
    if name == "ocr_pages" and isinstance(data, dict):
        pages = data.get("pages") if isinstance(data.get("pages"), list) else []
        summary.update({"page_count": len(pages), "pages_digest": _stable_digest(pages)})
        return summary
    if name == "ocr_raw":
        summary.update({"raw_digest": _stable_digest(data)})
        return summary
    return summary


def compare_backup_bundle_to_live(
    *,
    backup_bundle: dict[str, Any],
    live_bundle: dict[str, Any],
) -> dict[str, Any]:
    backup_artifacts = backup_bundle.get("artifacts") if isinstance(backup_bundle, dict) else {}
    live_artifacts = live_bundle.get("artifacts") if isinstance(live_bundle, dict) else {}
    names = sorted(set((backup_artifacts or {}).keys()) | set((live_artifacts or {}).keys()))
    artifacts: dict[str, Any] = {}
    changed_names: list[str] = []
    for name in names:
        before = summarize_artifact_for_compare(name, (backup_artifacts or {}).get(name))
        after = summarize_artifact_for_compare(name, (live_artifacts or {}).get(name))
        changed = before != after
        artifacts[name] = {
            "changed": changed,
            "before": before,
            "after": after,
        }
        if changed:
            changed_names.append(name)
    return {
        "order_id": str(live_bundle.get("order_id") or backup_bundle.get("order_id") or ""),
        "changed": bool(changed_names),
        "changed_artifacts": changed_names,
        "artifacts": artifacts,
    }
