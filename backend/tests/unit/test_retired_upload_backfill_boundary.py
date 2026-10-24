import ast
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[3]
SERVICE = "backend/src/services/uploaded_pdf_service.py"
BASE = "cfedb6ea72735bd6a2b5307699e5a9c35eb90503"
RETIREMENT = "450f22ab3ce1813bded846314affca77cc2a7b86"
REMOVED = {
    "_backfill_scan_limit", "_parse_datetime", "_looks_like_manual_upload_job",
    "_guess_content_sha256", "_map_ingest_job_state", "_link_current_entities",
}


@pytest.mark.parametrize("limit", [None, 0, 1, 20, -1, 500])
def test_retired_backfill_stops_before_any_side_effect(monkeypatch, limit):
    from src.services import uploaded_pdf_service as service

    def forbidden(*args, **kwargs):
        pytest.fail("retired backfill accessed DB/source/time or mutation")

    for name in ("session_scope", "_now", "_make_uploaded_pdf_id", "_max_attempts", "select"):
        monkeypatch.setattr(service, name, forbidden)
    with pytest.raises(ValueError, match="^original_uploaded_pdf_receipt_required$"):
        service.backfill_uploaded_pdfs_from_ingest_jobs(limit=limit)


def test_retired_backfill_default_call_stops(monkeypatch):
    from src.services import uploaded_pdf_service as service

    def forbidden(*args, **kwargs):
        pytest.fail("retired backfill opened a transaction")

    monkeypatch.setattr(service, "session_scope", forbidden)
    with pytest.raises(ValueError, match="^original_uploaded_pdf_receipt_required$"):
        service.backfill_uploaded_pdfs_from_ingest_jobs()


def test_all_nonretired_functions_remain_exact_source_and_ast():
    before = subprocess.check_output(["git", "show", f"{BASE}:{SERVICE}"], cwd=ROOT, text=True)
    after = subprocess.check_output(["git", "show", f"{RETIREMENT}:{SERVICE}"], cwd=ROOT, text=True)

    def functions(source):
        return {node.name: node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef)}

    original, current = functions(before), functions(after)
    assert REMOVED.isdisjoint(current)
    assert set(current) == set(original) - REMOVED
    for name in set(current) - {"backfill_uploaded_pdfs_from_ingest_jobs"}:
        assert ast.get_source_segment(after, current[name]) == ast.get_source_segment(before, original[name])
        assert ast.dump(current[name]) == ast.dump(original[name])
    boundary = current["backfill_uploaded_pdfs_from_ingest_jobs"]
    assert ast.dump(boundary.args) == ast.dump(original[boundary.name].args)
    assert ast.dump(boundary.returns) == ast.dump(original[boundary.name].returns)
    assert len(boundary.body) == 1
    assert ast.dump(boundary.body[0]) == ast.dump(ast.parse('raise ValueError("original_uploaded_pdf_receipt_required")').body[0])
    present = functions((ROOT / SERVICE).read_text())
    assert REMOVED.isdisjoint(present)
    assert ast.dump(present[boundary.name]) == ast.dump(boundary)
