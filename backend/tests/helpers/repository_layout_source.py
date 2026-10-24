"""Opt-in repository layout specimens for non-live parser/workbook components."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from src.services.template_field_schema_service import canonical_field_name_from_template_column


@pytest.fixture
def repository_layout_source(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[2] / "src/data/facility_master.template.json"
    original = source.read_bytes()
    master = json.loads(original)
    for facility in master["facilities"]:
        facility["fax_template"] = deepcopy(facility["fax_template_override"])
        facility["fax_template"]["template_id"] = facility["fax_template_id"]
        for column in facility["fax_template"]["columns"]:
            column["name"] = canonical_field_name_from_template_column(column)
    path = tmp_path / "repository-layout-specimens.json"
    path.write_text(json.dumps(master, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("FACILITY_MASTER_PATH", str(path))
    monkeypatch.delenv("FACILITY_MASTER_URI", raising=False)
    monkeypatch.delenv("K_SERVICE", raising=False)
    yield master
    assert source.read_bytes() == original
