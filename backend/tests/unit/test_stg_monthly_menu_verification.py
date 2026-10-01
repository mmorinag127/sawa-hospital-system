import importlib.util
import hashlib
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "verify_stg_monthly_menus", Path(__file__).resolve().parents[3] / "scripts/verify_stg_monthly_menus.py",
)
verification = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verification)


def payload(quantity=1, diffs=None):
    return {"items": [{"id": "item", "name": "Fish", "menu_master_id": "master",
                       "unit_type": "cut", "qty_per_serving": quantity}],
            "master_checks": {"issues": [] if diffs is None else [{"item_id": "item", "field_diffs": diffs}]}}


MASTER = {"master": {"unit_type": "cut", "qty_per_serving": 1}}


def test_verification_checks_values_and_target_coverage():
    assert verification.validate(payload(), MASTER, {"Fish": ["cut", 1]})["checked_existing_items"] == 1
    with pytest.raises(AssertionError):
        verification.validate(payload(100), MASTER, {"Fish": ["cut", 1]})
    with pytest.raises(AssertionError):
        verification.validate(payload(), MASTER, {"Missing": ["cut", 1]})


def test_verification_rejects_quantity_diff_but_keeps_other_warnings():
    with pytest.raises(AssertionError):
        verification.validate(payload(diffs=[{"field": "qty_per_serving"}]), MASTER, {"Fish": ["cut", 1]})
    assert verification.validate(payload(diffs=[{"field": "category"}]), MASTER,
                                 {"Fish": ["cut", 1]})["remaining_issues"] == 1


def test_production_fixture_uri_is_not_read_by_staging_job():
    with pytest.raises(ValueError):
        verification.download("gs://sawahospitalsystem-prod-raw/file.xlsm")


def test_only_exact_explicit_test_snapshot_can_be_replaced():
    snapshot = payload()
    digest = hashlib.sha256(verification.canonical_snapshot(snapshot).encode()).hexdigest()
    verification.verify_replace_snapshot(snapshot, digest)
    with pytest.raises(RuntimeError):
        verification.verify_replace_snapshot(snapshot, None)
    with pytest.raises(RuntimeError):
        verification.verify_replace_snapshot(payload(2), digest)


def test_snapshot_ignores_item_order_but_preserves_all_values():
    snapshot = {"items": [{"id": "b", "quantity": 1}, {"id": "a", "quantity": 2}]}
    reordered = {"items": list(reversed(snapshot["items"]))}
    assert verification.canonical_snapshot(snapshot) == verification.canonical_snapshot(reordered)
    changed = {"items": [{"id": "a", "quantity": 2}, {"id": "b", "quantity": 3}]}
    assert verification.canonical_snapshot(snapshot) != verification.canonical_snapshot(changed)
