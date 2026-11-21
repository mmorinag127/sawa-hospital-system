import base64
import hashlib

import httpx
import pytest
from fastapi.testclient import TestClient

from experiments.agent_assistant.app import create_app
from experiments.agent_assistant.fax_preview import read_overlay
from experiments.agent_assistant.store import Conflict


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScL+WQAAAABJRU5ErkJggg==")
URI = "ocr-artifacts/artifacts/OCR-OA-test/overlay.png"
ORIGINAL = hashlib.sha256(b"%PDF-original").hexdigest()


def fixture(root):
    path = root / URI
    path.parent.mkdir(parents=True)
    path.write_bytes(PNG)
    item = {
        "fax_id": "UPLA",
        "snapshot": {"order_id": "OA", "document_id": "DOC1", "sha256": ORIGINAL},
        "review": {"id": "review1", "sheet": {"base_evidence_run_id": "OEV1"},
                   "original": {"sha256": ORIGINAL}},
    }
    source = {"order_id": "OA", "document_id": "DOC1", "uploaded_pdf_id": "UPLA", "sha256": ORIGINAL}
    receipt = {"uri": URI, "sha256": hashlib.sha256(PNG).hexdigest(),
               "size_bytes": len(PNG), "content_type": "image/png"}
    packet = {"order_id": "OA", "source_receipt": {"selected_pdf_receipt": source},
              "outputs": {"overlay_png": receipt}}
    return item, {"id": "OEV1", "order_id": "OA", "artifact_manifest_json": {"artifact_packet": packet}}, path


@pytest.mark.parametrize("change", [None, "document", "fax", "pdf_hash", "evidence", "missing", "outside", "symlink", "changed"])
def test_overlay_never_falls_back_to_unrelated_image(tmp_path, change):
    root = tmp_path / "copy"
    item, evidence, path = fixture(root)
    packet = evidence["artifact_manifest_json"]["artifact_packet"]
    source = packet["source_receipt"]["selected_pdf_receipt"]
    if change in ("document", "fax", "pdf_hash"):
        source[{"document": "document_id", "fax": "uploaded_pdf_id", "pdf_hash": "sha256"}[change]] = "other"
    elif change == "evidence":
        evidence["id"] = "OEV2"
    elif change == "missing":
        packet["outputs"] = {}
    elif change == "outside":
        packet["outputs"]["overlay_png"]["uri"] = "ocr-artifacts/artifacts/../../other.png"
    elif change == "symlink":
        outside = tmp_path / "other.png"
        outside.write_bytes(PNG)
        path.unlink()
        path.symlink_to(outside)
    elif change == "changed":
        path.write_bytes(PNG[:-1] + b"x")
    if change is None:
        assert read_overlay(root, item, evidence) == PNG
    else:
        with pytest.raises(Conflict):
            read_overlay(root, item, evidence)


def test_preview_route_is_scoped_to_the_current_review(tmp_path, monkeypatch):
    import pymupdf
    class Client:
        async def close(self):
            pass

    app = create_app({}, tmp_path / "trial", managed_client=Client())
    store = app.state.store
    store.set_budget(100)
    run, _ = store.create("local-trial-operator", "fax-preview", "FAX確認", ["hospital"], 30,
                          task_id="hospital_pending_faxes")
    store.update(run["id"], status="running")
    root = app.state.sandbox.path(run["id"]) / "data" / "hospital"
    item, evidence, _ = fixture(root)
    from experiments.agent_assistant.tools import Tools
    tools = Tools(store, app.state.sandbox)
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((72, 72), "Source page one")
        pdf.new_page().insert_text((72, 72), "Source page two")
        source = pdf.tobytes()
    original = tools.artifact(run["id"], source, "source.pdf")
    item["review"]["original"] = original
    item["snapshot"]["sha256"] = original["sha256"]
    evidence["artifact_manifest_json"]["artifact_packet"]["source_receipt"]["selected_pdf_receipt"]["sha256"] = original["sha256"]
    with store.tx() as db:
        db.execute("INSERT INTO fax_items(run_id,fax_id,snapshot,state) VALUES(?,?,?,'awaiting_approval')",
                   (run["id"], item["fax_id"], "{}"))
    tools.faxes.set(run["id"], item["fax_id"], snapshot=item["snapshot"], review=item["review"])
    calls = []

    async def fetch(run, domain, method, path, **kwargs):
        calls.append((domain, method, path))
        return httpx.Response(200, json=evidence)

    monkeypatch.setattr(app.state.sandbox, "request", fetch)
    url = f'/runs/{run["id"]}/faxes/UPLA/preview/'
    with TestClient(app) as web:
        assert web.get(url + "review1").status_code == 401
        web.get("/")
        assert web.get(url + "stale").status_code == 409
        result = web.get(url + "review1")
        assert result.status_code == 200
        assert result.headers["content-type"] == "image/png"
        assert result.headers["cache-control"] == "private, no-store"
        assert result.content == PNG
        first = web.get(url + "review1?page=1")
        second = web.get(url + "review1?page=2")
        assert first.status_code == second.status_code == 200
        assert first.headers["x-fax-page-count"] == second.headers["x-fax-page-count"] == "2"
        assert first.content.startswith(b"\x89PNG") and first.content != second.content
        assert "content-disposition" not in first.headers
        assert web.get(url + "review1?page=3").status_code == 409
        assert web.get(f'/runs/{run["id"]}/artifacts/{original["id"]}').content == source
        assert calls == [("hospital", "GET", "/orders/OA/workflow-v2/ocr-results/OEV1")]
        assert tools.faxes.item(run["id"], "UPLA")["review"] == item["review"]
