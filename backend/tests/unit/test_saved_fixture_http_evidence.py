"""Pre-publication GET evidence preserves differences and never sends writes."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest
from test_prepare_stg_saved_import import inputs, staged_run


def script():
    root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(root / "scripts"))
    spec = importlib.util.spec_from_file_location("fixture_http_capture", root / "scripts/prepare_stg_saved_import.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_reads_three_surfaces_for_fixture_and_normal_and_records_mismatch(monkeypatch):
    module = script()
    import staging_db_target
    sheet = {"fields": ["quantity"], "rows": [["4"]], "source": "codex_stg_verification"}
    snapshots = [{"order": {"id": identity}, "draft": {"draft_sheet_json": sheet}, "evidence": evidence}
                 for identity, evidence in (("FIX", None), ("OCR", {"id": "E"}), ("OTHER", {"id": "E2"}))]
    calls = []
    raw = json.dumps({"sheet": {**sheet, "rows": [["5"]]}, "request_id": "unchanged-raw"}).encode()
    def read(url, token):
        assert token == "private-token"
        calls.append(url)
        return (409, b"not JSON") if url.endswith("/workflow-v2") else (200, raw)
    monkeypatch.setattr(staging_db_target, "read_http", read)
    result = module.read_http_evidence(snapshots, "private-token", "source-sha")
    assert len(calls) == 6
    assert [item["order_id"] for item in result["orders"]] == ["FIX", "OCR"]
    assert "private-token" not in json.dumps(result)
    for order in result["orders"]:
        assert order["saved_sheet"] == sheet
        responses = order["responses"]
        assert responses[0]["raw_sha256"] == hashlib.sha256(raw).hexdigest()
        assert responses[0]["sheets"][0]["fields_equal"] is True
        assert responses[0]["sheets"][0]["rows_equal"] is False
        assert responses[0]["sheets"][0]["whole_sheet_equal"] is False
        assert responses[1]["status"] == 409
        assert responses[1]["sheets"] == []


def test_requires_token_without_emitting_it():
    with pytest.raises(ValueError, match="token_required"):
        script().read_http_evidence([], "", "source-sha")


def test_http_evidence_is_retained_in_existing_receipt(inputs, staged_run, monkeypatch, capsys):
    import prepare_stg_saved_import as transport
    inputs.capture_http_evidence = True
    monkeypatch.setenv("LIVE_ID_TOKEN", "private-token")
    evidence = {"orders": [{"order_id": "ORD1", "responses": [{"status": 409}]}]}
    monkeypatch.setattr(transport, "read_http_evidence", lambda *args: evidence)
    transport.run(inputs)
    raw = capsys.readouterr().out
    assert "private-token" not in raw
    receipt = json.loads(raw)
    assert receipt["http_evidence"] == evidence
    assert receipt["database_modified"] is False
    assert receipt["sha256"] == hashlib.sha256(inputs.output.read_bytes()).hexdigest()
