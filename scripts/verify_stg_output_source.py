#!/usr/bin/env python3
"""Actions-only, GET-only staging evidence for the output canonical source."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from urllib.parse import parse_qs, quote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from staging_db_target import read_http  # noqa: E402
from verify_stg_menu_master_ui import WEB, WORKER, deployed_sources, private_command  # noqa: E402

OUTPUT = ROOT / "tmp/output-source-live"
SA_EMAIL = "sawa-github-deploy-stg@sawahospitalsystem.iam.gserviceaccount.com"
ORDER_PATTERN = re.compile(r"ORD[0-9A-Za-z_-]+$")


class Blocked(RuntimeError):
    pass


def require(ok: object, code: str) -> None:
    if not ok:
        raise Blocked(code)


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def write_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(".part")
    temporary.write_bytes(canonical(value))
    temporary.replace(path)


def require_context(env: dict[str, str], head: str) -> tuple[str, str]:
    fixed = {
        "GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/develop", "GITHUB_REF_NAME": "develop",
        "PROJECT_ID": "sawahospitalsystem", "REGION": "asia-northeast2",
        "WEB_SERVICE": "web-stg", "WORKER_SERVICE": "worker-stg", "WEB_URL": WEB,
        "WORKER_URL": WORKER, "LIVE_SERVICE_ACCOUNT": SA_EMAIL,
    }
    for key, expected in fixed.items():
        require(env.get(key) == expected, "context-invalid-" + key)
    require(bool(re.fullmatch(r"[1-9][0-9]*", env.get("GITHUB_RUN_ID", ""))), "context-invalid-run-id")
    require(bool(re.fullmatch(r"[1-9][0-9]*", env.get("GITHUB_RUN_ATTEMPT", ""))), "context-invalid-run-attempt")
    require(bool(re.fullmatch(r"[0-9a-f]{40}", env.get("GITHUB_SHA", ""))) and env["GITHUB_SHA"] == head, "checkout-source-SHA-mismatch")
    order_id = env.get("OUTPUT_SOURCE_VERIFY_ORDER_ID", "").strip()
    require(bool(ORDER_PATTERN.fullmatch(order_id)), "order-id-required-or-invalid")
    target_date = env.get("OUTPUT_SOURCE_VERIFY_DATE", "").strip()
    require(bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", target_date)), "target-date-required-or-invalid")
    require(bool(re.fullmatch(r"[0-9]+-[a-z0-9]+\.apps\.googleusercontent\.com", env.get("GOOGLE_OAUTH_CLIENT_ID", ""))), "staging-audience-missing")
    require(env.get("GOOGLE_OAUTH_CLIENT_ID") != env.get("PROD_GOOGLE_OAUTH_CLIENT_ID"), "production-audience-forbidden")
    return order_id, target_date


def check_token(token: str, audience: str) -> None:
    try:
        segment = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))
        valid = claims.get("email") == SA_EMAIL and claims.get("email_verified") is True
        valid = valid and claims.get("aud") == audience and claims.get("exp", 0) > time.time() + 120
    except Exception:
        valid = False
    require(valid, "fresh-deploy-SA-ID-token-required")


def get(path: str, token: str) -> tuple[int, object | None, str]:
    parsed = urlsplit(path)
    require(not parsed.scheme and not parsed.netloc and not parsed.fragment, "GET-path-rejected")
    query = parse_qs(parsed.query, keep_blank_values=True)
    order_route = rf"/api/orders/{ORDER_PATTERN.pattern[:-1]}"
    allowed = (
        (bool(re.fullmatch(order_route, parsed.path)) and not query)
        or (bool(re.fullmatch(order_route + r"/workflow-v2", parsed.path)) and not query)
        or (bool(re.fullmatch(order_route + r"/workflow-v2/sheet", parsed.path)) and not query)
        or (parsed.path == "/api/orders/daily-output-context" and set(query) == {"date", "facility"}
            and all(len(query[key]) == 1 for key in ("date", "facility"))
            and bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", query["date"][0])) and bool(query["facility"][0]))
        or (parsed.path == "/api/orders/daily-bags" and set(query) == {"date", "facility"}
            and all(len(query[key]) == 1 for key in ("date", "facility"))
            and bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", query["date"][0])) and bool(query["facility"][0]))
        or (parsed.path == "/api/totals" and set(query) == {"date", "include_order_refs"}
            and all(len(query[key]) == 1 for key in ("date", "include_order_refs"))
            and bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", query["date"][0])) and query["include_order_refs"] == ["true"])
    )
    require(allowed, "GET-path-rejected")
    status, body = read_http(WEB + path, token)
    digest = hashlib.sha256(body).hexdigest()
    if not body:
        return status, None, digest
    try:
        return status, json.loads(body), digest
    except json.JSONDecodeError:
        return status, None, digest


def _row(value: object) -> dict[str, object] | None:
    return value if isinstance(value, dict) else None


def sanitize_order(value: object) -> dict[str, object]:
    data = value if isinstance(value, dict) else {}
    return {"id": data.get("id"), "facility": data.get("facility"), "status": data.get("status"), "week": data.get("week"),
            "lines": [{key: row.get(key) for key in ("date", "daypart", "menu_name", "area_id", "quantity_original", "quantity_corrected")}
                      for row in data.get("lines", []) if isinstance(row, dict)]}


def sanitize_workflow(value: object) -> dict[str, object]:
    data = value if isinstance(value, dict) else {}
    return {key: data.get(key) for key in ("order_id", "state", "draft_id", "saved_sheet_id", "template_version_id", "week_start", "week_end", "headline", "blockers", "warnings", "blockers_json", "warnings_json")}


def sanitize_sheet(value: object) -> dict[str, object]:
    data = value if isinstance(value, dict) else {}
    sheet = data.get("sheet") if isinstance(data.get("sheet"), dict) else data
    result = {key: data.get(key) for key in ("saved_sheet_id", "order_id", "template_version_id", "state")}
    result["sheet"] = {key: sheet.get(key) for key in ("fields", "rows", "source", "state", "blockers", "warnings") if key in sheet}
    return result


def sanitize_context(value: object) -> dict[str, object]:
    data = value if isinstance(value, dict) else {}
    sections = data.get("sections") if isinstance(data.get("sections"), dict) else {}
    return {"date": data.get("date"), "facility": data.get("facility"), "ok": data.get("ok"),
            "sections": {key: {"status": item.get("status"), "error": {field: item.get("error", {}).get(field) for field in ("type", "message")} if isinstance(item.get("error"), dict) else None}
                         for key, item in sections.items() if key in {"daily_bags", "daily_bags_audit", "totals"} and isinstance(item, dict)}}


def sanitize_quantity_rows(value: object) -> object:
    if not isinstance(value, dict):
        return {}
    groups = value.get("groups")
    if isinstance(groups, list):
        return {"date": value.get("date"), "groups": [
            {
                **{key: group.get(key) for key in ("daypart", "menu_category", "menu_name")},
                "diet_groups": [
                    {
                        **{key: diet.get(key) for key in ("diet_type", "total_quantity")},
                        "bag_type_groups": [
                            {
                                **{key: bag.get(key) for key in ("bag_type", "bag_count", "total_quantity")},
                                "breakdowns": [
                                    {
                                        **{key: breakdown.get(key) for key in ("area_id", "quantity")},
                                        "order_refs": [
                                            {key: ref.get(key) for key in ("order_id", "area_id", "quantity")}
                                            for ref in breakdown.get("order_refs", []) if isinstance(ref, dict)
                                        ],
                                    }
                                    for breakdown in bag.get("breakdowns", []) if isinstance(breakdown, dict)
                                ],
                            }
                            for bag in diet.get("bag_type_groups", []) if isinstance(bag, dict)
                        ],
                    }
                    for diet in group.get("diet_groups", []) if isinstance(diet, dict)
                ],
            }
            for group in groups if isinstance(group, dict)
        ]}
    rows = value.get("rows")
    if isinstance(rows, list):
        return {"rows": [{key: row.get(key) for key in ("date", "daypart", "menu_category", "menu_name", "diet_type", "quantity")}
                         for row in rows if isinstance(row, dict)]}
    return {}


def sanitize_totals(value: object) -> dict[str, object]:
    data = value if isinstance(value, dict) else {}
    return {"date_from": data.get("date_from"), "date_to": data.get("date_to"), "rows": [
        {
            **{key: row.get(key) for key in ("date", "daypart", "menu_category", "menu_name", "diet_type", "quantity")},
            "order_refs": [
                {key: ref.get(key) for key in ("order_id", "facility_id", "source_diet_type", "aggregated_diet_type", "area_id", "quantity")}
                for ref in row.get("order_refs", []) if isinstance(ref, dict)
            ],
        }
        for row in data.get("rows", []) if isinstance(row, dict)
    ]}


def _sheet_contains_date(sheet: dict[str, object], target_date: str) -> bool:
    payload = sheet.get("sheet") if isinstance(sheet.get("sheet"), dict) else {}
    fields, rows = payload.get("fields"), payload.get("rows")
    if not isinstance(fields, list) or not isinstance(rows, list):
        return False
    indexes = [index for index, value in enumerate(fields) if value in {"date", "date_mmdd"}]
    if len(indexes) != 1:
        return False
    accepted = {target_date, target_date[5:].replace("-", "/")}
    return any(isinstance(row, list) and len(row) > indexes[0] and str(row[indexes[0]]).strip() in accepted for row in rows)


def extract_case(order: dict[str, object], workflow: dict[str, object], sheet: dict[str, object], context: dict[str, object], target_date: str) -> dict[str, object]:
    expected = (workflow.get("saved_sheet_id"), workflow.get("order_id"), workflow.get("template_version_id"))
    actual = (sheet.get("saved_sheet_id"), sheet.get("order_id"), sheet.get("template_version_id"))
    if not all(isinstance(value, str) and value.strip() for value in (*expected, *actual)) or expected != actual:
        return {"kind": "not-verified", "reason": "canonical-saved-sheet-lineage-mismatch"}
    week_start, week_end = workflow.get("week_start"), workflow.get("week_end")
    if not all(isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) for value in (week_start, week_end)) or not week_start <= target_date <= week_end:
        return {"kind": "not-verified", "reason": "workflow-week-does-not-contain-target-date", "savedSheetId": expected[0], "targetDate": target_date}
    if order.get("id") != expected[1] or not _sheet_contains_date(sheet, target_date):
        return {"kind": "not-verified", "reason": "canonical-target-date-not-confirmed", "savedSheetId": expected[0], "targetDate": target_date}
    sections = context.get("sections") if isinstance(context.get("sections"), dict) else {}
    if not all(isinstance(sections.get(name), dict) and sections[name].get("status") == "fulfilled" for name in ("daily_bags", "totals")):
        return {"kind": "not-verified", "reason": "output-sections-not-captured", "savedSheetId": expected[0], "targetDate": target_date}
    return {"kind": "captured", "savedSheetId": expected[0], "targetDate": target_date,
            "note": "captured; parent quantity comparison against saved sheet, daily bags, totals order_refs, and PNG is required before PASS"}


def run_browser(token: str, order_id: str, target_date: str, output: Path) -> None:
    env = {key: value for key, value in os.environ.items() if key not in {"DEBUG", "PWDEBUG", "NODE_OPTIONS"}}
    env.update({"LIVE_ID_TOKEN": token, "OUTPUT_SOURCE_ORDER_ID": order_id, "OUTPUT_SOURCE_TARGET_DATE": target_date,
                "OUTPUT_SOURCE_WEB": WEB, "OUTPUT_SOURCE_SHA": os.environ["GITHUB_SHA"]})
    process = subprocess.Popen(["node", str(ROOT / "frontend/scripts/verify-output-source-live.mjs"), str(output)], cwd=ROOT, env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        require(process.wait(timeout=180) == 0, "browser-failed-see-sanitized-browser-result")
    except subprocess.TimeoutExpired:
        raise Blocked("browser-deadline-exceeded") from None
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    result: dict[str, object] = {"scope": "GET-only stg output-source verification with service-principal auth; not human GIS login", "status": "not-verified"}
    try:
        head = private_command(["git", "rev-parse", "HEAD"])
        order_id, target_date = require_context(os.environ, head)
        require(not private_command(["git", "diff", "--name-only", "HEAD"]), "tracked-source-dirty")
        token = os.environ.get("LIVE_ID_TOKEN", "")
        check_token(token, os.environ["GOOGLE_OAUTH_CLIENT_ID"])
        result["sourceSHA"] = head
        result["sources"] = deployed_sources(head)
        quoted = quote(order_id, safe="")
        endpoints = {"order": f"/api/orders/{quoted}", "workflow": f"/api/orders/{quoted}/workflow-v2",
                     "sheet": f"/api/orders/{quoted}/workflow-v2/sheet"}
        raw: dict[str, object] = {}
        result["http"] = {}
        for name, path in endpoints.items():
            status, body, digest = get(path, token)
            result["http"][name] = {"status": status, "bodySHA256": digest}
            raw[name] = body
        require(all(result["http"][name]["status"] == 200 for name in ("order", "workflow", "sheet")), "canonical-read-failed")
        order = sanitize_order(raw["order"])
        workflow = sanitize_workflow(raw["workflow"])
        sheet = sanitize_sheet(raw["sheet"])
        facility = str(order.get("facility") or "").strip()
        require(bool(facility), "order-facility-missing")
        context_path = "/api/orders/daily-output-context?date=" + quote(target_date) + "&facility=" + quote(facility)
        bags_path = "/api/orders/daily-bags?date=" + quote(target_date) + "&facility=" + quote(facility)
        totals_path = "/api/totals?date=" + quote(target_date) + "&include_order_refs=true"
        for name, path in (("dailyOutputContext", context_path), ("dailyBags", bags_path), ("totals", totals_path)):
            status, body, digest = get(path, token)
            result["http"][name] = {"status": status, "bodySHA256": digest}
            raw[name] = body
            require(status == 200, "output-read-failed-" + name)
        context = sanitize_context(raw["dailyOutputContext"])
        result["business"] = {"order": order, "workflow": workflow, "sheet": sheet, "dailyOutputContext": context,
                              "dailyBags": sanitize_quantity_rows(raw["dailyBags"]), "totals": sanitize_totals(raw["totals"])}
        result["case"] = extract_case(order, workflow, sheet, context, target_date)
        if result["case"].get("kind") != "captured":
            result["code"] = result["case"].get("reason")
        else:
            run_browser(token, order_id, target_date, OUTPUT)
            result["status"] = "captured"
    except Blocked as error:
        result["code"] = str(error)
    except Exception:
        result["code"] = "verification-transport-or-schema-error-details-withheld"
    finally:
        result["ownedBrowserAndProcessesStopped"] = True
        write_json(OUTPUT / "result.json", result)
        files = [path for path in OUTPUT.iterdir() if path.is_file() and path.name != "manifest.json"]
        write_json(OUTPUT / "manifest.json", {"sourceSHA": result.get("sourceSHA"), "status": result["status"],
                                                "files": [{"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in sorted(files)]})
    return 0 if result["status"] == "captured" else 1


if __name__ == "__main__":
    raise SystemExit(main())
