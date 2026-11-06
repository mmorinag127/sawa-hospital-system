"""Prepare production settings from a verified snapshot in an owned local DB only."""

import argparse
import base64
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/tests/integration")]

from live_schema_adoption_support import owned_postgres, target
from rehearse_hospital_schema import restore_owned
from hospital_snapshot_fingerprints import fingerprints
from run_stg_schema_adoption import upgrade_connection
from prepare_hakodate_facility_config import prepare_config
from src import staging_copy_schema as preservation
from src.services import canonical_facility_store as store


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def document(value):
    if not isinstance(value, dict):
        raise ValueError("embedded snapshot settings required; URI fetching is not implicit")
    raw = base64.b64decode(value["content_base64"], validate=True)
    if sha(raw) != value["sha256"]:
        raise ValueError("settings document hash mismatch")
    return json.loads(raw)


def prepare(snapshot, output, legacy_source):
    if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
        raise ValueError("local preparation only")
    snapshot = snapshot.resolve(strict=True)
    receipt = json.loads((snapshot / "source.json").read_bytes())
    settings_raw = (snapshot / "settings.json").read_bytes()
    settings = json.loads(settings_raw)
    dump = snapshot / "production.dump"
    if (receipt.get("instance") != "sawahospitalsystem:asia-northeast2:orders-prod"
            or receipt.get("database") != "orders" or receipt.get("production_read_only") is not True
            or receipt.get("fingerprint_format") != "sha256-sorted-row-sha256-v1"
            or receipt.get("settings_sha256") != sha(settings_raw)
            or settings.get("schema") != 1):
        raise ValueError("same-snapshot production receipt required")
    with dump.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != receipt["dump_sha256"]:
            raise ValueError("production dump hash mismatch")
    master = document(settings["settings"]["FACILITY_MASTER_URI"])
    policy = document(settings["settings"]["INGEST_POLICY_URI"])
    legacy_sha = subprocess.check_output(
        ["git", "rev-parse", "--verify", legacy_source + "^{commit}"], cwd=ROOT, text=True).strip()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    with owned_postgres() as (engine, directory):
        restore_owned(directory, dump)
        with engine.connect() as connection:
            columns = preservation.columns(connection)
            if fingerprints(connection, columns) != receipt["tables"]:
                raise ValueError("local restore differs from captured production")
        archive = directory / "legacy-source.tar"
        with archive.open("xb") as stream:
            subprocess.run(["git", "archive", legacy_sha, "backend/src"], cwd=ROOT,
                           stdout=stream, check=True)
        legacy = directory / "legacy"
        legacy.mkdir()
        with tarfile.open(archive) as bundle:
            bundle.extractall(legacy, filter="data")
        base = legacy / "backend/src/data/facility_master.template.json"
        if sha(base.read_bytes()) != settings["settings"]["FACILITY_MASTER_URI"]["raw_file_sha256"]:
            raise ValueError("legacy master file differs from the running production file")
        # Run the original resolver unchanged, against only our isolated restored DB.
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("DB_", "PG", "FACILITY_MASTER_", "INGEST_POLICY_"))}
        env.update(DB_URI=engine.url.render_as_string(hide_password=False),
                   PGOPTIONS="-c default_transaction_read_only=on",
                   PYTHONPATH=str(legacy / "backend"))
        code = (
            "import json; from sqlalchemy import select; import src.models; "
            "from src.db import session_scope; from src.models.facility import Facility; "
            "from src.services.config_service import get_facility_config; "
            "\nwith session_scope() as session: ids=list(session.execute(select(Facility.id).order_by(Facility.id)).scalars())"
            "\nprint(json.dumps([get_facility_config(i) for i in ids],ensure_ascii=False,allow_nan=False))"
        )
        resolved = json.loads(subprocess.check_output(
            [sys.executable, "-c", code], cwd=legacy, env=env, timeout=120))
        entries = {item["facility_id"]: item for item in master["facilities"]}
        configs = {item["facility_id"]: item for item in resolved}
        if len(entries) != len(master["facilities"]) or len(configs) != len(resolved) or entries.keys() != configs.keys():
            raise ValueError("facility inventory mismatch")
        initial = deepcopy(master)
        changes = []
        for entry in initial["facilities"]:
            if entry.get("versions") not in (None, []):
                raise ValueError("initial versions already exist")
            config = configs[entry["facility_id"]]
            prepared, additions = prepare_config(config, facility_id=entry["facility_id"],
                                                  template_id=config["fax_template_id"])
            entry["versions"] = [store.initial_facility_version(prepared, sha(settings_raw))]
            changes.append({"facility_id": entry["facility_id"], "changes": additions})
        store._encode(initial)
        from src.services.config_service import validate_selected_authored_facility_config
        for entry in initial["facilities"]:
            validate_selected_authored_facility_config(entry["facility_id"], entry["versions"][0], master=initial)
        with target(engine.url), engine.connect() as connection:
            upgrade_connection(connection, environment="local production-settings preparation")
            connection.rollback()
            if fingerprints(connection, columns) != receipt["tables"]:
                raise ValueError("migration modified captured production values")
            connection.rollback()
            from sqlalchemy import select
            from sqlalchemy.orm import Session
            from src.models.order import Order
            from src.models.order_sheet_draft import OrderSheetDraft
            from src.models.order_workflow_state import OrderWorkflowState
            from src.services.initial_saved_source_service import capture_saved_source
            from src.services.initial_saved_import_service import prepare_import_master
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            with Session(bind=connection, autoflush=False, join_transaction_mode="rollback_only") as session:
                ids = session.execute(select(Order.id)
                    .join(OrderWorkflowState, OrderWorkflowState.order_id == Order.id)
                    .join(OrderSheetDraft, OrderSheetDraft.id == OrderWorkflowState.draft_id)
                    .where(Order.archived_at.is_(None), OrderSheetDraft.draft_state == "saved")
                    .order_by(Order.id)).scalars().all()
                # A saved OCR history can be large; never retain every order's payload.
                snapshots = (capture_saved_source(session, identity) for identity in ids)
                final = prepare_import_master(initial, snapshots, policy)
            connection.rollback()
        data = store._encode(final)
        (output / "facility-master.json").write_bytes(data)
        (output / "ingest-policy.json").write_text(json.dumps(policy, ensure_ascii=False, indent=2) + "\n")
        report = {"source_dump_sha256": receipt["dump_sha256"], "source_settings_sha256": sha(settings_raw),
                  "legacy_source_sha": legacy_sha, "facility_master_sha256": sha(data),
                  "facilities": len(entries), "saved_orders": ids, "configuration_changes": changes,
                  "production_modified": False, "published": False}
        (output / "preparation.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"output": str(output), "facilities": len(entries), "saved_orders": len(ids),
                      "production_modified": False, "published": False}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--legacy-source", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    prepare(args.snapshot, args.output, args.legacy_source)
