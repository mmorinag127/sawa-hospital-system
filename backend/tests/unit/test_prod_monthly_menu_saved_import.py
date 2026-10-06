import copy
import hashlib
import importlib
import json
from pathlib import Path
import sys

import pytest


@pytest.fixture
def importer(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "scripts"))
    return importlib.import_module("import_prod_menu_upload_to_stg")


def test_prod_upload_passes_actual_sheet_and_scope_as_api_query(importer, monkeypatch):
    calls = []
    monkeypatch.setattr(importer.requests, "post", lambda *args, **kwargs: calls.append((args, kwargs)))
    importer._upload_to_stg(stg_base_url="https://fixture.invalid", bearer_token="fixture-auth",
        target_month_id="2026-10", filename="original.xlsm", sheet_name="recorded-sheet",
        file_bytes=b"original", media_type="application/vnd.ms-excel.sheet.macroEnabled.12", scope_override="FAC-A", diet_types=["soft", "mixer"])
    _, request = calls[0]
    assert request["params"] == {"month_id": "2026-10", "sheet_name": "recorded-sheet", "scope_type": "facility", "scope_value": "FAC-A", "diet_types": json.dumps(["soft", "mixer"])}
    assert request["files"]["file"][0:2] == ("original.xlsm", b"original")
    assert "data" not in request


def test_actual_historical_digest_or_explicit_original_required(importer):
    raw = b"verified-original"
    digest = hashlib.sha256(raw).hexdigest()
    assert importer._verified_original_digest(raw, {"content_sha256": digest}, None) == digest
    assert importer._verified_original_digest(raw, {}, digest) == digest
    for metadata, explicit in (({}, None), ({"content_sha256": "a" * 64}, digest),
                               ({"content_sha256": digest}, "wrong"), ({"content_sha256": digest[:24]}, None)):
        with pytest.raises(ValueError):
            importer._verified_original_digest(raw, metadata, explicit)


@pytest.mark.parametrize("missing", ["scope_override", "diet_types"])
def test_prod_recorded_selection_missing_never_becomes_all_or_base(importer, missing):
    metadata = {"scope_override": None, "diet_types": None}
    del metadata[missing]
    with pytest.raises(RuntimeError, match="absence is not null"):
        importer.authored_selection(metadata)


def test_prod_upload_authored_null_is_preserved_without_query_supplement(importer, monkeypatch):
    calls = []
    monkeypatch.setattr(importer.requests, "post", lambda *args, **kwargs: calls.append(kwargs))
    importer._upload_to_stg(stg_base_url="https://fixture.invalid", bearer_token="fixture-auth", target_month_id="2026-10",
        filename="original.xlsm", sheet_name="recorded-sheet", file_bytes=b"original", media_type="application/vnd.ms-excel.sheet.macroEnabled.12",
        scope_override=None, diet_types=None)
    assert calls[0]["params"] == {"month_id": "2026-10", "sheet_name": "recorded-sheet"}


