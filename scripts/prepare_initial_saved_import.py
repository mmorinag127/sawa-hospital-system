"""Offline preparation from explicitly captured saved-source JSON; never opens a DB.

Operational order: apply the formal backend/alembic.ini migration chain to its
current head, then capture saved sources read-only, then run this offline tool.
Migration 0029 (backend/migrations/0029_order_workflow_state_revision.py) adds
order_workflow_states.revision. A pre-migration SELECT failure is a schema
prerequisite failure, not missing historical data or a request for past dates.
This tool neither migrates the database nor supplies absent revision values.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def prepare(initial_path, snapshots_path, policy_path, *, hashes):
    from src.services.initial_saved_import_service import prepare_import_master
    values = []
    sources = {}
    for key, path in (("initial", initial_path), ("snapshots", snapshots_path), ("policy", policy_path)):
        raw = path.read_bytes()
        actual = hashlib.sha256(raw).hexdigest()
        if hashes[key] != actual:
            raise ValueError("initial_saved_import_input_hash_mismatch:" + key)
        values.append(json.loads(raw))
        sources[key] = {"path": str(path.resolve()), "sha256": actual}
    initial, snapshots, policy = values
    if not isinstance(snapshots, list):
        raise ValueError("initial_saved_import_captured_snapshots_array_required")
    result = prepare_import_master(initial, snapshots, policy)
    raw = (json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
    return raw, {"sources": sources, "output_sha256": hashlib.sha256(raw).hexdigest(),
                 "orders": len(snapshots), "scope": "Explicit initial saved import; no DB access or OCR/approval creation",
                 "capture_prerequisite": "Formal Alembic current head, including 0029 workflow revision, before read-only capture",
                 "verification_limit": "Captured JSON preparation is not proof of live reader success or execution parity"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("initial", "snapshots", "policy"):
        parser.add_argument("--" + key, type=Path, required=True)
        parser.add_argument("--" + key + "-sha256", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    import subprocess
    actual_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if args.source_sha != actual_sha:
        raise ValueError("initial_saved_import_source_sha_mismatch")
    inputs = {args.initial.resolve(), args.snapshots.resolve(), args.policy.resolve()}
    if args.output.resolve() in inputs or args.report.resolve() in inputs or args.output.resolve() == args.report.resolve():
        raise ValueError("initial_saved_import_source_overwrite_forbidden")
    if args.output.exists() or args.report.exists():
        raise ValueError("initial_saved_import_output_exists")
    raw, report = prepare(args.initial, args.snapshots, args.policy,
                          hashes={key: getattr(args, key + "_sha256") for key in ("initial", "snapshots", "policy")})
    report["source_sha"] = actual_sha
    report["implementation_sha256"] = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in (
        "scripts/prepare_initial_saved_import.py", "backend/src/services/initial_saved_import_service.py",
        "backend/src/services/initial_saved_source_service.py")}
    with args.output.open("xb") as stream:
        stream.write(raw)
    with args.report.open("x") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
