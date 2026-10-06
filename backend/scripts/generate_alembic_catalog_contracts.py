"""Deduplicate observed catalogs; never infer schemas from ORM metadata."""

import argparse
import hashlib
import json
from pathlib import Path

from src.maintenance.formal_migration_schema import table_shapes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = {"format": 1, "sources": {}, "shapes": {}, "revisions": {}}
    for dialect in ("sqlite", "postgresql"):
        result["revisions"][dialect] = {}
        for number in range(1, 29):
            revision = f"{number:04d}"
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
            result["revisions"][dialect][revision] = {"tables": refs, "views": snapshot["views"]}
    args.output.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
    print(f"shapes={len(result['shapes'])} bytes={args.output.stat().st_size}")


if __name__ == "__main__":
    main()
