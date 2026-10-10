"""Real XLSX parser/archive/save -> mounted monthly-import download receipts.

Explicit synthetic menus, owned SQLite with reused 0030/0031 migrations,
AUTH_DISABLED and no app lifespan. Not user FAX/monthly documents, production
auth, formal full-schema PG, cloud durability or live proof. Only archive output
location is bound; no parser, receipt, reader or auth success stubs are used.
Archive tampering below is a restored component fault, not an operator action.
"""

from copy import deepcopy
from datetime import date
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from urllib.parse import quote

from fastapi.testclient import TestClient
from openpyxl import Workbook
import pytest
import sqlalchemy as sa

from test_monthly_menu_saved_import import store as store
from src.services import menu_service as service
from src.services import menu_upload_archive_service as archive


MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
FILENAME = "synthetic-monthly-menu.xlsx"


def synthetic_workbook(month):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Authored menu"
    sheet.append([f"2026年{month}月", "献立種類: 常食"])
    sheet.append(["区分", "日", "月", "火", "水", "木", "金", "土"])
    first = 4 if month == 10 else 1  # Explicit October/November 2026 Sunday frames.
    sheet.append([None, *range(first, first + 7)])
    for meal, name in (("朝食", "Synthetic breakfast"), ("昼食", "Synthetic lunch"), ("夕食", "Synthetic dinner")):
        sheet.append([meal, *([name] * 7)])
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()
    return stream.getvalue()


def snapshot(engine):
    metadata = sa.MetaData()
    metadata.reflect(engine)
    with engine.connect() as connection:
        return {table.name: deepcopy([dict(row) for row in connection.execute(
            sa.select(table).order_by(*table.primary_key.columns),
        ).mappings()]) for table in metadata.sorted_tables}


@pytest.fixture
def imported(store, monkeypatch, tmp_path):
    _scope, engine = store
    monkeypatch.setenv("AUTH_DISABLED", "true")
    monkeypatch.delenv("RAW_BUCKET", raising=False)
    monkeypatch.delenv("K_SERVICE", raising=False)
    archive_root = tmp_path / "archive"
    monkeypatch.setattr(archive, "_local_storage_base", lambda: archive_root)
    from src.main import app

    # Actual src.main prefix; no private convenience router/dependency overrides.
    with_client = TestClient(app)
    try:
        content = synthetic_workbook(10)
        parsed_month, parsed_diet, items, entries = service._parse_monthly_menu(
            content, FILENAME, "Authored menu", "2026-10",
        )
        assert parsed_month == date(2026, 10, 1)
        assert parsed_diet == "regular"
        assert len(items) == 3 and len(entries) == 21
        assert {entry["menu_date"] for entry in entries} == {date(2026, 10, day) for day in range(4, 11)}
        assert {entry["daypart"] for entry in entries} == {"朝食", "昼食", "夕食"}
        assert {entry["diet_type"] for entry in entries} == {"regular"}
        receipt = upload(with_client, "2026-10", content)
        source = receipt["source"]
        assert receipt["state"] == "review"
        assert source["scope_override"] is None  # Explicit base/all-facility source.
        assert source["diet_types"] == ["regular"]
        assert source["filename"] == FILENAME
        assert source["sheet_name"] == "Authored menu"
        assert source["media_type"] == MIME
        assert source["content_sha256"] == sha256(content).hexdigest()
        assert receipt["review"]["item_count"] == 3
        path = Path(source["file_uri"])
        assert path.is_relative_to(archive_root)
        assert path.read_bytes() == content
        yield with_client, engine, receipt, content, path, tmp_path
    finally:
        with_client.close()


def upload(client, month, content):
    response = client.post("/monthly-menus", params={
        "month_id": month, "sheet_name": "Authored menu", "scope_type": "base",
        "diet_types": json.dumps(["regular"]),
    }, files={"file": (FILENAME, content, MIME)})
    assert response.status_code == 200, response.text
    return response.json()


def test_real_multipart_saved_import_download_preserves_original_bytes_and_database(imported, capsys):
    client, engine, receipt, content, path, root = imported
    before = snapshot(engine)
    receipt_before = deepcopy(receipt)
    response = client.get(f"/monthly-menus/2026-10/imports/{receipt['id']}/download")
    assert response.status_code == 200, response.text
    assert response.content == content
    assert int(response.headers["content-length"]) == len(content)
    assert response.headers["content-type"] == MIME
    assert response.headers["content-disposition"] == (
        f'attachment; filename="{FILENAME}"; filename*=UTF-8\'\'{quote(FILENAME, safe="")}'
    )
    assert sha256(response.content).hexdigest() == receipt["source"]["content_sha256"]
    received = root / "http-received" / FILENAME
    received.parent.mkdir()
    received.write_bytes(response.content)
    assert received.read_bytes() == content
    with capsys.disabled():
        print(f"http-received artifact={received} sha256={sha256(response.content).hexdigest()}")
    assert snapshot(engine) == before
    assert receipt == receipt_before
    assert path.read_bytes() == content


def test_foreign_month_cannot_download_saved_import_or_substitute_its_own_receipt(imported):
    client, engine, receipt, content, path, _root = imported
    other = upload(client, "2026-11", synthetic_workbook(11))
    other_path = Path(other["source"]["file_uri"])
    other_bytes = other_path.read_bytes()
    before = snapshot(engine)
    response = client.get(f"/monthly-menus/2026-11/imports/{receipt['id']}/download")
    assert response.status_code == 404
    assert response.json() == {"detail": "saved menu import not found for this month"}
    assert snapshot(engine) == before
    assert path.read_bytes() == content
    assert other_path.read_bytes() == other_bytes


def test_archive_digest_tampering_explicitly_stops_download_without_database_changes(imported):
    client, engine, receipt, content, path, _root = imported
    try:
        changed = content + b"explicit-component-digest-fault"
        path.write_bytes(changed)
        before = snapshot(engine)
        response = client.get(f"/monthly-menus/2026-10/imports/{receipt['id']}/download")
        assert response.status_code == 400
        assert response.json() == {"detail": "saved source archive digest mismatch"}
        assert snapshot(engine) == before
        assert path.read_bytes() == changed
    finally:
        path.write_bytes(content)
    assert path.read_bytes() == content
