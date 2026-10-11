"""Registered local identity -> actual HTTP downloads with owned SQL receipts.

Only Google verification is doubled. Synthetic saved OCR is inherited from the
owned fixture; this is not native OCR, startup/lifespan, PG or live proof.
"""

from contextlib import contextmanager
from copy import deepcopy
import csv
from datetime import date
from io import BytesIO, StringIO
import os
import re
from zipfile import ZipFile

import pytest
from sqlalchemy import event, text

from tests.contract import test_portal_google_auth as registered_users
from tests.contract.auth_support import HUMAN_AUDIENCE, registered_hospital_operator
from test_modernization_real_bagging_confirmation import owned_bagging as owned_bagging
from test_modernization_workflow_surface_parity import surface as surface
from test_modernization_real_delivery_bundle import (
    MIME, bundle_order as bundle_order, label_rows, receive, workbook_rows,
)
from test_modernization_real_daily_delivery_html import preservation, assert_preserved
from src.api import auth
from src.main import app
from src.services import output_builder


CASES = ["labels-csv", "delivery-xlsx", "bundle-labels", "bundle-delivery", "bundle-both"]


def request_target(fixture, candidate, case):
    day = date.fromisoformat(str(candidate["lines"][0]["date"])[:10])
    if case.startswith("bundle-"):
        return "/outputs/daily-bundle", {"date": day.isoformat(), "bundle_type": case[7:]}
    if case == "labels-csv":
        return "/outputs/labels", {"order_id": fixture.order_id, "date": day.isoformat()}
    assert case == "delivery-xlsx"
    return "/outputs/delivery-notes", {"order_id": fixture.order_id}


@contextmanager
def readonly_request(fixture):
    before = preservation(fixture)
    with fixture.engine.connect() as connection:
        grants = list(connection.execute(text(
            "SELECT user_id, system_key, enabled FROM user_system_access ORDER BY user_id, system_key"
        )).tuples())
    writes = []
    statements = []

    def observe(_connection, _cursor, statement, _parameters, context, _many):
        statements.append(statement)
        if (context.isinsert or context.isupdate or context.isdelete or context.isddl
                or re.match(r"\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP|TRUNCATE)\b",
                            statement, re.IGNORECASE)):
            writes.append(statement)

    event.listen(fixture.engine, "before_cursor_execute", observe)
    try:
        yield
    finally:
        event.remove(fixture.engine, "before_cursor_execute", observe)
    assert statements, "Request must exercise the owned SQL identity/receipt boundary"
    assert writes == []
    assert_preserved(fixture, before)
    with fixture.engine.connect() as connection:
        assert list(connection.execute(text(
            "SELECT user_id, system_key, enabled FROM user_system_access ORDER BY user_id, system_key"
        )).tuples()) == grants


@pytest.fixture
def authenticated_output(bundle_order, monkeypatch):
    fixture, client, candidate = bundle_order
    assert client.app is app
    assert not app.dependency_overrides
    # Bind the existing real registration helper to the fixture's owned DB,
    # not the process bootstrap DB. Auth session_scope already uses this DB.
    monkeypatch.setattr(registered_users, "engine", fixture.engine)
    monkeypatch.setattr(output_builder, "OUTPUT_DIR", fixture.output)
    return fixture, client, candidate


