"""Generator keeps historical evidence and uses the runtime's exact comparison policy."""
import copy
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from src.maintenance.formal_migration_schema import table_shapes

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("drift", [False, True])
def test_append_contract_preserves_history_and_rejects_real_drift(tmp_path, monkeypatch, drift):
    spec = importlib.util.spec_from_file_location("portal_catalog_generator", ROOT / "scripts/generate_alembic_catalog_contracts.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "formal_revision_chain", lambda: ("0034", "0035", "0036"))
    base = {"format": 1, "sources": {"historical-proof": "unchanged"}, "shapes": {}, "revisions": {}}
    for dialect in ("sqlite", "postgresql"):
        snapshot = {"tables": {"example": {"columns": [], "indexes": []}}, "native": [], "views": {}}
        if dialect == "postgresql":
            snapshot["tables"]["example"]["indexes"] = [{"name": "example_index", "include_columns": [], "dialect_options": {"postgresql_include": []}}]
        shape = table_shapes(snapshot, dialect)["example"]
        historical = copy.deepcopy(shape)
        if dialect == "postgresql":
            historical["indexes"][0]["include_columns"] = []
        base["shapes"][dialect] = historical
        base["revisions"][dialect] = {}
        for revision in ("0034", "0035"):
            entry = {"tables": {"example": dialect}, "views": {}}
            if revision == "0035":
                entry["column_identity"] = "name" if dialect == "postgresql" else "position"
            base["revisions"][dialect][revision] = entry
            observed = copy.deepcopy(snapshot)
            if drift and revision == "0034" and dialect == "postgresql":
                observed["tables"]["example"]["indexes"][0]["name"] = "different_index"
            (tmp_path / f"{dialect}-{revision}.json").write_text(json.dumps(observed))
        snapshot["tables"]["portal_sessions"] = {"columns": [], "indexes": []}
        (tmp_path / f"{dialect}-0036.json").write_text(json.dumps(snapshot))
    base_path, output = tmp_path / "base.json", tmp_path / "result.json"
    base_path.write_text(json.dumps(base))
    monkeypatch.setattr(sys, "argv", ["generator", "--snapshot-dir", str(tmp_path), "--base-contract", str(base_path), "--output", str(output)])
    if drift:
        with pytest.raises(RuntimeError, match="historical postgresql 0034 example drift"):
            module.main()
        assert not output.exists()
        return
    module.main()
    actual = json.loads(output.read_text())
    def encoded(value):
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()

    for key, value in base["sources"].items():
        assert encoded(actual["sources"][key]) == encoded(value)
    for key, shape in base["shapes"].items():
        assert encoded(actual["shapes"][key]) == encoded(shape)
    for dialect in ("sqlite", "postgresql"):
        assert set(actual["revisions"][dialect]) - set(base["revisions"][dialect]) == {"0036"}
        for revision, entry in base["revisions"][dialect].items():
            assert encoded(actual["revisions"][dialect][revision]) == encoded(entry)
        new = actual["revisions"][dialect]["0036"]
        assert "portal_sessions" in new["tables"]
        assert new["column_identity"] == base["revisions"][dialect]["0035"]["column_identity"]