@pytest.mark.parametrize("mode", ["save", "apply", "stale", "unresolved", "unrecorded_scope", "unrecorded_diets"])
def test_actual_main_never_reports_saved_receipt_as_applied(importer, monkeypatch, tmp_path, capsys, mode):
    raw = b"verified-original"
    digest = hashlib.sha256(raw).hexdigest()
    items = [{"name": "Fish" if index == 0 else f"Fish {index}", "daypart": ("朝食", "昼食", "夕食")[index // 4],
              "category": "主菜", "diet_type": "regular", "unit_type": "cut", "temp_type": None,
              "qty_per_serving": 1, "_inferred_master_fields": []} for index in range(12)]
    review = {"issues": [], "master_diffs": [], "resolutions": [], "item_count": 12,
              "plans": {item["name"]: {"action": "existing", "menu_master_id": f"master-{index}",
                  "seed_fields": {key: item[key] for key in ("unit_type", "temp_type", "qty_per_serving", "daypart", "category")}}
                  for index, item in enumerate(items)}, "items": items,
              "entries": [{"name": item["name"], "menu_date": "2026-10-01", "daypart": item["daypart"],
                  "category": item["category"], "diet_type": item["diet_type"], "slot_index": index % 4}
                  for index, item in enumerate(items)], "scope_override": "FAC-A",
              "input_snapshot": {"revision": 3, "digest": "b" * 64}}
    receipt = {"id": "saved-A", "month_id": "2026-10", "state": "review", "revision": "read-receipt",
        "input_revision": "read-input", "current_input_revision": "read-input", "review": review, "current_review": review,
        "source": {"filename": "original.xlsm", "content_sha256": digest, "file_uri": "gs://fixture/source.xlsm",
                   "scope_override": "FAC-A", "sheet_name": "recorded-sheet", "diet_types": None,
                   "media_type": "application/vnd.ms-excel.sheet.macroEnabled.12"}}
    approval = {**copy.deepcopy(importer.review_material(receipt)), "resolutions": []}
    from types import SimpleNamespace
    from src.services import menu_service
    menu_service._validate_import_receipt(SimpleNamespace(id=receipt["id"], month_id=receipt["month_id"], state=receipt["state"],
        revision=3, source_json=receipt["source"], review_json=receipt["review"]))
    assert len(review["items"]) == len(review["entries"]) == review["item_count"] == 12
    if mode == "stale":
        approval["input_revision"] = "old-input"
    approval_file = tmp_path / "explicit-review.json"
    approval_file.write_text(json.dumps(approval))
    argv = ["import_prod_menu_upload_to_stg.py", "--source-month-id", "2026-10", "--upload-id", "exact-log-A",
            "--target-month-id", "2026-10", "--stg-base-url", "https://fixture.invalid", "--bearer-token", "fixture-auth"]
    if mode != "save":
        argv += ["--saved-import-id", "saved-A", "--review-file", str(approval_file)]
    monkeypatch.setattr(sys, "argv", argv)
    class ClosedSource:
        def cursor(self):
            return self
        def close(self):
            pass
    class Connector:
        def close(self):
            pass
    monkeypatch.setattr(importer, "Connector", Connector)
    monkeypatch.setattr(importer.storage, "Client", lambda **kwargs: object())
    monkeypatch.setattr(importer, "_load_secret", lambda *_args: "fixture-secret")
    monkeypatch.setattr(importer, "_connect", lambda *_args: ClosedSource())
    def fetch(_cursor, *, source_month_id, upload_id):
        assert (source_month_id, upload_id) == ("2026-10", "exact-log-A")
        metadata = {"file_uri": "gs://fixture/original.xlsm", "filename": "original.xlsm",
                    "content_sha256": digest, "scope_override": "FAC-A", "diet_types": None, "sheet_name": "recorded-sheet"}
        if mode == "unrecorded_scope":
            del metadata["scope_override"]
        if mode == "unrecorded_diets":
            del metadata["diet_types"]
        return {"metadata": metadata}
    monkeypatch.setattr(importer, "_fetch_prod_upload", fetch)
    source_reads = []
    def source_bytes(*args):
        source_reads.append(args)
        return raw, "application/vnd.ms-excel.sheet.macroEnabled.12"
    monkeypatch.setattr(importer, "_read_gcs_bytes", source_bytes)
    class Response:
        status_code = 200
        headers = {"content-type": "application/json"}
        def __init__(self, body):
            self.body = body
        def json(self):
            return copy.deepcopy(self.body)
        def raise_for_status(self):
            pass
    monkeypatch.setattr(importer, "_upload_to_stg", lambda **kwargs: Response(receipt))
    def get(url, **kwargs):
        assert url == "https://fixture.invalid/monthly-menus/2026-10/imports/saved-A"
        return Response(receipt)
    monkeypatch.setattr(importer.requests, "get", get)
    calls = []
    def post(url, *, data, headers, timeout):
        calls.append(url)
        if url.endswith("/review"):
            assert headers["If-Match"] == "read-receipt"
            assert json.loads(data) == {"resolutions": [], "input_revision": "read-input"}
            own = copy.deepcopy(receipt)
            own["revision"] = "own-review"
            if mode == "unresolved":
                own["current_review"] = copy.deepcopy(own["review"])
                own["current_review"]["issues"] = [{"source_name": "Fish", "normalized_name": "fish", "issue_key": "fish", "reason": "missing",
                    "suggested_patch": {"name": "Fish", "unit_type": "cut", "qty_per_serving": 1, "temp_type": None,
                        "daypart": "朝食", "category": "主菜", "bag_max_qty": None, "bag_max_unit": None, "condiments": []}, "candidates": []}]
                del own["current_review"]["plans"]["Fish"]
            return Response(own)
        assert headers["If-Match"] == "own-review" and data == b""
        return Response({"created": True, "item_count": 12, "receipt": {**receipt, "state": "applied"},
                         "snapshot": {"source_month_id": "2026-10", "menu": {"id": "2026-10"},
                                      "items": [], "entries": [], "revision": "own-month"}})
    monkeypatch.setattr(importer.requests, "post", post)
    if mode in {"unrecorded_scope", "unrecorded_diets"}:
        with pytest.raises(RuntimeError, match="absence is not null"):
            importer.main()
        assert calls == [] and source_reads == []
        assert capsys.readouterr().out == ""
        return
    if mode == "save":
        with pytest.raises(SystemExit, match="Original saved, not applied"):
            importer.main()
        assert calls == []
    elif mode in {"stale", "unresolved"}:
        with pytest.raises(RuntimeError):
            importer.main()
        assert len(calls) == (1 if mode == "unresolved" else 0)
    else:
        assert importer.main() == 0
        assert len(calls) == 2
    output = capsys.readouterr().out
    assert '"saved_import_id": "saved-A"' in output
    assert ('"applied": true' in output) is (mode == "apply")