@pytest.mark.parametrize("case", CASES)
def test_registered_operator_download_preserves_owned_receipts_without_sql_writes(
    authenticated_output, monkeypatch, capsys, case,
):
    fixture, client, candidate = authenticated_output
    candidate_before = deepcopy(candidate)
    with registered_hospital_operator(monkeypatch) as operator:
        assert os.environ["AUTH_DISABLED"] == "false"
        auth.invalidate_user_cache()
        path, params = request_target(fixture, candidate, case)
        with readonly_request(fixture):
            response = client.get(path, params=params, headers=operator.headers)
        operator.verifier.assert_called_once()
        zipped = case == "bundle-labels"
        mime = "text/csv; charset=ms932" if case == "labels-csv" else "application/zip" if zipped else MIME
        extension = "csv" if case == "labels-csv" else "zip" if zipped else "xlsx"
        body = receive(response, fixture, f"authenticated-{case}.{extension}", mime, capsys)
        if case == "labels-csv":
            rows = list(csv.reader(StringIO(body.decode("cp932"))))
            label_rows([tuple(row) for row in rows], zero=True, empty_header="")
        elif zipped:
            with ZipFile(BytesIO(body)) as archive:
                assert archive.testzip() is None
                assert len(archive.namelist()) == 1
                sheets = workbook_rows(archive.read(archive.namelist()[0]))
            assert len(sheets) == 1
            label_rows(next(iter(sheets.values())), zero=True)
        else:
            sheets = workbook_rows(body)
            facility = candidate["configured_input"]["facility"]["facility_name"]
            if case == "bundle-both":
                assert set(sheets) == {"ラベル_" + facility, "納品書_" + facility}
                label_rows(sheets["ラベル_" + facility], zero=False)
                delivery = sheets["納品書_" + facility]
            else:
                assert len(sheets) == 1
                delivery = next(iter(sheets.values()))
            assert delivery[0] == ("Date", "Menu", "Quantity")
            assert {row[1]: row[2] for row in delivery[1:]} == {"選択献立0": 7}
        assert candidate == candidate_before
        assert [row[4] for row in fixture.saved()["rows"]] == ["7", "", "", "0"]


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("refusal", ["absent", "invalid-token", "inactive", "missing-grant", "wrong-system", "disabled-grant"])
def test_download_requires_real_registered_active_hospital_grant(
    authenticated_output, monkeypatch, case, refusal,
):
    fixture, client, candidate = authenticated_output
    systems = () if refusal == "missing-grant" else ("shift",) if refusal == "wrong-system" else ("hospital",)
    with registered_hospital_operator(
        monkeypatch, status="inactive" if refusal == "inactive" else "active", systems=systems,
    ) as operator:
        assert os.environ["AUTH_DISABLED"] == "false"
        if refusal == "disabled-grant":
            with fixture.session_scope() as session:
                session.execute(text("UPDATE user_system_access SET enabled=FALSE WHERE user_id=:id"),
                                {"id": operator.user_id})
        auth.invalidate_user_cache()
        before = preservation(fixture)
        files = {path: path.read_bytes() for path in fixture.output.rglob("*") if path.is_file()}
        path, params = request_target(fixture, candidate, case)
        headers = {} if refusal == "absent" else {"Authorization": "Bearer invalid-google-token"} if refusal == "invalid-token" else operator.headers
        # Missing/invalid tokens stop before SQL; registered refusals must read
        # the actual users/grants and are also observed for DML/DDL.
        if refusal in {"absent", "invalid-token"}:
            statements = []

            def observe(_connection, _cursor, statement, _parameters, _context, _many):
                statements.append(statement)

            event.listen(fixture.engine, "before_cursor_execute", observe)
            try:
                response = client.get(path, params=params, headers=headers)
            finally:
                event.remove(fixture.engine, "before_cursor_execute", observe)
            assert statements == []
        else:
            with readonly_request(fixture):
                response = client.get(path, params=params, headers=headers)
        assert response.status_code == (401 if refusal in {"absent", "invalid-token"} else 403)
        assert response.json() == {"detail": "Unauthorized" if refusal in {"absent", "invalid-token"}
                                   else "Forbidden" if refusal == "inactive" else "System access denied"}
        if refusal == "absent":
            operator.verifier.assert_not_called()
        elif refusal == "invalid-token":
            url = str(response.request.url)
            assert [(call.args[0], call.args[2]) for call in operator.verifier.call_args_list] == [
                ("invalid-google-token", HUMAN_AUDIENCE),
                ("invalid-google-token", url),
                ("invalid-google-token", url.rstrip("/")),
            ]
        else:
            operator.verifier.assert_called_once()
        assert {path: path.read_bytes() for path in fixture.output.rglob("*") if path.is_file()} == files
        assert_preserved(fixture, before)
