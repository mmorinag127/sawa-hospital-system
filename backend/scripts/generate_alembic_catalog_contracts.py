"""Deduplicate observed catalogs; never infer schemas from ORM metadata."""

import argparse
import hashlib
import json
from pathlib import Path

from src.maintenance.formal_migration_schema import formal_revision_chain, table_shapes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-dir", type=Path, required=True)
    parser.add_argument("--base-contract", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--through-revision")
    args = parser.parse_args()
    revisions = formal_revision_chain()
    if args.through_revision is not None:
        if args.through_revision not in revisions:
            raise RuntimeError("catalog blocked: through revision is not in the source chain")
        revisions = revisions[:revisions.index(args.through_revision) + 1]
    if args.output.exists():
        raise RuntimeError("catalog blocked: generated contract output already exists")
    base = json.loads(args.base_contract.read_text())
    result = {"format": 1, "sources": {}, "shapes": {}, "revisions": {}}
    for dialect in ("sqlite", "postgresql"):
        result["revisions"][dialect] = {}
        for revision in revisions:
            path = args.snapshot_dir / f"{dialect}-{revision}.json"
            raw = path.read_bytes()
            snapshot = json.loads(raw)
            result["sources"][path.name] = hashlib.sha256(raw).hexdigest()
            refs = {}
            for table, shape in table_shapes(snapshot, dialect).items():
                encoded = json.dumps(shape, sort_keys=True, separators=(",", ":")).encode()
                reference = hashlib.sha256(encoded).hexdigest()
                result["shapes"][reference] = shape
                refs[table] = reference
            actual = {"tables": refs, "views": snapshot["views"]}
            expected = base.get("revisions", {}).get(dialect, {}).get(revision)
            if expected is not None:
                if expected["views"] != actual["views"] or set(expected["tables"]) != set(actual["tables"]):
                    raise RuntimeError(f"catalog blocked: historical {dialect} {revision} table/view drift")
                for table, expected_ref in expected["tables"].items():
                    if base["shapes"][expected_ref] != result["shapes"][actual["tables"][table]]:
                        raise RuntimeError(f"catalog blocked: historical {dialect} {revision} {table} drift")
            result["revisions"][dialect][revision] = actual
    args.output.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
    print(f"shapes={len(result['shapes'])} bytes={args.output.stat().st_size}")


if __name__ == "__main__":
    main()
