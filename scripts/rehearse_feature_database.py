"""Exercise release migrations and seed import only on an owned Unix-socket PG."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import sys

from rehearse_hospital_schema import owned_postgres, preservation, fingerprints, sequences




def run(feature, backend, source, *, platform_root=None, originals=None, source_sha=None):
    if os.environ.get("GITHUB_ACTIONS") or os.environ.get("CI"):
        raise RuntimeError("local rehearsal only")
    if feature == "school" and (platform_root is None or originals is None or not re.fullmatch(r"[a-f0-9]{40}", source_sha or "")):
        raise ValueError("school rehearsal requires platform source and original metadata")
    with owned_postgres() as (engine, directory):
        uri = engine.url.render_as_string(hide_password=False)
        env = {k:v for k,v in os.environ.items() if not k.startswith(("PG", "DB_", "DATABASE_", "ALEMBIC_"))}
        env.update(DATABASE_URL=uri, PYTHONPATH=str(backend))
        if feature == "shift":
            receipt = json.loads((source / "source.json").read_text())
            dump = source / "production.dump"
            with dump.open("rb") as f:
                if (receipt["instance"] != "sawahospitalsystem:asia-northeast2:shift-prod"
                        or receipt["production_read_only"] is not True or receipt["database"] != "shift_scheduler"
                        or hashlib.file_digest(f, "sha256").hexdigest() != receipt["dump_sha256"]):
                    raise ValueError("invalid shift snapshot")
            subprocess.run([str(Path(os.environ["OWNED_POSTGRES_PG_BIN"]) / "pg_restore"),
                "--exit-on-error", "--single-transaction", "--no-owner", "--no-acl", "--dbname=postgres", str(dump)],
                env=dict(env, PGHOST=str(directory / "socket"), PGPORT="55495", PGUSER="adoption_test"), check=True)
            with engine.connect() as conn:
                cols = preservation.columns(conn)
                if fingerprints(conn, cols) != receipt["tables"]:
                    raise ValueError("shift restore differs from source")
                sequence_before = sequences(conn)
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=backend, env=env, check=True)
        if feature == "school":
            sys.path.insert(0, str(platform_root / "scripts"))
            from school_seed_import import load_seed
            load_seed(engine, source)
            load_seed(engine, source, verify_existing=True)
            from school_seed_assets import prepare
            with engine.connect() as conn:
                jobs = conn.exec_driver_sql("SELECT id,facility_id,status,stored_pdf_ref FROM implementation_price_ocr_jobs ORDER BY id").all()
                assets = prepare(jobs, json.loads(originals.read_text()), source)
            (source / "assets.json").write_text(json.dumps(assets, indent=2) + "\n")
            manifest = json.loads((source / "candidate.json").read_text())
            manifest.update(source_sha=source_sha, assets=assets)
            (source / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        with engine.connect() as conn:
            if feature == "shift" and fingerprints(conn, cols) != receipt["tables"]:
                raise ValueError("shift migration changed original data")
            if feature == "shift":
                sequence_after = sequences(conn)
                if any(sequence_after.get(name) != value for name, value in sequence_before.items()):
                    raise ValueError("shift migration changed an existing sequence")
            head = conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
            expected = {"shift":"a8c31e4f902d", "school":"0032_route_source_references"}[feature]
            if head != expected:
                raise ValueError("unexpected migrated revision")
        print(json.dumps({"feature":feature, "head":head, "isolated_restore":True}))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("feature", choices=["shift", "school"])
    p.add_argument("backend", type=Path)
    p.add_argument("source", type=Path)
    p.add_argument("--platform-root", type=Path)
    p.add_argument("--originals", type=Path)
    p.add_argument("--source-sha")
    args = p.parse_args()
    os.umask(0o077)
    run(args.feature, args.backend.resolve(), args.source.resolve(),
        platform_root=args.platform_root.resolve() if args.platform_root else None,
        originals=args.originals.resolve() if args.originals else None, source_sha=args.source_sha)
