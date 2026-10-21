"""Deduplicate observed catalogs; never infer schemas from ORM metadata."""

import argparse
import copy
import hashlib
import json
from pathlib import Path

from src.maintenance.formal_migration_schema import (
    formal_revision_chain, table_shapes, column_identity_policy,
    _normalize_reflected_index_aliases, _named_columns,
)


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
    result = copy.deepcopy(base)
    for dialect in ("sqlite", "postgresql"):
        result["revisions"].setdefault(dialect, {})
        for index, revision in enumerate(revisions):
            path = args.snapshot_dir / f"{dialect}-{revision}.json"
            raw = path.read_bytes()
            snapshot = json.loads(raw)
            refs = {}
            observed = table_shapes(snapshot, dialect)
            for table, shape in observed.items():
                encoded = json.dumps(shape, sort_keys=True, separators=(",", ":")).encode()
                reference = hashlib.sha256(encoded).hexdigest()
                refs[table] = reference
            actual = {"tables": refs, "views": snapshot["views"]}
            expected = base.get("revisions", {}).get(dialect, {}).get(revision)
            if expected is not None:
                policy = column_identity_policy(expected, dialect, revision)
                if expected["views"] != actual["views"] or set(expected["tables"]) != set(actual["tables"]):
                    raise RuntimeError(f"catalog blocked: historical {dialect} {revision} table/view drift")
                for table, expected_ref in expected["tables"].items():
                    prior = _normalize_reflected_index_aliases(base["shapes"][expected_ref], dialect)
                    current = observed[table]
                    if policy == "name":
                        prior, current = _named_columns(prior), _named_columns(current)
                    if prior != current:
                        raise RuntimeError(f"catalog blocked: historical {dialect} {revision} {table} drift")
                continue
            if int(revision) >= 35:
                previous = result["revisions"][dialect][revisions[index - 1]]
                actual["column_identity"] = column_identity_policy(previous, dialect, revisions[index - 1])
                column_identity_policy(actual, dialect, revision)
            result["sources"][path.name] = hashlib.sha256(raw).hexdigest()
            for table, shape in observed.items():
                result["shapes"].setdefault(refs[table], shape)
            result["revisions"][dialect][revision] = actual
    args.output.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
    print(f"shapes={len(result['shapes'])} bytes={args.output.stat().st_size}")


if __name__ == "__main__":
    main()
