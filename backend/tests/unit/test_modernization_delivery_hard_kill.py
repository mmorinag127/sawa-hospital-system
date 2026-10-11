"""Owned local SIGKILL recovery; not machine/cloud/PG/OIDC/native OCR proof."""
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

from test_modernization_delivery_process_restart import child, owned_failure, stable_data
from test_modernization_owned_ocr_workflow import ORDER, JOB, _owned_workflow_database_snapshot
from src import db
from src.models.order_workflow_state import OrderWorkflowState
from src.models.workflow_ocr_delivery import WorkflowOcrDelivery
from src.services import order_service
from src.services import order_workflow_v2_service as workflow
from src.services import master_order_form_template_service as master
from src.services import workflow_ocr_delivery_service as delivery


def test_owned_claim_survives_sigkill_and_successor_acks_terminal_once(owned_failure):
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
    originals = {path: path.read_bytes() for path in f.root.iterdir()
                 if path.suffix in {".pdf", ".json", ".xlsx"}}
    expected = deepcopy(f.expected)
    env = os.environ.copy()
    for name in ("ALEMBIC_DB_URI", "DB_URI", "DATABASE_URL"):
        env[name] = str(f.engine.url)
    env["PYTHONPATH"] = os.pathsep.join([
        str(Path(__file__).resolve().parents[2]), str(Path(__file__).resolve().parent),
        env.get("PYTHONPATH", ""),
    ])

    def request_path(action, **extra):
        path = f.root / f"hard-kill-{action}-request.json"
        path.write_text(json.dumps({"action": action, "database": str(f.engine.url),
                                   "identity": identity, "policy": str(f.root / "ingest-policy.json"), **extra}))
        return path

    claim_request = request_path("claim")
    process = None
    with (f.root / "hard-kill-claim.log").open("wb") as log:
        try:
            process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), str(claim_request)],
                                       env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log)
            deadline = time.monotonic() + 180
            ready = bytearray()
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while b"\n" not in ready:
                    remaining = deadline - time.monotonic()
                    assert remaining > 0, "Committed claim readiness deadline exceeded"
                    assert selector.select(remaining), "Committed claim readiness timed out"
                    chunk = os.read(process.stdout.fileno(), 65536)
                    assert chunk, "Child exited before committed claim readiness"
                    ready.extend(chunk)
            first = json.loads(ready.split(b"\n", 1)[0])
            assert first["pid"] == process.pid and process.poll() is None
            assert first["result"]["selected_pdf_receipt"] == f.receipt
            with db.session_scope() as session:
                persisted = session.get(WorkflowOcrDelivery, identity["delivery_id"])
                assert persisted.claim_token == first["result"]["claim_token"]
                assert persisted.lease_expires_at is not None
            process.kill()  # Popen owns this unreaped child, not a looked-up PID.
            stdout, _ = process.communicate(timeout=180)
            assert stdout == b""
            assert process.returncode == -signal.SIGKILL
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=180)
                assert process.poll() is not None

    def run(action, **extra):
        response_path = f.root / f"hard-kill-{action}-response.json"
        completed = subprocess.run([
            sys.executable, str(Path(__file__).resolve()), str(request_path(action, **extra)), str(response_path),
        ], env=env, capture_output=True, text=True, timeout=180)
        (f.root / f"hard-kill-{action}.log").write_text(completed.stdout + completed.stderr)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        return json.loads(response_path.read_text())

    # Existing real helper verifies duplicate-before-expiry, explicit technical
    # expiry, immutable successor identity, old-token refusal, worker and ACK.
    second = run("recover", old_claim=first["result"])
    terminal_before = _owned_workflow_database_snapshot()
    third = run("terminal", acknowledged_claim=second["result"]["claim"])
    assert _owned_workflow_database_snapshot() == terminal_before
    assert len({os.getpid(), first["pid"], second["pid"], third["pid"]}) == 4
    assert all(third["result"][key] == value
               for key, value in second["result"]["worker_result"]["terminal_receipt"].items())
    assert stable_data(_owned_workflow_database_snapshot()) == stable_data(before)
    assert all(path.read_bytes() == content for path, content in originals.items())
    assert f.expected == expected
    assert sha256(master.MASTER_TEMPLATE_PATH.read_bytes()).hexdigest() == f.locked_hash


if __name__ == "__main__":
    request = json.loads(Path(sys.argv[1]).read_text())
    result = child(request)
    if len(sys.argv) == 2:
        print(json.dumps(result), flush=True)
        sys.stdin.buffer.read()  # Remain alive until the owning parent SIGKILLs.
        raise AssertionError("Claim child must not exit normally")
    if request["action"] == "terminal":
        try:
            before = _owned_workflow_database_snapshot()
            acknowledged = request["acknowledged_claim"]
            assert delivery.acknowledge_claim(acknowledged["delivery_id"], acknowledged["generation"],
                                              acknowledged["claim_token"]) is False
            assert _owned_workflow_database_snapshot() == before
        finally:
            db.engine.dispose()
    Path(sys.argv[2]).write_text(json.dumps(result))
