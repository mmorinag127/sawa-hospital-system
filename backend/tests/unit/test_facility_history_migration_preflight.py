from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from tests.unit.test_canonical_facility_lineage import authored

PATH = Path(__file__).resolve().parents[3] / "scripts/check_facility_history_migration.py"
spec = importlib.util.spec_from_file_location("history_migration", PATH)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def inputs():
    version = authored()
    master = {"facilities": [{"facility_id": "F", "versions": [version]}]}
    inventory = {"read_only": True, "facilities": [{"facility_id": "F"}], "source": {
        "facilities": [{"id": "F"}],
        "facility_template_versions": [{"id": "old", "facility_id": "F", "valid_from": None,
            "template_id": "human-template", "columns_json": deepcopy(version["facility"]["fax_template"]["columns"]),
            "cells_json": None, "config_json": None}],
        "active_order_bindings": [{"order_id": "order", "facility_id": "F", "template_version_id": "old",
                                   "week_code": "2026-01@2026-01-12~2026-01-18"}],
    }}
    return inventory, master


def test_preflight_neither_rebinds_nor_claims_historical_full_config_parity():
    inventory, master = inputs()
    before = deepcopy((inventory, master))
    report = migration.check_history(inventory, master)
    assert report["ready_for_explicit_reselection_count"] == 1
    assert report["automatic_rebinding_performed"] is False
    assert report["orders"][0]["historical_full_config_available"] is False
    assert report["orders"][0]["authored_version_id"] == "human-A"
    assert (inventory, master) == before


@pytest.mark.parametrize("change,error", [
    ("no_period", "facility_version_date_unrecorded"),
    ("overlap", "facility_version_period_overlap"),
    ("no_legacy", "migration_legacy_version_unresolved"),
    ("foreign_legacy", "migration_legacy_version_unresolved"),
    ("columns", "migration_template_columns_changed"),
    ("cells", "migration_template_cells_changed"),
    ("identity", "migration_template_identity_changed"),
    ("month_only", "migration_order_period_required"),
])
def test_missing_and_changed_sources_block_without_guessing(change, error):
    inventory, master = inputs()
    source = inventory["source"]
    versions = master["facilities"][0]["versions"]
    if change == "no_period":
        versions.clear()
    elif change == "overlap":
        versions.append(authored("other"))
    elif change == "no_legacy":
        source["facility_template_versions"].clear()
    elif change == "foreign_legacy":
        source["facility_template_versions"][0]["facility_id"] = "other"
    elif change in ("columns", "cells", "identity"):
        source["facility_template_versions"][0][{"columns": "columns_json", "cells": "cells_json", "identity": "template_id"}[change]] = [] if change != "identity" else "other"
    elif change == "month_only":
        source["active_order_bindings"][0]["week_code"] = "2026-01"
    before = deepcopy((inventory, master))
    report = migration.check_history(inventory, master)
    assert report["ready_for_explicit_reselection_count"] == 0
    assert report["orders"][0]["error"]["error"] == error
    assert (inventory, master) == before


def test_before_and_after_month_faxes_resolve_separately_not_from_week_start():
    inventory, master = inputs()
    master["facilities"][0]["versions"] = [authored("September", "2026-09-01", "2026-09-30"),
                                           authored("October", "2026-10-01", "2026-10-31")]
    original = inventory["source"]["active_order_bindings"][0]
    inventory["source"]["active_order_bindings"] = [
        {**original, "order_id": "before", "week_code": "2026-09@2026-09-27~2026-09-30"},
        {**original, "order_id": "after", "week_code": "2026-10@2026-10-01~2026-10-03"},
        {**original, "order_id": "crossing", "week_code": "2026-09@2026-09-27~2026-10-03"},
    ]
    rows = {row["order_id"]: row for row in migration.check_history(inventory, master)["orders"]}
    assert rows["before"]["authored_version_id"] == "September"
    assert rows["after"]["authored_version_id"] == "October"
    assert rows["crossing"]["error"]["error"] == "migration_order_spans_facility_versions"


def test_duplicate_legacy_identity_is_not_resolved_by_first_candidate():
    inventory, master = inputs()
    inventory["source"]["facility_template_versions"] *= 2
    with pytest.raises(ValueError, match="migration_inventory_identity_invalid"):
        migration.check_history(inventory, master)


def test_cli_hash_and_exclusive_output_preserve_inputs(tmp_path):
    inventory, master = inputs()
    paths = [tmp_path / name for name in ("inventory.json", "master.json", "report.json")]
    for path, value in zip(paths, (inventory, master)):
        path.write_text(json.dumps(value))
    hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths[:2]]
    args = ["--inventory", str(paths[0]), "--inventory-sha256", hashes[0], "--master", str(paths[1]),
            "--master-sha256", hashes[1], "--output", str(paths[2])]
    assert migration.main(args) == 0
    saved = paths[2].read_bytes()
    with pytest.raises(SystemExit) as duplicate:
        migration.main(args)
    assert duplicate.value.code == 2 and paths[2].read_bytes() == saved
    paths[2].unlink()
    args[3] = "changed"
    with pytest.raises(SystemExit) as changed:
        migration.main(args)
    assert changed.value.code == 2 and not paths[2].exists()
    assert [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths[:2]] == hashes
