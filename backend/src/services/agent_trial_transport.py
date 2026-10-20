"""Explicit request-local delivery for the isolated agent worktree trial.

This is not a Cloud Tasks emulator. The normal cloud dispatcher and OIDC worker
endpoints retain their contracts. Local execution uses the same business claims,
input snapshots and terminal receipts, inside the killable API process group.
"""
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
    marker = json.loads((root / "trial-manifest.json").read_text())
    if (marker.get("purpose") != "sawa-agent-isolated-trial"
            or os.environ.get("DB_URI") != "sqlite:///"+str(root / "database.sqlite3")
            or len(os.environ.get("SAWA_TRIAL_TOKEN", "")) < 32
            or not (root.parent.parent / "before" / "hospital").is_dir()):
        raise ValueError("request_local_trial_identity_required")
    return {"mode": "request_local", "root": str(root),
            "credential_digest": hashlib.sha256(os.environ["SAWA_TRIAL_TOKEN"].encode()).hexdigest()}


def contract(identity, generation, kind):
    cfg = configuration()
    if kind not in {"ingest", "workflow", "auto-llm"} or not identity or type(generation) is not int or generation < 1:
        raise ValueError("request_local_delivery_identity_required")
    return {"mode": "request_local", "name": cfg["root"]+"/tasks/"+identity+"-g"+str(generation),
            "identity": identity, "generation": generation, "kind": kind,
            "credential_digest": cfg["credential_digest"],
            # Successor capture uses this route label; it is never an HTTP target.
            "http_request": {"url": "request-local:/"+kind}}


def validate(recorded, identity, generation, kind):
    if recorded != contract(identity, generation, kind):
        raise ValueError("request_local_saved_contract_differs")


def submit(recorded, identity, generation, kind, callback):
    validate(recorded, identity, generation, kind)
    folder = Path(configuration()["root"]) / "local-deliveries"
    folder.mkdir(exist_ok=True)
    path = folder / hashlib.sha256(recorded["name"].encode()).hexdigest()
    try:
        with path.with_name("."+path.name+".claim").open("x"):
            pass
    except FileExistsError:
        if not path.is_file():
            raise ValueError("request_local_acceptance_unknown")
        receipt = json.loads(path.read_text())
        if receipt["contract"] != recorded:
            raise ValueError("request_local_receipt_differs")
        # Crash-unknown executions are not retried from an HTTP request.
        return {"action": "already_accepted", "transport": "request_local"}

    def persist(receipt):
        temporary = path.with_name("."+path.name+".tmp")
        temporary.write_text(json.dumps(receipt, ensure_ascii=False, default=str))
        os.replace(temporary, path)
    persist({"state": "accepted", "contract": recorded})

    def execute():
        try:
            result = callback()
            receipt = {"state": "returned", "result": result}
        except Exception as exc:
            receipt = {"state": "failed", "error_class": type(exc).__name__}
        receipt["contract"] = recorded
        persist(receipt)
    threading.Thread(target=execute, daemon=True, name="sawa-request-delivery").start()
    return {"action": "accepted", "transport": "request_local"}
