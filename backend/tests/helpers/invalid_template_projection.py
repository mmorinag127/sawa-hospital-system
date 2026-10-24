"""Explicit invalid projection for refusal tests, never an authored selection."""
from copy import deepcopy
from uuid import uuid4

from src.models.facility_template_version import FacilityTemplateVersion


def add_invalid_projection(session, source_id):
    source = session.get(FacilityTemplateVersion, source_id)
    assert source is not None
    values = {column.name: deepcopy(getattr(source, column.name))
              for column in FacilityTemplateVersion.__table__.columns}
    values.update(id="FTV-invalid-component-" + uuid4().hex, status="invalid",
                  version="invalid-component", activated_at=None)
    # This is deliberately invalid metadata, not a second valid source digest.
    values["template_digest"] = "invalid-component:" + values["id"]
    invalid = FacilityTemplateVersion(**values)
    session.add(invalid)
    session.flush()
    assert invalid.facility_id == source.facility_id
    assert invalid.template_digest != source.template_digest
    assert invalid.status == "invalid"
    return invalid.id
