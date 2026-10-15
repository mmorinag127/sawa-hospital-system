from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from src.services import order_service as service
from src.services import facility_template_version_service as templates
from src.services import facility_master_service
from tests.unit.test_canonical_facility_lineage import Session, selected, install, authored


@pytest.fixture
def saved(monkeypatch):
    selection, _ = selected(monkeypatch)
    session = Session()
    _, projection = templates.materialize_facility_version_lineage(session, selection)
    order = SimpleNamespace(id="O", facility_code="F", template_version_id=projection.id,
                            week_code="2026-01@2026-01-15~2026-01-21")
    session.get = lambda model, identity: order if identity == "O" else None
    session.statements.clear()
    monkeypatch.setattr(service, "session_scope", lambda: nullcontext(session))
    monkeypatch.setattr(service.config_service, "load_facility_master", lambda: facility_master_service.get_snapshot().master)
    return order, session


def test_saved_exact_version_readonly(saved):
    order, session = saved
    result = service.get_order_facility_config("O")
    assert result["template_version_id"] == order.template_version_id
    assert result["week_start"] == "2026-01-15"
    assert result["week_end"] == "2026-01-21"
    assert result["source"] == "canonical_json"
    assert result["config"]["fax_template"] == authored()["facility"]["fax_template"]
    assert all(statement.is_select for statement in session.statements)
    assert not session.new and not session.dirty and not session.deleted


def test_saved_does_not_choose_newer_version(saved, monkeypatch):
    install(monkeypatch, [authored(), authored("newer", "2026-02-01", "2026-02-28")])
    assert service.get_order_facility_config("O")["template_version_id"] == saved[0].template_version_id


def test_missing_saved_week_stops(saved):
    saved[0].week_code = None
    with pytest.raises(ValueError, match="week_range_required"):
        service.get_order_facility_config("O")


@pytest.mark.parametrize("change", ["missing", "foreign", "changed_source", "removed_source", "ambiguous_source"])
def test_saved_invalid_source_never_substitutes(saved, monkeypatch, change):
    order, _ = saved
    if change == "missing":
        order.template_version_id = None
    elif change == "foreign":
        order.facility_code = "OTHER"
    elif change == "changed_source":
        record = authored()
        record["facility"]["quantity_setting"] = 9
        install(monkeypatch, [record])
    elif change == "removed_source":
        install(monkeypatch, [authored("replacement")])
    else:
        install(monkeypatch, [authored(), authored()])
    with pytest.raises(ValueError):
        service.get_order_facility_config("O")


def test_api_missing_order_and_version_are_explicit(saved):
    from fastapi import HTTPException
    from src.api.orders import read_order_facility_config
    with pytest.raises(HTTPException) as missing:
        read_order_facility_config("absent")
    assert missing.value.status_code == 404
    saved[0].template_version_id = None
    with pytest.raises(HTTPException) as version:
        read_order_facility_config("O")
    assert version.value.status_code == 409
    assert version.value.detail == {"error": "original_template_projection_missing"}
