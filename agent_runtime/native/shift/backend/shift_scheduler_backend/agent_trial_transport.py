"""Explicit local transport for the separate SAWA agent trial worktree."""
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import threading


def enabled():
    return os.getenv("SAWA_AGENT_TRIAL_TRANSPORT") == "request_local"


def configuration():
    if not enabled():
        raise ValueError("request_local_transport_not_enabled")
    root = Path(os.environ["SAWA_TRIAL_DATA"]).resolve()
    if (json.loads((root / "trial-manifest.json").read_text()).get("purpose") != "sawa-agent-isolated-trial"
            or os.environ.get("DATABASE_URL") != "sqlite:///"+str(root / "database.sqlite3")
            or len(os.environ.get("SAWA_TRIAL_TOKEN", "")) < 32
            or not (root.parent.parent / "before" / "shift").is_dir()):
        raise ValueError("request_local_trial_identity_required")
    return {"mode": "request_local", "root": str(root),
            "credential_digest": hashlib.sha256(os.environ["SAWA_TRIAL_TOKEN"].encode()).hexdigest()}


def contract(run_id, generation):
    if type(run_id) is not int or run_id < 1 or type(generation) is not int or generation < 1:
        raise ValueError("request_local_delivery_identity_required")
    return {**configuration(), "run_id": run_id, "generation": generation}


def enqueue(session, request, config):
    from .solve_delivery_service import _enqueue_solve
    if config != configuration():
        raise ValueError("request_local_configuration_differs")
    return _enqueue_solve(session, request, transport_factory=lambda run_id: contract(run_id, 1))


def dispatch(session, run_id):
    from .models import SolveDelivery
    from .solve_delivery_service import _validate_delivery_capture
    from .models import SolveRun
    delivery = session.query(SolveDelivery).filter_by(solve_run_id=run_id).one()
    saved = dict(delivery.transport_contract)
    if saved != contract(run_id, delivery.generation) or delivery.state != "pending":
        raise ValueError("request_local_saved_contract_differs")
    _validate_delivery_capture(delivery, session.get(SolveRun, run_id))
    ident = delivery.id
    session.commit()
    folder = Path(configuration()["root"]) / "local-deliveries"
    folder.mkdir(exist_ok=True)
    receipt_path = folder / f"solve-{run_id}-g{saved['generation']}.json"
    with receipt_path.with_name("."+receipt_path.name+".claim").open("x"):
        pass
    def persist(receipt):
        temporary = receipt_path.with_name("."+receipt_path.name+".tmp")
        temporary.write_text(json.dumps({**receipt, "contract": saved}))
        os.replace(temporary, receipt_path)
    persist({"state": "accepted"})

    def execute():
        from .db import get_session_factory
        from .solve_delivery_service import claim_solve, execute_claimed_solve
        try:
            with get_session_factory()() as owned:
                # Revalidate captured local identity before obtaining the native claim.
                row = owned.get(SolveDelivery, ident)
                if row.transport_contract != saved or saved != contract(run_id, row.generation):
                    raise ValueError("request_local_saved_contract_differs")
                owned.rollback()
                now = datetime.utcnow()
                claim = claim_solve(owned, ident, now=now, lease_until=now+timedelta(seconds=1800))
                if claim is None:
                    raise ValueError("request_local_delivery_not_claimable")
                result = execute_claimed_solve(owned, claim, clock=datetime.utcnow)
                receipt = {"state": "returned", "solve_status": result.status}
        except Exception as exc:
            receipt = {"state": "failed", "error_class": type(exc).__name__}
        persist(receipt)
    threading.Thread(target=execute, daemon=True, name="sawa-request-solve").start()
