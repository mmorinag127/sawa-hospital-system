"""Synthetic proofs for the explicitly approved change gate, not release evidence."""
from copy import deepcopy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import production_change_receipt as subject


def valid():
    return {"receipt_version": 6, "source_sha": "f" * 40, "definition_sha256": "d" * 64,
            "baseline": {"payload": {"receipt_version": 5, "source_sha": subject.BASE}, "signature": "synthetic"},
            "deployment": {"id": subject.RUN, "source_sha": subject.BASE, "repository": subject.REPOSITORY},
            "changed_files": ["backend/src/api/orders.py"], "clean_before": True, "clean_after": True,
            "full_rerun": False, "commands": [
                {"name": name, "command_sha256": subject.command_hash(argv, directory),
                 "log_sha256": "e" * 64, "exit_code": 0}
                for name, argv, directory in subject.COMMANDS]}


@pytest.mark.parametrize("change", [None, "dirty", "full", "source", "definition", "baseline",
    "recursive", "signature", "deployment", "files", "extra-runtime", "failed", "missing",
    "command", "log", "bool-exit"])
def test_gate_requires_verified_prior_release_exact_changes_and_real_related_success(monkeypatch, change):
    import local_hospital_test_runner
    proof = valid()
    files = list(proof["changed_files"])
    calls = []
    monkeypatch.setattr(local_hospital_test_runner, "validate_receipt", lambda *args, **kwargs: calls.append(args))
    def verify(envelope):
        if change == "signature":
            raise ValueError("bad signature")
        return envelope["payload"]
    monkeypatch.setattr(subject, "verify_signed", verify)
    monkeypatch.setattr(subject, "successful_baseline_run", lambda: valid()["deployment"])
    monkeypatch.setattr(subject, "git", lambda *args: "\n".join(files) if args[0] == "diff" else "")
    if change == "dirty": proof["clean_after"] = False
    if change == "full": proof["full_rerun"] = True
    if change == "source": proof["source_sha"] = "a" * 40
    if change == "definition": proof["definition_sha256"] = "a" * 64
    if change == "baseline": proof["baseline"]["payload"]["source_sha"] = "a" * 40
    if change == "recursive": proof["baseline"]["payload"]["receipt_version"] = 6
    if change == "deployment": proof["deployment"]["id"] += 1
    if change == "files": proof["changed_files"] = []
    if change == "extra-runtime": files.append("backend/src/services/storage_service.py"); proof["changed_files"] = files
    if change == "failed": proof["commands"][0]["exit_code"] = 1
    if change == "missing": proof["commands"].pop()
    if change == "command": proof["commands"][0]["command_sha256"] = "0" * 64
    if change == "log": proof["commands"][0]["log_sha256"] = "not-a-hash"
    if change == "bool-exit": proof["commands"][0]["exit_code"] = False
    before = deepcopy(proof)
    if change:
        with pytest.raises(ValueError): subject.validate(proof, "f" * 40, "d" * 64)
    else:
        subject.validate(proof, "f" * 40, "d" * 64)
        assert calls[0][1] == subject.BASE
    assert proof == before


def test_production_validator_accepts_scoped_proof_without_claiming_full_rerun(monkeypatch):
    import local_hospital_test_runner
    calls = []
    monkeypatch.setattr(subject, "validate", lambda *args: calls.append(args))
    proof = {"receipt_version": 6}
    local_hospital_test_runner.validate_receipt(proof, "f" * 40, "d" * 64, require_full=True)
    assert calls == [(proof, "f" * 40, "d" * 64)]


@pytest.mark.parametrize("field,value", [("head_sha", "wrong"), ("conclusion", "failure"),
    ("event", "push"), ("status", "in_progress"), ("path", "other.yml"),
    ("head_branch", "develop"), ("id", 1)])
def test_prior_github_run_must_be_successful_owner_production_dispatch(monkeypatch, field, value):
    import json
    run = {"id": subject.RUN, "head_sha": subject.BASE, "status": "completed", "conclusion": "success",
           "event": "workflow_dispatch", "head_branch": "release/prod-example",
           "path": ".github/workflows/deploy-prod.yml", "repository": {"full_name": subject.REPOSITORY},
           "actor": {"login": "mmorinag127"}}
    run[field] = value
    monkeypatch.setattr(subject.subprocess, "check_output", lambda *args, **kwargs: json.dumps(run))
    with pytest.raises(ValueError): subject.successful_baseline_run()
