"""Authored registration/state components; never an OCR or accepted-output proof."""
from datetime import date
from copy import deepcopy
import json
from pathlib import Path
from uuid import uuid4

import pytest

from src.services import order_service
from src.services.canonical_facility_store import read_snapshot
from src.services.ingest_facility_selection_service import capture_selection
from src.services.manual_upload_service import ManualUploadSavedFile
from src.services.uploaded_pdf_service import create_uploaded_pdf_from_upload
from tests.helpers.ingest_original import (
    authored_sources, configure_delivery, reserve_uploaded_original,
    register_authored_original,
)


@pytest.fixture
def owned_registration(monkeypatch, tmp_path, isolated_formal_schema):
    """Keep period and line values explicit; acquire the real registration lease."""
    configure_delivery(monkeypatch)

    def register(*, message_id, facility_id, received_at, week, effective_date, lines, facility_config=None):
        day = date.fromisoformat(effective_date)
        uri, digest = authored_sources(monkeypatch, tmp_path,
            facility_id=facility_id, menu_date=day, seed_menu=False, seed_month=False,
            original_name=f"authored-{uuid4().hex}.pdf", facility_config=facility_config)
        selection = capture_selection(facility_id, effective_date,
            read_snapshot().revision, "authored-original", week)
        saved = ManualUploadSavedFile(message_id=message_id, pdf_uri=uri,
            content_sha256=digest, original_filename="authored-registration.pdf",
            received_at=received_at)
        uploaded, duplicate = create_uploaded_pdf_from_upload(saved=saved,
            facility_hint=facility_id, week_hint=week, facility_name=None,
            skip_ocr=False, source_kind="manual_upload", facility_selection=selection)
        assert duplicate is False
        reserve_uploaded_original(uploaded["id"], effective_date=day)
        order = register_authored_original(uploaded["id"], effective_date=day)
        if lines:
            assert order_service.update_lines(order["id"], lines) is True
        return order_service.get_order_by_id(order["id"])

    return register


@pytest.fixture
def authored_layout_registration(owned_registration):
    """Explicit repository layout specimens, never current/live facility proof."""
    from src.services.template_field_schema_service import canonical_field_name_from_template_column

    source = Path(__file__).resolve().parents[2] / "src/data/facility_master.template.json"
    specimens = json.loads(source.read_text())["facilities"]

    def register(**inputs):
        matches = [item for item in specimens if item["facility_id"] == inputs["facility_id"]]
        assert len(matches) == 1, "explicit authored layout specimen required"
        facility = deepcopy(matches[0])
        facility.pop("versions", None)
        if "fax_template" not in facility:
            facility["fax_template"] = deepcopy(facility["fax_template_override"])
            facility["fax_template"]["template_id"] = facility["fax_template_id"]
        # authored_sources creates this component's one-page PDF.
        facility["fax_template"]["page"] = 1
        for column in facility["fax_template"]["columns"]:
            column["name"] = canonical_field_name_from_template_column(column)
        return owned_registration(**inputs, facility_config=facility)

    return register


