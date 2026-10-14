"""Offline migration preflight; never changes JSON, bindings, evidence or approvals."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from src.services import canonical_facility_store as store
from src.services.canonical_facility_lineage import digest
from src.services.sheet_week_service import parse_sheet_week_value


def unique_rows(rows, key):
    if not isinstance(rows, list):
        raise ValueError("migration_inventory_rows_required")
    result = {}
    for row in rows:
        identity = row.get(key) if isinstance(row, dict) else None
        if not isinstance(identity, str) or not identity or identity in result:
            raise ValueError("migration_inventory_identity_invalid")
        result[identity] = row
    return result


def check_history(inventory, master, current_configs, *, inventory_sha256):
    if inventory.get("read_only") is not True or not isinstance(inventory.get("source"), dict):
        raise ValueError("readonly_migration_inventory_required")
    source = inventory["source"]
    facilities = unique_rows(source.get("facilities"), "id")
    legacy = unique_rows(source.get("facility_template_versions"), "id")
    orders = unique_rows(source.get("active_order_bindings"), "order_id")
    if current_configs.get("inventory_sha256") != inventory_sha256:
        raise ValueError("migration_current_capture_source_mismatch")
    current = unique_rows(current_configs.get("facilities"), "facility_id")
    snapshot = store.MasterSnapshot(master, "offline-preflight", "explicit_file")
    reports = []
    for order_id, order in sorted(orders.items()):
        report = {"order_id": order_id, "facility_id": order.get("facility_id"),
                  "week_code": order.get("week_code"), "legacy_version_id": order.get("template_version_id")}
        try:
            facility_id = order.get("facility_id")
            if facility_id not in facilities:
                raise ValueError("migration_order_facility_missing")
            previous = legacy.get(order.get("template_version_id"))
            if previous is None or previous.get("facility_id") != facility_id:
                raise ValueError("migration_legacy_version_unresolved")
            _, start, end = parse_sheet_week_value(order.get("week_code"))
            if start is None or end is None:
                raise ValueError("migration_order_period_required")
            first = store.resolve_facility_version(snapshot, facility_id, start)["version"]
            last = store.resolve_facility_version(snapshot, facility_id, end)["version"]
            if first["version_id"] != last["version_id"]:
                raise ValueError("migration_order_spans_facility_versions")
            captured = current.get(facility_id)
            if not isinstance(captured, dict) or not isinstance(captured.get("prepared_facility"), dict):
                raise ValueError("migration_current_capture_facility_missing")
            if captured["prepared_facility"].get("facility_id") != facility_id:
                raise ValueError("migration_current_capture_facility_mismatch")
            # Old FTV cell/column serialization is not Hakodate's processing input.
            if digest(first["facility"]) != digest(captured["prepared_facility"]):
                raise ValueError("migration_current_configuration_changed")
            report.update(status="ready_for_explicit_reselection", authored_version_id=first["version_id"],
                          authored_version_digest=digest(first),
                          legacy_config_object_recorded=isinstance(previous.get("config_json"), dict))
        except store.CanonicalStoreError as exc:
            report.update(status="blocked", error=exc.detail)
        except ValueError as exc:
            report.update(status="blocked", error={"error": str(exc)})
        reports.append(report)
    blocked = sum(row["status"] == "blocked" for row in reports)
    return {"scope": "Period/template preflight only. Not historical full-config parity, approval, DB migration or OCR verification.",
            "orders": reports, "order_count": len(reports), "blocked_count": blocked,
            "ready_for_explicit_reselection_count": len(reports) - blocked,
            "automatic_rebinding_performed": False}


def read_checked(path, expected):
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("migration_input_sha256_mismatch")
    return store._decode(raw)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--inventory-sha256", required=True)
    parser.add_argument("--master", type=Path, required=True)
    parser.add_argument("--master-sha256", required=True)
    parser.add_argument("--current-configs", type=Path, required=True)
    parser.add_argument("--current-configs-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if len({path.resolve() for path in (args.inventory, args.master, args.current_configs, args.output)}) != 4:
            raise ValueError("migration_paths_must_be_distinct")
        if args.output.exists():
            raise FileExistsError("migration_output_already_exists")
        result = check_history(read_checked(args.inventory, args.inventory_sha256),
                               read_checked(args.master, args.master_sha256),
                               read_checked(args.current_configs, args.current_configs_sha256),
                               inventory_sha256=args.inventory_sha256)
        result["inputs"] = {"inventory": str(args.inventory.resolve()), "inventory_sha256": args.inventory_sha256,
                            "master": str(args.master.resolve()), "master_sha256": args.master_sha256,
                            "current_configs": str(args.current_configs.resolve()),
                            "current_configs_sha256": args.current_configs_sha256}
        with args.output.open("x") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2, allow_nan=False)
    except (ValueError, OSError, store.CanonicalStoreError) as exc:
        parser.exit(2, str(exc) + "\n")
    print(json.dumps({key: result[key] for key in ("order_count", "blocked_count", "ready_for_explicit_reselection_count")}))
    return 1 if result["blocked_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
