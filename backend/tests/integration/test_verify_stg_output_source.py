"""Pure safety tests for the Actions-only, GET-only output-source verifier."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

import verify_stg_output_source as verifier


SHA = "a" * 40
CONTEXT = {
    "GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": "refs/heads/develop",
    "GITHUB_REF_NAME": "develop", "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_SHA": SHA,
    "PROJECT_ID": "sawahospitalsystem", "REGION": "asia-northeast2", "WEB_SERVICE": "web-stg",
    "WORKER_SERVICE": "worker-stg", "WEB_URL": verifier.WEB, "WORKER_URL": verifier.WORKER,
    "LIVE_SERVICE_ACCOUNT": verifier.SA_EMAIL, "GOOGLE_OAUTH_CLIENT_ID": "123-stg.apps.googleusercontent.com",
    "PROD_GOOGLE_OAUTH_CLIENT_ID": "123-prod.apps.googleusercontent.com", "OUTPUT_SOURCE_VERIFY_ORDER_ID": "ORD123abc",
    "OUTPUT_SOURCE_VERIFY_DATE": "2026-10-05",
}


@pytest.mark.parametrize("change", [{"OUTPUT_SOURCE_VERIFY_ORDER_ID": ""}, {"OUTPUT_SOURCE_VERIFY_ORDER_ID": "bad"}, {"OUTPUT_SOURCE_VERIFY_DATE": ""}, {"GITHUB_EVENT_NAME": "push"}])
def test_context_requires_explicit_dispatch_order_and_develop(change):
    with pytest.raises(verifier.Blocked):
        verifier.require_context({**CONTEXT, **change}, SHA)


def test_context_accepts_complete_explicit_order_and_date_pair():
    assert verifier.require_context(CONTEXT, SHA) == ("ORD123abc", "2026-10-05")


def test_sanitizers_exclude_tokens_urls_and_unrequested_fields():
    order = verifier.sanitize_order({"id": "ORD1", "facility": "FAC1", "signed_url": "secret", "lines": [{"date": "2026-10-05", "quantity_original": 3, "token": "secret"}]})
    assert order == {"id": "ORD1", "facility": "FAC1", "status": None, "week": None,
                     "lines": [{"date": "2026-10-05", "daypart": None, "menu_name": None, "area_id": None,
                                "quantity_original": 3, "quantity_corrected": None}]}
    totals = verifier.sanitize_totals({"rows": [{"quantity": 3, "signed_url": "secret", "order_refs": [{"order_id": "ORD1", "quantity": 3, "token": "secret"}]}]})
    assert totals == {"date_from": None, "date_to": None, "rows": [{"date": None, "daypart": None, "menu_category": None, "menu_name": None, "diet_type": None, "quantity": 3, "order_refs": [{"order_id": "ORD1", "facility_id": None, "source_diet_type": None, "aggregated_diet_type": None, "area_id": None, "quantity": 3}]}]}


def test_case_is_captured_only_after_explicit_date_and_exact_saved_sheet_lineage():
    order = {"id": "ORD1", "facility": "FAC1"}
    workflow = {"saved_sheet_id": "ODS1", "order_id": "ORD1", "template_version_id": "FTV1", "week_start": "2026-10-05", "week_end": "2026-10-11"}
    sheet = {"saved_sheet_id": "ODS1", "order_id": "ORD1", "template_version_id": "FTV1", "sheet": {"fields": ["date_mmdd", "qty.regular"], "rows": [["10/05", 3]]}}
    fulfilled = {"sections": {"daily_bags": {"status": "fulfilled"}, "totals": {"status": "fulfilled"}}}
    blocked = {"sections": {"daily_bags": {"status": "rejected"}, "totals": {"status": "fulfilled"}}}
    captured = verifier.extract_case(order, workflow, sheet, fulfilled, "2026-10-05")
    assert captured["kind"] == "captured"
    assert "parent quantity comparison" in captured["note"]
    assert verifier.extract_case(order, workflow, sheet, blocked, "2026-10-05")["kind"] == "not-verified"
    assert verifier.extract_case(order, workflow, {**sheet, "template_version_id": "FTV0"}, fulfilled, "2026-10-05") == {"kind": "not-verified", "reason": "canonical-saved-sheet-lineage-mismatch"}
    assert verifier.extract_case(order, {**workflow, "week_end": "2026-10-04"}, sheet, fulfilled, "2026-10-05")["reason"] == "workflow-week-does-not-contain-target-date"


@pytest.mark.parametrize("path", [
    "/api/orders/ORD123abc",
    "/api/orders/ORD123abc/workflow-v2",
    "/api/orders/ORD123abc/workflow-v2/sheet",
    "/api/orders/daily-output-context?date=2026-10-05&facility=FAC1",
    "/api/orders/daily-bags?date=2026-10-05&facility=FAC1",
    "/api/totals?date=2026-10-05&include_order_refs=true",
])
def test_get_allows_only_current_get_routes(path, monkeypatch):
    monkeypatch.setattr(verifier, "read_http", lambda _url, _token: (200, b"{}"))
    assert verifier.get(path, "token")[0] == 200


@pytest.mark.parametrize("path", [
    "/api/orders?date=2026-10-05",
    "/api/orders/ORD123abc/draft-sheet",
    "/api/orders/ORD123abc/workflow-state",
    "/api/orders/daily-bags?date=2026-10-05",
    "/api/orders/daily-output-context?date=2026-10-05&date=2026-10-06&facility=FAC1",
    "/api/totals?date=2026-10-05&include_order_refs=false",
])
def test_get_rejects_noncanonical_or_incomplete_paths(path):
    with pytest.raises(verifier.Blocked):
        verifier.get(path, "token")


def test_verifier_uses_current_read_routes_and_get_only_browser_guard():
    script = (ROOT / "scripts" / "verify_stg_output_source.py").read_text()
    browser = (ROOT / "frontend" / "scripts" / "verify-output-source-live.mjs").read_text()
    assert "/workflow-v2/sheet" in script
    assert "/workflow-state" not in script
    assert "/draft-sheet" not in script
    assert "allowedMethods = new Set(['GET', 'HEAD'])" in browser
    assert "await expect(dateInput).toHaveValue(targetDate)" in browser
    assert "route.abort('blockedbyclient')" in browser
    assert browser.index("assert.equal(isAllowedOutputSourceOrigin(origin), true)") < browser.index("webkit.launch")
    assert "if (location.origin !== sessionOrigin) return;" in browser


def test_entrypoint_resolves_repo_packages_without_cwd_or_pythonpath_and_stops_before_auth(tmp_path):
    output = tmp_path / "output-source-live"
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    env.pop("GITHUB_ACTIONS", None)
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "verify_stg_output_source.py"), "--output", str(output)],
        cwd=Path("/private/tmp"),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 1
    result = json.loads((output / "result.json").read_text())
    manifest = json.loads((output / "manifest.json").read_text())
    assert result["status"] == "not-verified"
    assert result["code"] == "context-invalid-GITHUB_ACTIONS"
    assert "sourceSHA" not in result
    assert manifest["status"] == "not-verified"
    assert [item["name"] for item in manifest["files"]] == ["result.json"]
