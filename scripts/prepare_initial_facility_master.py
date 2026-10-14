"""Initialize current settings explicitly; never rebind or rewrite existing orders."""
import argparse
from copy import deepcopy
import hashlib
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from src.services import canonical_facility_store as store
from scripts.check_facility_history_migration import read_checked, unique_rows


def prepare_initial_master(master, capture, inventory, *, capture_sha256, inventory_sha256):
    if inventory.get("read_only") is not True or capture.get("inventory_sha256") != inventory_sha256:
        raise ValueError("migration_current_capture_source_mismatch")
    facilities = unique_rows(inventory["source"]["facilities"], "id")
    current = unique_rows(capture.get("facilities"), "facility_id")
    entries = unique_rows(master.get("facilities"), "facility_id")
    if set(facilities) != set(current) or set(entries) != set(current):
        raise ValueError("migration_facility_inventory_mismatch")
    result = deepcopy(master)
    for entry in result["facilities"]:
        facility_id = entry["facility_id"]
        if entry.get("versions") not in (None, []):
            raise ValueError("migration_initial_versions_already_exist")
        facility = current[facility_id].get("prepared_facility")
        if not isinstance(facility, dict) or facility.get("facility_id") != facility_id:
            raise ValueError("migration_current_capture_facility_mismatch")
        entry["versions"] = [store.initial_facility_version(facility, capture_sha256)]
    store._encode(result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("master", "capture", "inventory"):
        parser.add_argument("--" + name, type=Path, required=True)
        parser.add_argument("--" + name + "-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if len({path.resolve() for path in (args.master, args.capture, args.inventory, args.output)}) != 4:
        parser.error("migration_paths_must_be_distinct")
    result = prepare_initial_master(
        read_checked(args.master, args.master_sha256), read_checked(args.capture, args.capture_sha256),
        read_checked(args.inventory, args.inventory_sha256), capture_sha256=args.capture_sha256,
        inventory_sha256=args.inventory_sha256,
    )
    data = store._encode(result)
    with args.output.open("xb") as stream:
        stream.write(data)
    print("initial_facilities=" + str(len(result["facilities"])) + " sha256=" + hashlib.sha256(data).hexdigest())


if __name__ == "__main__":
    main()
