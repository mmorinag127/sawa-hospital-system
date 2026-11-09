"""Owner-approved, source-bound FAX change proof, not a claimed full rerun."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from local_receipt_signing import sign, verify_signed
from production_deploy_supplement import ROOT, git

BASE = "419046eb02d6c7e758da897fd28bec3656809ca0"
RUN = 42460685431
REPOSITORY = "mmorinag127/sawa-hospital-system"
ALLOWED = {
    "backend/src/api/orders.py", "backend/src/services/order_workflow_v2_service.py",
    "frontend/src/pages/orders/[id]/workflow-v2.tsx", "frontend/src/features/orders/workflowTypes.ts",
    "backend/tests/unit/test_h2_quad_geometry_roundtrip.py",
    "backend/tests/unit/test_production_change_receipt.py",
    "frontend/tests/h2-quad-owner.test.cjs", "frontend/tests/fax-orientation-template.test.cjs",
    "scripts/production_change_receipt.py", "scripts/local_hospital_test_runner.py",
    ".github/workflows/deploy-stg.yml", ".github/workflows/deploy-prod.yml",
    "docs/fax-orientation-template-20261108.md",
    "backend/src/services/template_field_schema_service.py", "backend/src/services/order_service.py",
    "backend/src/services/hakodate_fixed_quad_registration_service.py",
    "backend/src/services/workflow_attempt_guard_service.py", "backend/src/services/workflow_ocr_delivery_service.py",
    "backend/tests/unit/test_grouped_quantity_fields.py", "agent_runtime/manifests/shift.json",
    "backend/tests/unit/test_fax_run_orientation.py",
    "backend/src/services/hakodate_step_review_pipeline_service.py",
    "backend/tests/unit/test_row_ruling_continuity.py",
    "backend/src/services/workflow_display_read_service.py",
    "backend/tests/unit/test_page_performance_projections.py",
    "backend/tests/unit/test_header_topology_alignment.py",
    *{"agent_runtime/native/shift/backend/shift_scheduler_backend/" + name for name in (
        "main.py", "schedule_selection.py", "operational_constraints.py", "solver.py", "candidate_validation.py")},
}
PYTEST = ["backend/tests/unit/" + name for name in (
    "test_h2_quad_geometry_roundtrip.py", "test_hakodate_fixed_quad_registration_service.py",
    "test_order_facility_read.py", "test_initial_saved_facility_read.py",
    "test_order_workflow_v2_service.py", "test_production_change_receipt.py",
    "test_production_corrective_receipt.py", "test_grouped_quantity_fields.py", "test_fax_run_orientation.py",
    "test_workflow_ocr_delivery_runner_contract.py")]
PYTEST += ["backend/tests/unit/" + name for name in (
    "test_row_ruling_continuity.py", "test_modernization_printed_header_boundaries.py",
    "test_modernization_semantic_day_boundaries.py")]
PYTEST += ["backend/tests/contract/test_saved_sheet_quantity_schema.py"]
PYTEST += ["backend/tests/unit/test_page_performance_projections.py"]
PYTEST += ["backend/tests/unit/test_header_topology_alignment.py"]
COMMANDS = [
    ("backend", ["python3", "-m", "pytest", *PYTEST, "-q"], "."),
    ("frontend", ["node", "--test", "--test-concurrency=1",
                  "tests/fax-orientation-template.test.cjs", "tests/order-facility-read.test.cjs",
                  "tests/h2-quad-owner.test.cjs"], "frontend"),
    ("types", ["node", "node_modules/typescript/bin/tsc", "--noEmit", "--incremental", "false"], "frontend"),
    ("native-shift", ["python3", "-m", "pytest",
                      *["experiments/agent_assistant/tests/" + name for name in (
                          "test_shift_review.py", "test_shift_read_scopes.py",
                          "test_shift_confirmation_receipt.py", "test_shift_business_questions.py")], "-q"], "."),
]


def digest(value):
    return hashlib.sha256(value).hexdigest()


def require(value, message):
    if not value:
        raise ValueError(message)


def successful_baseline_run():
    value = json.loads(subprocess.check_output([
        "gh", "api", f"repos/{REPOSITORY}/actions/runs/{RUN}"], text=True))
    require(value.get("id") == RUN and value.get("head_sha") == BASE
            and value.get("status") == "completed" and value.get("conclusion") == "success"
            and value.get("event") == "workflow_dispatch"
            and value.get("head_branch", "").startswith("release/prod-")
            and value.get("path") == ".github/workflows/deploy-prod.yml"
            and value.get("repository", {}).get("full_name") == REPOSITORY
            and value.get("actor", {}).get("login") == REPOSITORY.split("/")[0],
            "baseline must be the exact successful owner production deployment")
    return {"id": RUN, "source_sha": BASE, "repository": REPOSITORY}


def command_hash(argv, directory):
    return digest(json.dumps([argv, directory]).encode())


def validate(proof, expected_sha, definition):
    from local_hospital_test_runner import validate_receipt
    require(set(proof) == {"receipt_version", "source_sha", "definition_sha256", "baseline",
                           "deployment", "changed_files", "commands", "clean_before", "clean_after",
                           "full_rerun"}, "change proof fields mismatch")
    require(type(proof["receipt_version"]) is int and proof["receipt_version"] == 6
            and proof["source_sha"] == expected_sha and proof["definition_sha256"] == definition
            and proof["clean_before"] is True and proof["clean_after"] is True
            and proof["full_rerun"] is False, "change proof source/meaning mismatch")
    baseline = verify_signed(proof["baseline"])
    require(baseline.get("receipt_version") == 5 and baseline.get("source_sha") == BASE,
            "exact prior signed baseline required; recursive change proofs forbidden")
    validate_receipt(baseline, BASE, definition, require_full=True)
    require(proof["deployment"] == successful_baseline_run(), "baseline deployment mismatch")
    git("merge-base", "--is-ancestor", BASE, expected_sha)
    changed = git("diff", "--name-only", BASE, expected_sha).splitlines()
    require(changed and changed == proof["changed_files"] and not set(changed) - ALLOWED,
            "unreviewed dependency/migration/runtime change")
    require(isinstance(proof["commands"], list) and len(proof["commands"]) == len(COMMANDS),
            "every related command is required")
    for record, (name, argv, directory) in zip(proof["commands"], COMMANDS, strict=True):
        require(set(record) == {"name", "command_sha256", "log_sha256", "exit_code"}
                and record["name"] == name and record["command_sha256"] == command_hash(argv, directory)
                and type(record["exit_code"]) is int and record["exit_code"] == 0
                and isinstance(record["log_sha256"], str) and len(record["log_sha256"]) == 64
                and all(c in "0123456789abcdef" for c in record["log_sha256"]),
                "exact successful related execution required")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(not os.environ.get("GITHUB_ACTIONS") and not os.environ.get("CI")
            and not git("status", "--porcelain"), "clean local source required")
    source = git("rev-parse", "HEAD")
    definition = digest((ROOT / "scripts/local_hospital_test_manifest.json").read_bytes())
    proof = {"receipt_version": 6, "source_sha": source, "definition_sha256": definition,
             "baseline": json.loads(args.baseline.read_text()), "deployment": successful_baseline_run(),
             "changed_files": git("diff", "--name-only", BASE, source).splitlines(),
             "commands": [], "clean_before": True, "clean_after": True, "full_rerun": False}
    args.output.mkdir(parents=True, exist_ok=False)
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join(str(ROOT / item) for item in
                         ("backend", "backend/tests/unit", "scripts", ".")),
                       DB_URI="sqlite:///:memory:", PYTHONDONTWRITEBYTECODE="1")
    for name, argv, directory in COMMANDS:
        log = args.output / f"{name}.log"
        with log.open("wb") as stream:
            result = subprocess.run(argv, cwd=ROOT / directory, env=environment,
                                    stdout=stream, stderr=subprocess.STDOUT)
        proof["commands"].append({"name": name, "command_sha256": command_hash(argv, directory),
                                  "log_sha256": digest(log.read_bytes()), "exit_code": result.returncode})
    require(not git("status", "--porcelain") and git("rev-parse", "HEAD") == source,
            "source changed during verification")
    (args.output / "execution.json").write_text(json.dumps(proof, indent=2) + "\n")
    validate(proof, source, definition)
    (args.output / "receipt.json").write_text(json.dumps(sign(proof), indent=2) + "\n")
    print("Signed exact-source change proof issued; no full rerun claimed")


if __name__ == "__main__":
    main()
