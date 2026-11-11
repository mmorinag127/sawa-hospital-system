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
import fcntl
import queue
import secrets
import logging
import resource
import sys
from contextvars import copy_context

_QUEUE = queue.Queue()
_QUEUE_LOCK = threading.Lock()
_WORKER = None
_ENQUEUED = set()
_ROOT_LOCKS = {}


def _delivery_identity(root):
    folder = root / 'local-deliveries'
    folder.mkdir(exist_ok=True)
    identity = folder / '.identity'
    if not identity.exists():
        # Old checkpoints already bind all accepted work to one transport ID.
        # This is a delivery identity, not the rotating HTTP access credential.
        previous = set()
        for path in folder.iterdir():
            if not path.name.startswith('.') and path.is_file():
                saved = json.loads(path.read_text())['contract']
                if saved.get('mode') != 'request_local' or saved.get('name') != str(root / 'tasks' / (saved['identity'] + '-g' + str(saved['generation']))):
                    raise ValueError('request_local_saved_contract_differs')
                previous.add(saved['credential_digest'])
        if len(previous) > 1:
            raise ValueError('request_local_identity_ambiguous')
        value = next(iter(previous)) if previous else secrets.token_hex(32)
        temporary = folder / ('.identity-' + secrets.token_hex(8))
        try:
            temporary.write_text(value)
            temporary.chmod(0o600)
            try:
                os.link(temporary, identity)
            except FileExistsError:
                pass
        finally:
            temporary.unlink(missing_ok=True)
    value = identity.read_text()
    if len(value) != 64 or any(char not in '0123456789abcdef' for char in value):
        raise ValueError('request_local_identity_invalid')
    return value


def _enqueue(root, path, execute):
    global _WORKER
    with _QUEUE_LOCK:
        if str(root) not in _ROOT_LOCKS:
            owner = (root / 'local-deliveries' / '.worker-lock').open('a')
            try:
                fcntl.flock(owner.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                owner.close()
                raise ValueError('request_local_worker_still_owned')
            _ROOT_LOCKS[str(root)] = owner
        if str(path) in _ENQUEUED:
            return
        _ENQUEUED.add(str(path))
        _QUEUE.put((copy_context(), execute))
        if _WORKER is None or not _WORKER.is_alive():
            _WORKER = threading.Thread(target=_drain, daemon=True, name='sawa-request-delivery')
            _WORKER.start()


def _drain():
    while True:
        context, execute = _QUEUE.get()
        try:
            context.run(execute)
        except Exception:
            logging.getLogger(__name__).exception('Request-local delivery receipt could not be persisted')
        finally:
            _QUEUE.task_done()


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
    return {"mode": "request_local", "root": str(root), "relative_storage": marker.get('request_relative_storage') is True,
            "credential_digest": _delivery_identity(root)}


def load_source_asset(uri, generation=None):
    """Read an explicitly captured source asset, never a live production fallback."""
    if not enabled():
        return None
    cfg = configuration()
    root = Path(cfg['root'])
    marker = json.loads((root/'trial-manifest.json').read_text())
    if marker.get('initialization') != 'register-authoritative-pending-faxes-v1':
        return None
    captured = marker.get('source_assets', {}).get(uri)
    if captured is not None:
        unresolved = root/captured['path']
        path = unresolved.resolve()
        if (not path.is_relative_to(root) or any(parent.is_symlink() for parent in (unresolved,*unresolved.parents) if parent != root.parent)
                or generation is not None and str(generation) != str(captured.get('generation'))):
            raise ValueError('trial_source_asset_identity_changed')
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != captured['sha256']:
            raise ValueError('trial_source_asset_digest_changed')
        return body
    if uri.startswith('gs://') or '${' in uri:
        bucket = os.environ.get('OCR_PIPELINE_BUCKET', '').removeprefix('gs://').rstrip('/')
        run_id = root.parent.parent.name
        if len(run_id) != 32 or not uri.startswith(f'gs://{bucket}/agent-trial/{run_id}/'):
            raise ValueError('trial_source_asset_not_captured')
    return None


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


def submit(recorded, identity, generation, kind, callback, *, _resume=False):
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
        if not _resume or receipt['state'] != 'accepted':
            return {"action": "already_accepted", "transport": "request_local"}

    def persist(receipt):
        temporary = path.with_name("."+path.name+".tmp")
        temporary.write_text(json.dumps(receipt, ensure_ascii=False, default=str))
        os.replace(temporary, path)
    if not path.exists():
        persist({"state": "accepted", "contract": recorded})

    def execute():
        try:
            result = callback()
            receipt = {"state": "returned", "result": result}
        except Exception as exc:
            receipt = {"state": "failed", "error_class": type(exc).__name__}
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        receipt['native_peak_rss_bytes'] = int(peak if sys.platform == 'darwin' else peak * 1024)
        receipt["contract"] = recorded
        persist(receipt)
    _enqueue(Path(configuration()['root']), path, execute)
    return {"action": "accepted", "transport": "request_local"}


def resume_deliveries():
    """Redeliver saved worker contracts, never repeat their HTTP intake command.

    The process lock excludes a surviving native worker; native claim/generation
    fences and terminal receipts still decide whether business work may execute.
    """
    root = Path(configuration()['root'])
    pending = []
    for path in sorted((root / 'local-deliveries').iterdir()):
        if path.name.startswith('.') or not path.is_file():
            continue
        receipt = json.loads(path.read_text())
        if receipt.get('state') != 'accepted':
            continue
        recorded = receipt['contract']
        identity, generation, kind = (recorded[key] for key in ('identity', 'generation', 'kind'))
        validate(recorded, identity, generation, kind)
        if path.name != hashlib.sha256(recorded['name'].encode()).hexdigest():
            raise ValueError('request_local_receipt_path_differs')
        if kind == 'ingest':
            from src.services.ingest_delivery_service import process
        elif kind == 'workflow':
            from src.api.worker import execute_workflow_ocr_delivery as process
        else:
            # A provider mutation needs its own saved response, not a retry.
            raise ValueError('request_local_provider_response_unknown')
        pending.append((recorded, identity, generation, kind, process))
    for recorded, identity, generation, kind, process in pending:
        submit(recorded, identity, generation, kind,
            lambda process=process, identity=identity, generation=generation: process(identity, generation), _resume=True)
    return {'accepted': len(pending)}
