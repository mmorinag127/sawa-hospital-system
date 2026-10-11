"""Owned SIGKILL/recovery proof only; not HTTP/OIDC, PG, cloud or OCR success."""

from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time

from tests.unit.test_modernization_delivery_process_restart import child
from tests.unit.test_modernization_internal_failure_delivery import owned_failure, stable_data
from tests.unit.test_modernization_owned_ocr_workflow import (
    JOB, ORDER, _owned_workflow_database_snapshot,
)
from src import db
from src.models.order_workflow_state import OrderWorkflowState
from src.models.workflow_ocr_delivery import WorkflowOcrDelivery
from src.services import master_order_form_template_service as master
from src.services import order_service
from src.services import order_workflow_v2_service as workflow
from src.services import workflow_ocr_delivery_service as delivery


TIMEOUT = 180


def _read_ready(process):
    deadline = time.monotonic() + TIMEOUT
    data = bytearray()
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while b"\n" not in data:
            remaining = deadline - time.monotonic()
            assert remaining > 0, "owned claim readiness timed out"
            assert selector.select(remaining), "owned claim readiness timed out"
            block = os.read(process.stdout.fileno(), 65536)
            assert block, "owned child exited before claim readiness"
            data.extend(block)
    line, trailing = bytes(data).split(b"\n", 1)
    assert not trailing, "unexpected output after claim readiness"
    return json.loads(line)


def _reap_owned(process):
    # An unreaped Popen child retains its PID; never target a reported/arbitrary PID.
    if process.poll() is None:
        process.kill()
    process.communicate(timeout=TIMEOUT)
    assert process.returncode is not None


def test_real_committed_claim_survives_sigkill_recovery_and_terminal_duplicate(owned_failure):
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
    env = os.environ.copy()
    for name in ("ALEMBIC_DB_URI", "DB_URI", "DATABASE_URL"):
        env[name] = str(f.engine.url)
    env["PYTHONPATH"] = os.pathsep.join([
        str(Path(__file__).resolve().parents[2]), str(Path(__file__).resolve().parent),
        env.get("PYTHONPATH", ""),
    ])

    def launch(action, **extra):
        request = {"action": action, "database": str(f.engine.url), "identity": identity,
                   "policy": str(f.root / "ingest-policy.json"), **extra}
        request_path = f.root / f"abrupt-{action}-request.json"
        request_path.write_text(json.dumps(request))
        return [sys.executable, str(Path(__file__).resolve()), str(request_path)]

    with (f.root / "abrupt-claim-child.log").open("wb") as log:
        process = subprocess.Popen(launch("claim"), env=env, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=log)
        try:
            first = _read_ready(process)
            assert first["pid"] == process.pid and first["pid"] != os.getpid()
            assert first["result"]["selected_pdf_receipt"] == f.receipt
            assert process.poll() is None
            with db.session_scope() as session:
                row = session.get(WorkflowOcrDelivery, identity["delivery_id"])
                assert row.state == delivery.CLAIMED
                assert row.delivery_generation == identity["generation"]
                assert row.claim_token == first["result"]["claim_token"]
                assert row.lease_expires_at is not None
            committed = _owned_workflow_database_snapshot()
            (f.root / "abrupt-committed-claim.json").write_text(json.dumps(first))
            process.kill()
            process.communicate(timeout=TIMEOUT)
            assert process.returncode == -signal.SIGKILL
            assert _owned_workflow_database_snapshot() == committed
        finally:
            _reap_owned(process)

    def run(action, **extra):
        with (f.root / f"abrupt-{action}-child.log").open("wb") as log:
            process = subprocess.Popen(launch(action, **extra), env=env,
                                       stdout=subprocess.PIPE, stderr=log)
            try:
                stdout, _ = process.communicate(timeout=TIMEOUT)
                assert process.returncode == 0, log.name
                result = json.loads(stdout)
                assert result["pid"] == process.pid
                (f.root / f"abrupt-{action}-response.json").write_text(json.dumps(result))
                return result
            finally:
                _reap_owned(process)

    second = run("recover", old_claim=first["result"])
    third = run("terminal")
    assert len({os.getpid(), first["pid"], second["pid"], third["pid"]}) == 4
    assert all(third["result"][key] == value
               for key, value in second["result"]["worker_result"]["terminal_receipt"].items())
    assert stable_data(_owned_workflow_database_snapshot()) == stable_data(before)
    assert all(Path(path).read_bytes() == content for path, content in originals.items())
    assert f.expected == expected
    assert sha256(master.MASTER_TEMPLATE_PATH.read_bytes()).hexdigest() == f.locked_hash


if __name__ == "__main__":
    request = json.loads(Path(sys.argv[1]).read_text())
    result = child(request)
    print(json.dumps(result), flush=True)
    if request["action"] == "claim":
        sys.stdin.buffer.read(1)
