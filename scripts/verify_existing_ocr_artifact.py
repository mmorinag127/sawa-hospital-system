"""Read-only GitHub provenance gate for the fixed staging OCR artifact."""
import json
import re
import subprocess
import sys
from datetime import datetime

REPOSITORY = "mmorinag127/sawa-hospital-system"
OCR_STEP = "Verify explicitly requested staging OCR uploads"


def validate(run_id, run, jobs, artifacts):
    if not re.fullmatch(r"[0-9]+", run_id):
        raise ValueError("invalid_run_id")
    if (run.get("id") != int(run_id)
        or run.get("repository", {}).get("full_name") != REPOSITORY
        or run.get("head_repository", {}).get("full_name") != REPOSITORY
        or run.get("path") != ".github/workflows/deploy-stg.yml"
        or run.get("head_branch") != "develop"
        or run.get("event") != "workflow_dispatch"):
        raise ValueError("invalid_run_source")
    matches = [job for job in jobs if job.get("name") == "deploy-backend"]
    if len(matches) != 1:
        raise ValueError("backend_job_not_unique")
    job = matches[0]
    steps = [step for step in job.get("steps", []) if step.get("name") == OCR_STEP]
    if (job.get("run_id") != int(run_id) or job.get("status") != "completed"
        or job.get("conclusion") != "success" or len(steps) != 1
        or steps[0].get("conclusion") != "success"):
        raise ValueError("backend_ocr_not_successful")
    matches = [item for item in artifacts if item.get("name") == "live-ocr-stg"]
    if len(matches) != 1:
        raise ValueError("artifact_not_unique")
    artifact = matches[0]
    if (artifact.get("expired") is not False
        or artifact.get("workflow_run", {}).get("id") != int(run_id)
        or artifact.get("workflow_run", {}).get("head_sha") != run.get("head_sha")):
        raise ValueError("invalid_artifact")
    date = lambda value: datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not date(job["started_at"]) <= date(artifact["created_at"]) <= date(job["completed_at"]):
        raise ValueError("artifact_attempt_mismatch")


def api(path, collection=None):
    command = ["gh", "api", f"repos/{REPOSITORY}/{path}"]
    if collection:
        command += ["--paginate", "--slurp"]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise ValueError("github_read_failed")
    data = json.loads(result.stdout)
    return [item for page in data for item in page[collection]] if collection else data


def main(run_id):
    if not re.fullmatch(r"[0-9]+", run_id):
        raise ValueError("invalid_run_id")
    run = api(f"actions/runs/{run_id}")
    attempt = run.get("run_attempt")
    if type(attempt) is not int or attempt < 1:
        raise ValueError("invalid_run_attempt")
    jobs = api(f"actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100", "jobs")
    artifacts = api(f"actions/runs/{run_id}/artifacts?per_page=100", "artifacts")
    validate(run_id, run, jobs, artifacts)


if __name__ == "__main__":
    try:
        main(sys.argv[1])
    except Exception:
        print("existing_ocr_artifact_validation_failed", file=sys.stderr)
        sys.exit(1)
