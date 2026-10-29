"""Select staging deploy groups from a verified successful workflow ancestor."""
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.request


REPOSITORY = "mmorinag127/sawa-hospital-system"


def surfaces(paths):
    backend = frontend = False
    for path in paths:
        if path.startswith(("docs/", "tests/", "backend/tests/", "frontend/tests/", "frontend/e2e/",
                            "infra/ci/tests/", "scripts/run_local_", "scripts/test_local_")) or path in {
            "AGENTS.md", "scripts/local_hospital_test_runner.py", "scripts/local_hospital_test_manifest.json",
        }:
            continue
        if path.startswith(("frontend/", "agent_runtime/", "experiments/agent_assistant/")):
            frontend = True
        else:
            # Backend changes also affect the Hospital code packaged in the agent.
            backend = frontend = True
    return backend, frontend


def github(path):
    request = urllib.request.Request("https://api.github.com/repos/" + REPOSITORY + path,
        headers={"Authorization": "Bearer " + os.environ["GH_TOKEN"],
                 "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def previous_source(runs, jobs):
    if not runs:
        return None
    run = runs[0]
    source = run.get("head_sha", "")
    required = {"event": "workflow_dispatch", "head_branch": "develop", "status": "completed",
                "conclusion": "success", "path": ".github/workflows/deploy-stg.yml"}
    if (any(run.get(key) != value for key, value in required.items())
            or not re.fullmatch("[0-9a-f]{40}", source)
            or run.get("head_repository", {}).get("full_name") != REPOSITORY
            or jobs.get("source-gate") != "success"):
        raise ValueError("Verified normal staging deployment source is required")
    if jobs.get("recover-web") == "success":
        return None
    if jobs.get("recover-web") != "skipped":
        raise ValueError("Verified staging recovery status is required")
    return source


def main():
    if (os.environ.get("GITHUB_REPOSITORY") != REPOSITORY
            or os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
            or os.environ.get("GITHUB_REF") != "refs/heads/develop"):
        raise ValueError("Staging dispatch context is required")
    result = github("/actions/workflows/deploy-stg.yml/runs?branch=develop&event=workflow_dispatch&status=success&per_page=1")
    runs = result["workflow_runs"]
    jobs = github(f"/actions/runs/{int(runs[0]['id'])}/jobs?per_page=100")["jobs"] if runs else []
    source = previous_source(runs, {job["name"]: job["conclusion"] for job in jobs})
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    if head != os.environ.get("GITHUB_SHA"):
        raise ValueError("Staging checkout source mismatch")
    if source is None:
        selected = (True, True)
    else:
        subprocess.run(["git", "fetch", "origin", source], check=True, capture_output=True)
        subprocess.run(["git", "merge-base", "--is-ancestor", source, head], check=True)
        paths = subprocess.check_output(["git", "diff", "--name-only", "-z", source, head], text=True)
        selected = surfaces([path for path in paths.split("\0") if path])
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        for name, value in zip(("backend_changed", "frontend_changed"), selected):
            output.write(f"{name}={str(value).lower()}\n")
    print(json.dumps({"base_sha": source, "source_sha": head,
                      "backend_changed": selected[0], "frontend_changed": selected[1]}))


if __name__ == "__main__":
    main()
