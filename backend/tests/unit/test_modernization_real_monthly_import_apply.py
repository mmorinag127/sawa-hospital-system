"""Synthetic XLSX -> actual upload/review/CAS/apply/readback HTTP.

Owned SQLite with 0030/0031 migrations, AUTH_DISABLED, no lifespan. Reused
archive binding only: parser, receipt, master resolution and apply are real.
Not user originals, full-schema PG, authentication or cloud/live proof.
"""

from copy import deepcopy
from hashlib import sha256

import pytest

from test_monthly_menu_saved_import import store as store
from test_modernization_real_monthly_import_download import imported as imported, snapshot


def test_real_upload_review_apply_and_readback_preserve_source_and_exact_scope(imported):
    client, engine, receipt, content, archive, _root = imported
    original = deepcopy(receipt)
    path = f"/monthly-menus/2026-10/imports/{receipt['id']}"
    names = ["Synthetic breakfast", "Synthetic lunch", "Synthetic dinner"]
    # Explicit synthetic count-unit masters, matching the existing real-XLSX
    # contract. Zero is authored, not inferred from missing quantities.
    resolutions = [{"source_name": name, "action": "create", "unit_type": "count",
                    "qty_per_serving": 0, "bag_max_qty": 20, "bag_max_unit": "count"}
                   for name in names]
    response = client.post(path + "/review", headers={"If-Match": receipt["revision"]}, json={
        "resolutions": resolutions, "input_revision": receipt["current_input_revision"],
    })
    assert response.status_code == 200, response.text
    reviewed = response.json()
    assert reviewed["review"]["issues"] == []
    assert reviewed["review"]["resolutions"] == resolutions
    assert reviewed["source"] == receipt["source"]
    assert reviewed["revision"] != receipt["revision"]
    before = snapshot(engine)
    stale = client.post(path + "/apply", headers={"If-Match": receipt["revision"]})
    assert stale.status_code == 409, stale.text
    assert snapshot(engine) == before
    applied_response = client.post(path + "/apply", headers={"If-Match": reviewed["revision"]})
    assert applied_response.status_code == 200, applied_response.text
    applied = applied_response.json()
    assert applied["created"] is True and applied["item_count"] == 3
    assert applied["receipt"]["state"] == "applied"
    assert applied["receipt"]["source"] == receipt["source"]
    items, entries = applied["snapshot"]["items"], applied["snapshot"]["entries"]
    assert len(items) == 3 and len(entries) == 21
    assert {item["name"] for item in items} == set(names)
    assert all(item["qty_per_serving"] == 0 and item["unit_type"] == "count" for item in items)
    assert {entry["menu_date"] for entry in entries} == {f"2026-10-{day:02d}" for day in range(4, 11)}
    assert {entry["diet_type"] for entry in entries} == {"regular"}
    assert {entry["daypart"] for entry in entries} == {"朝食", "昼食", "夕食"}
    assert all(entry["slot_index"] == 0 for entry in entries)
    after = snapshot(engine)
    assert after != before  # Intended application, not a read-only operation.
    read = client.get(path)
    assert read.status_code == 200, read.text
    assert read.json() == applied["receipt"]
    repeated = client.post(path + "/apply", headers={"If-Match": reviewed["revision"]})
    assert repeated.status_code == 409
    assert repeated.json() == {"detail": "import already applied; do not reapply"}
    download = client.get(path + "/download")
    assert download.status_code == 200 and download.content == content
    assert snapshot(engine) == after
    assert archive.read_bytes() == content
    assert sha256(content).hexdigest() == original["source"]["content_sha256"]
    assert receipt == original


@pytest.mark.parametrize("fault", ["foreign-month", "missing-input-revision"])
def test_real_review_rejects_foreign_or_missing_authority_without_write(imported, fault):
    client, engine, receipt, content, archive, _root = imported
    month = "2026-11" if fault == "foreign-month" else "2026-10"
    body = {"resolutions": []}
    if fault == "foreign-month":
        body["input_revision"] = receipt["current_input_revision"]
    before = snapshot(engine)
    response = client.post(f"/monthly-menus/{month}/imports/{receipt['id']}/review",
                           headers={"If-Match": receipt["revision"]}, json=body)
    assert response.status_code == (404 if fault == "foreign-month" else 428), response.text
    assert snapshot(engine) == before
    assert archive.read_bytes() == content
