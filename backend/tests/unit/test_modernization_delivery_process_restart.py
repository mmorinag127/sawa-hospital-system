"""Separate-process local worker/SQLite recovery, not OIDC/provider/PG/live proof."""
from copy import deepcopy
from datetime import datetime, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys

from test_modernization_internal_failure_delivery import (
    owned_failure, execute_delivery, assert_failed_terminal, stable_data,
)
from test_modernization_owned_ocr_workflow import (
    ORDER, JOB, _owned_workflow_database_snapshot,
)
from src import db
from src.models.order_workflow_state import OrderWorkflowState
from src.models.workflow_ocr_delivery import WorkflowOcrDelivery
from src.services import order_service
from src.services import order_workflow_v2_service as workflow
from src.services import workflow_ocr_delivery_service as delivery
from src.services import master_order_form_template_service as master


def child(request):
    """Bind existing owned storage only; never construct OwnedProducer here."""
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from src.services import config_service, menu_service, facility_template_version_service
    from src.services import workflow_ocr_delivery_execution_service as execution
    from src.services import ocr_execution_lock_service as slots

    engine = create_engine(request["database"])

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    db.engine = engine
    db.SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    for module in (menu_service, facility_template_version_service, execution, slots):
        module.engine = engine
    config_service.INGEST_POLICY_PATH = Path(request["policy"])
    assert all(module.engine.url == engine.url for module in (
        db, menu_service, facility_template_version_service, execution, slots))
    identity = request["identity"]
    try:
        if request["action"] == "claim":
            result = delivery.claim_delivery(**identity,
                lease_expires_at=datetime.utcnow() + timedelta(minutes=5))
            assert not result.get("duplicate"), result
        elif request["action"] == "recover":
            old = request["old_claim"]
            before = _owned_workflow_database_snapshot()
            assert delivery.claim_delivery(**identity,
                lease_expires_at=datetime.utcnow() + timedelta(minutes=5)) == {"duplicate": True}
            assert _owned_workflow_database_snapshot() == before
            with db.session_scope() as session:
                session.get(WorkflowOcrDelivery, identity["delivery_id"]).lease_expires_at = (
                    datetime.utcnow() - timedelta(seconds=1))
            successor = delivery.claim_delivery(**identity,
                lease_expires_at=datetime.utcnow() + timedelta(minutes=5))
            assert not successor.get("duplicate"), successor
            assert successor["claim_token"] != old["claim_token"]
            for key in ("delivery_id", "generation", "workflow_attempt_id", "workflow_revision",
                        "selected_pdf_receipt", "request_identity"):
                assert successor[key] == old[key]
            before_refusal = _owned_workflow_database_snapshot()
            assert delivery.acknowledge_claim(old["delivery_id"], old["generation"], old["claim_token"]) is False
            assert _owned_workflow_database_snapshot() == before_refusal
            result = execute_delivery(successor)
            assert_failed_terminal(successor, result)
            assert delivery.acknowledge_claim(successor["delivery_id"], successor["generation"],
                                              successor["claim_token"]) is True
            result = {"claim": successor, "worker_result": result}
        else:
            assert request["action"] == "terminal"
            before = _owned_workflow_database_snapshot()
            with db.session_scope() as session:
                row = session.get(WorkflowOcrDelivery, identity["delivery_id"])
                assert row.state == delivery.ACKNOWLEDGED and row.lease_expires_at is None
                receipt = deepcopy(row.result_receipt)
                assert receipt["status"] == "failed" and receipt["error"] == "quad_estimation_failed"
            assert delivery.claim_delivery(**identity,
                lease_expires_at=datetime.utcnow() + timedelta(minutes=5)) == {"duplicate": True}
            assert _owned_workflow_database_snapshot() == before
            result = receipt
        return {"pid": os.getpid(), "result": result}
    finally:
        engine.dispose()


def test_real_delivery_survives_process_exit_reclaims_and_acks_once(owned_failure):
    f = owned_failure
    order = order_service.get_order_by_id(ORDER)
    selected = [row for row in order["versions"] if row["document_id"] == f.receipt["document_id"]]
    assert len(selected) == 1 and selected[0]["storage_uri"] == str(f.pdf)
    with db.session_scope() as session:
        revision = session.get(OrderWorkflowState, ORDER).revision
    queued, error = workflow.mark_ocr_run_queued(
        ORDER, JOB, input_reference=str(f.pdf), request_mode="ocr_rerun",
        job_metrics={"selected_version_input": selected[0]["selected_version_input"]},
        delivery_selected_document_id=f.receipt["document_id"], expected_revision=revision,
    )
    assert error is None, (error, queued)
    identity = {"delivery_id": queued["delivery"]["id"], "generation": queued["delivery"]["generation"]}
    before = _owned_workflow_database_snapshot()
    originals = {str(path): path.read_bytes() for path in f.root.iterdir()
                 if path.suffix in {".pdf", ".json", ".xlsx"}}
    expected = deepcopy(f.expected)

    def run(action, **extra):
        request = {"action": action, "database": str(f.engine.url), "identity": identity,
                   "policy": str(f.root / "ingest-policy.json"), **extra}
        request_path, response_path = f.root / f"{action}-request.json", f.root / f"{action}-response.json"
        request_path.write_text(json.dumps(request))
        env = os.environ.copy()
        env["DB_URI"] = env["DATABASE_URL"] = str(f.engine.url)
        env["PYTHONPATH"] = os.pathsep.join([str(Path(__file__).resolve().parents[2]),
            str(Path(__file__).resolve().parent), env.get("PYTHONPATH", "")])
        completed = subprocess.run([sys.executable, str(Path(__file__).resolve()),
            str(request_path), str(response_path)], env=env, capture_output=True, text=True, timeout=180)
        (f.root / f"{action}-child.log").write_text(completed.stdout + completed.stderr)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        return json.loads(response_path.read_text())

    first = run("claim")
    assert first["result"]["selected_pdf_receipt"] == f.receipt
    second = run("recover", old_claim=first["result"])
    third = run("terminal")
    assert len({os.getpid(), first["pid"], second["pid"], third["pid"]}) == 4
    assert third["result"] == second["result"]["worker_result"]["terminal_receipt"] or all(
        third["result"][key] == value
        for key, value in second["result"]["worker_result"]["terminal_receipt"].items())
    assert stable_data(_owned_workflow_database_snapshot()) == stable_data(before)
    assert all(Path(path).read_bytes() == content for path, content in originals.items())
    assert f.expected == expected
    assert sha256(master.MASTER_TEMPLATE_PATH.read_bytes()).hexdigest() == f.locked_hash


if __name__ == "__main__":
    Path(sys.argv[2]).write_text(json.dumps(child(json.loads(Path(sys.argv[1]).read_text()))))
