"""Migrate the image-owned hospital baseline before sealing its release hash."""
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
    baseline = root / "baselines" / "hospital"
    marker = json.loads((baseline / "trial-manifest.json").read_text())
    if marker.get("purpose") != "sawa-agent-isolated-trial":
        raise RuntimeError("An image-owned trial baseline is required")
    backend = root / "native" / "hospital" / "backend"
    env = {key: os.environ[key] for key in ("PATH", "LANG") if key in os.environ}
    env.update(
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONPATH=str(backend),
        ALEMBIC_DB_URI="sqlite:///" + str(baseline / "database.sqlite3"),
    )
    subprocess.run(
        [str(python), "-m", "alembic", "-c", str(backend / "alembic.ini"), "upgrade", "head"],
        cwd=backend, env=env, check=True,
    )
    migrated = source_manifest(baseline)
    original = release["domains"]["hospital"]["baseline_files"]
    if ({key: value for key, value in migrated.items() if key != "database.sqlite3"}
            != {key: value for key, value in original.items() if key != "database.sqlite3"}):
        raise RuntimeError("Formal migration changed non-database trial sources")
    release["domains"]["hospital"]["baseline_files"] = migrated
    release_path.write_text(json.dumps(release, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    prepare("/opt/sawa-agent", "/opt/agent/hospital/bin/python")
