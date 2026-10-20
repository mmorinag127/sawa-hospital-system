"""Create empty, migrated trial baselines; never read or migrate an existing DB.

Invoke explicitly with --initialize. It does not seed PDFs, facilities or quantities.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--initialize", action="store_true", required=True)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[2]
    parent = project.parent
    targets = {
        "hospital": (project, project / "backend/.venv/bin/python"),
        "shift": (parent / "agent-assistant-shift-trial-20261019", parent / "agent-assistant-shift-trial-20261019/.venv/bin/python"),
        "school-lunch": (parent / "agent-assistant-lunch-trial-20261019", parent / "agent-assistant-lunch-trial-20261019/.venv/bin/python"),
    }
    root = project / "tmp" / "agent-assistant-trial"
    domains = {}
    for domain, (source, python) in targets.items():
        branch = subprocess.check_output(["git", "-C", str(source), "branch", "--show-current"], text=True).strip()
        if branch != "codex/agent-assistant-trial-20261019":
            raise RuntimeError("trial named branch required")
        directory = root / "baselines" / domain
        directory.mkdir(parents=True, exist_ok=False)
        uri = "sqlite:///"+str(directory / "database.sqlite3")
        env = {k: os.environ[k] for k in ("PATH", "LANG") if k in os.environ}
        env.update(DB_URI=uri, DATABASE_URL=uri, ALEMBIC_DB_URI=uri, PYTHONDONTWRITEBYTECODE="1")
        subprocess.run([str(python), "-m", "alembic", "-c", str(source / "backend" / "alembic.ini"), "upgrade", "head"],
                       cwd=source / "backend", env=env, check=True)
        (directory / "trial-manifest.json").write_text(json.dumps({"purpose": "sawa-agent-isolated-trial", "domain": domain,
            "source_sha": subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip(), "data": "empty_schema_no_real_order_test"}))
        domains[domain] = {"worktree": str(source), "python": str(python), "baseline": str(directory), "env": {}}
        if domain == "hospital":
            # An explicitly empty trial master, not an inferred real facility.
            (directory / "facility-master.json").write_text('{"facilities": []}\n')
            domains[domain]["facility_master_file"] = "facility-master.json"
    target = root / "config.json"
    with target.open("x") as output:
        json.dump({"domains": domains}, output, ensure_ascii=False, indent=2)
    print(str(target))


if __name__ == "__main__":
    main()
