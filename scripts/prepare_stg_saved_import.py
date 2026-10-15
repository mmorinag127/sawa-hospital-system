"""Capture explicitly selected staging saved results without modifying the database."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from copy import deepcopy
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
_phase = "startup"


def checked_inputs(args):
    from src.services.canonical_facility_store import _unique_object, _invalid_constant

    paths = [getattr(args, name).resolve() for name in ("initial", "policy", "orders")]
    output = args.output.resolve()
    if len(set(paths + [output])) != 4 or output.exists():
        raise ValueError("saved_import_paths_must_be_distinct_and_output_new")
    values = []
    for name, path in zip(("initial", "policy", "orders"), paths):
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != getattr(args, name + "_sha256"):
            raise ValueError("saved_import_input_hash_mismatch:" + name)
        values.append(json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_invalid_constant))
    initial, policy, orders = values
    if not isinstance(initial, dict) or not isinstance(policy, dict):
        raise ValueError("saved_import_configuration_object_required")
    if (not isinstance(orders, list) or not orders
            or any(not isinstance(value, str) or not value or value != value.strip() for value in orders)
            or len(set(orders)) != len(orders)):
        raise ValueError("saved_import_unique_explicit_order_ids_required")
    return initial, policy, orders


def verify_live_initial(initial, token):
    global _phase
    _phase = "initial_validation"
    from staging_db_target import read_http
    from src.services.canonical_facility_store import _encode

    if not token:
        raise ValueError("saved_import_live_verification_token_required")
    _encode(initial)
    facilities = initial["facilities"]
    if not facilities:
        raise ValueError("saved_import_initial_facilities_required")
    _phase = "live_facility_inventory"
    status, body = read_http("https://web-stg-avlnzjjrca-dt.a.run.app/api/facilities", token)
    if status != 200:
        raise ValueError("saved_import_live_facility_read_failed")
    live_ids = [entry["id"] for entry in json.loads(body)["facilities"]]
    if len(live_ids) != len(facilities) or set(live_ids) != {entry["facility_id"] for entry in facilities}:
        raise ValueError("saved_import_live_facility_inventory_changed")
    for entry in facilities:
        _phase = "live_facility_configuration"
        versions = entry.get("versions")
        if not isinstance(versions, list) or len(versions) != 1 or "initial_snapshot" not in versions[0]:
            raise ValueError("saved_import_single_initial_snapshot_required")
        expected = versions[0]["facility"]
        status, body = read_http("https://web-stg-avlnzjjrca-dt.a.run.app/api/facilities/"
                                 + quote(entry["facility_id"], safe=""), token)
        if status != 200:
            raise ValueError("saved_import_live_facility_read_failed")
        actual = deepcopy(json.loads(body)["resolved_config"])
        # The legacy endpoint decorates the configured input with DB-version identity metadata.
        actual.pop("facility_template_version_id", None)
        actual.pop("facility_template_version", None)
        template = actual["fax_template"]
        template.pop("facility_template_version_id", None)
        template.pop("facility_template_version_digest", None)
        if "template_id" not in template:
            template["template_id"] = actual["fax_template_id"]
        if actual != expected:
            raise ValueError("saved_import_live_facility_configuration_changed:" + entry["facility_id"])


def capture(connection, initial, policy, orders, *, snapshots_out=None):
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from staging_db_target import verify_connection_target
    from src.maintenance.formal_migration_schema import require_formal_head
    from src.services.initial_saved_source_service import capture_saved_source
    from src.services.initial_saved_import_service import prepare_import_master
    from src.models.order import Order
    from src.models.order_sheet_draft import OrderSheetDraft
    from src.models.order_workflow_state import OrderWorkflowState

    if connection.in_transaction():
        raise RuntimeError("saved_import_owned_transaction_required")
    transaction = connection.begin()
    try:
        connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        connection.exec_driver_sql("SET LOCAL statement_timeout = '60s'")
        if connection.exec_driver_sql("SHOW transaction_read_only").scalar_one() != "on":
            raise RuntimeError("saved_import_read_only_required")
        verify_connection_target(connection)
        require_formal_head(connection)
        with Session(bind=connection, autoflush=False, join_transaction_mode="rollback_only") as session:
            selected = session.execute(
                select(Order.id).join(OrderWorkflowState, OrderWorkflowState.order_id == Order.id)
                .join(OrderSheetDraft, OrderSheetDraft.id == OrderWorkflowState.draft_id)
                .where(Order.archived_at.is_(None), OrderSheetDraft.draft_state == "saved")
            ).scalars().all()
            if set(selected) != set(orders):
                raise ValueError("saved_import_selected_order_inventory_changed")
            snapshots = [capture_saved_source(session, order_id) for order_id in orders]
            result = prepare_import_master(initial, snapshots, policy)
            if snapshots_out is not None:
                snapshots_out.extend(snapshots)
            return result
    finally:
        if transaction.is_active:
            transaction.rollback()


def read_http_evidence(snapshots, token, source_sha):
    from staging_db_target import read_http

    if not token:
        raise ValueError("saved_import_live_verification_token_required")
    fixtures = [s for s in snapshots if s["evidence"] is None]
    normal = [s for s in snapshots if s["evidence"] is not None]
    targets = fixtures + normal[:1]
    evidence = {"source_sha": source_sha, "phase": "before_import_publication", "orders": []}
    for snapshot in targets:
        order_id = snapshot["order"]["id"]
        original = snapshot["draft"]["draft_sheet_json"]
        record = {"order_id": order_id, "saved_sheet": deepcopy(original), "responses": []}
        for suffix in ("", "/workflow-v2", "/workflow-v2/sheet"):
            path = "/api/orders/" + quote(order_id, safe="") + suffix
            status, raw = read_http("https://web-stg-avlnzjjrca-dt.a.run.app" + path, token)
            response = {"path": path, "status": status, "raw_sha256": hashlib.sha256(raw).hexdigest(),
                        "raw_bytes": len(raw), "sheets": []}
            try:
                payload = json.loads(raw)
            except (ValueError, UnicodeError):
                payload = None
            def inspect(value, location):
                if isinstance(value, dict):
                    if "fields" in value and "rows" in value:
                        response["sheets"].append({"path": location,
                            "fields": value["fields"], "rows": value["rows"],
                            "fields_equal": value["fields"] == original.get("fields"),
                            "rows_equal": value["rows"] == original.get("rows"),
                            "whole_sheet_equal": value == original})
                    for key, item in value.items():
                        inspect(item, location + "/" + key.replace("~", "~0").replace("/", "~1"))
                elif isinstance(value, list):
                    for index, item in enumerate(value):
                        inspect(item, location + "/" + str(index))
            inspect(payload, "")
            record["responses"].append(response)
        evidence["orders"].append(record)
    return evidence


def run(args):
    global _phase
    _phase = "runtime_imports"
    from run_stg_menu_master_migration import (
        require_staging_context, cloud_sql_proxy, _load_service_db_config,
        STAGING_INSTANCE, STAGING_DATABASE,
    )
    from sqlalchemy import create_engine
    from sqlalchemy.engine import URL

    _phase = "staging_context"
    require_staging_context()
    _phase = "source_identity"
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if sha != args.source_sha or sha != os.environ.get("GITHUB_SHA"):
        raise ValueError("saved_import_actions_source_sha_mismatch")
    _phase = "checked_inputs"
    initial, policy, orders = checked_inputs(args)
    verify_live_initial(initial, os.environ.get("LIVE_ID_TOKEN", ""))
    _phase = "service_configuration"
    configs = [_load_service_db_config(os.environ["PROJECT_ID"], os.environ["REGION"], os.environ[key])
               for key in ("WEB_SERVICE", "WORKER_SERVICE")]
    _phase = "staging_target"
    for config in configs:
        if (config.instance_connection_name, config.db_name, config.db_user) != (
                STAGING_INSTANCE, STAGING_DATABASE, "orders_app"):
            raise ValueError("saved_import_staging_target_required")
    web = configs[0]
    _phase = "database_proxy"
    with cloud_sql_proxy(web.instance_connection_name):
        _phase = "database_connection"
        url = URL.create("postgresql+psycopg2", username=web.db_user, password=web.db_password,
                         host="127.0.0.1", port=5432, database=web.db_name,
                         query={"options": "-c default_transaction_read_only=on"})
        engine = create_engine(url, hide_parameters=True)
        previous = os.environ.get("DB_URI")
        os.environ["DB_URI"] = url.render_as_string(hide_password=False)
        try:
            with engine.connect() as connection:
                _phase = "saved_capture"
                snapshots = []
                result = capture(connection, initial, policy, orders, snapshots_out=snapshots)
        finally:
            engine.dispose()
            if previous is None:
                os.environ.pop("DB_URI", None)
            else:
                os.environ["DB_URI"] = previous
    evidence = None
    if getattr(args, "capture_http_evidence", False):
        _phase = "http_read_evidence"
        token = os.environ.get("LIVE_ID_TOKEN", "")
        evidence = read_http_evidence(snapshots, token, sha)
        serialized = json.dumps(evidence, ensure_ascii=False, indent=2, allow_nan=False)
        if token in serialized:
            raise ValueError("saved_import_http_evidence_credential_rejected")
    _phase = "write_output"
    raw = (json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
    with args.output.open("xb") as stream:
        stream.write(raw)
    receipt = {"source_sha": sha, "orders": len(orders),
               "sha256": hashlib.sha256(raw).hexdigest(), "database_modified": False}
    if evidence is not None:
        receipt["http_evidence"] = evidence
    print(json.dumps(receipt, ensure_ascii=False, allow_nan=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("initial", "policy", "orders"):
        parser.add_argument("--" + name, required=True, type=Path)
        parser.add_argument("--" + name + "-sha256", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--capture-http-evidence", action="store_true")
    args = parser.parse_args()
    try:
        run(args)
    except Exception as exc:
        print(json.dumps({"phase": _phase, "exception_type": type(exc).__name__}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
