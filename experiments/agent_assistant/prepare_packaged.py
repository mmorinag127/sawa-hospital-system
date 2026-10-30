"""Migrate each image-owned baseline before sealing its release hash."""
import json
import os
from pathlib import Path
import subprocess

from .runtime import source_manifest


def prepare(root, python):
    root = Path(root).resolve()
    release_path = root / "release.json"
    release = json.loads(release_path.read_text())
    for domain, entry in release["domains"].items():
        baseline = root / "baselines" / domain
        if any(path.is_symlink() for path in baseline.rglob("*")):
            raise RuntimeError("Packaged baseline symlinks are forbidden")
        if source_manifest(baseline) != entry["baseline_files"]:
            raise RuntimeError("Packaged baseline differs from the declared source: " + domain)
        if domain not in {"hospital", "shift", "school-lunch"}:
            raise RuntimeError("Unsupported packaged domain: " + domain)
        marker = json.loads((baseline / "trial-manifest.json").read_text())
        if marker.get("purpose") != "sawa-agent-isolated-trial":
            raise RuntimeError("An image-owned trial baseline is required")
    for domain, entry in release["domains"].items():
        baseline = root / "baselines" / domain
        before = source_manifest(baseline)
        backend = root / "native" / domain / "backend"
        interpreter = python[domain] if isinstance(python, dict) else python
        env = {key: os.environ[key] for key in ("PATH", "LANG") if key in os.environ}
        env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(backend))
        marker = json.loads((baseline / 'trial-manifest.json').read_text())
        directories = [baseline]
        for task, relative in marker.get('task_baselines', {}).items():
            selected = (baseline / relative).resolve()
            if (domain != 'hospital' or task not in {'hospital_review_faxes', 'hospital_outputs'}
                    or not isinstance(relative, str) or Path(relative).name != relative
                    or not selected.is_relative_to(baseline) or selected == baseline):
                raise RuntimeError('Invalid packaged task baseline')
            directories.append(selected)
        for directory in directories:
            env['DATABASE_URL' if domain == 'shift' else 'ALEMBIC_DB_URI'] = "sqlite:///" + str(directory / "database.sqlite3")
            subprocess.run(
                [str(interpreter), "-m", "alembic", "-c", str(backend / "alembic.ini"), "upgrade", "head"],
                cwd=backend, env=env, check=True,
            )
        migrated = source_manifest(baseline)
        databases = {str((directory / 'database.sqlite3').relative_to(baseline)) for directory in directories}
        if ({key: value for key, value in migrated.items() if key not in databases}
                != {key: value for key, value in before.items() if key not in databases}):
            raise RuntimeError("Formal migration changed non-database trial sources: " + domain)
        entry["baseline_files"] = migrated
    release_path.write_text(json.dumps(release, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    prepare("/opt/sawa-agent", {domain: f"/opt/agent/{domain}/bin/python"
                              for domain in ("hospital", "shift", "school-lunch")})
