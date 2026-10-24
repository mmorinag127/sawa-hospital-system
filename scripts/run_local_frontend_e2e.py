"""Run E2E against an isolated source copy; Next must not edit the checkout."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile

from local_hospital_test_runner import ROOT, clean_tree, git, reject_hosted_runner


def main():
    reject_hosted_runner()
    if not clean_tree():
        raise ValueError("E2E requires a fixed clean source checkout")
    source = git("rev-parse", "HEAD")
    base = os.environ.get("SAWA_LOCAL_EVIDENCE_DIR")
    evidence = Path(base if base else tempfile.mkdtemp(prefix="sawa-local-e2e-")) / "frontend-e2e"
    evidence.mkdir(exist_ok=False)
    workspace = evidence / "source/frontend"
    workspace.mkdir(parents=True, exist_ok=False)
    tracked = subprocess.check_output([
        "git", "ls-files", "-z", "--", "frontend",
    ], cwd=ROOT).decode().split("\0")
    hashes = {}
    for name in filter(None, tracked):
        original = ROOT / name
        copied = workspace / original.relative_to(ROOT / "frontend")
        if original.is_symlink() or not original.is_file():
            raise ValueError("E2E requires ordinary tracked source files: " + name)
        copied.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, copied)
        digest = hashlib.sha256(original.read_bytes()).hexdigest()
        if hashlib.sha256(copied.read_bytes()).hexdigest() != digest:
            raise ValueError("E2E source copy differs: " + name)
        hashes[name] = digest
    # Independent installed files keep Next's generated changes out of the source.
    shutil.copytree(ROOT / "frontend/node_modules", workspace / "node_modules", symlinks=True)
    for installed in (workspace / "node_modules").rglob("*"):
        if installed.is_symlink() and workspace not in installed.resolve(strict=True).parents:
            raise ValueError("E2E dependency link escapes its isolated copy")
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    env = {key: value for key, value in os.environ.items() if key in {
        "PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR",
        "NODE_OPTIONS", "PLAYWRIGHT_BROWSERS_PATH", "NO_COLOR", "FORCE_COLOR",
    }}
    env.update({"E2E_BASE_URL": f"http://127.0.0.1:{port}",
                "NEXT_PUBLIC_API_BASE_URL": "/api/hospital",
                "NEXT_PUBLIC_GIT_SHA": source, "NEXT_TELEMETRY_DISABLED": "1",
                "NODE_ENV": "development"})
    (evidence / "source.json").write_text(json.dumps({
        "source_sha": source, "files": hashes, "base_url": env["E2E_BASE_URL"],
        "workspace": str(workspace),
    }, indent=2))
    result = subprocess.run([
        "node", "node_modules/@playwright/test/cli.js", "test", *sys.argv[1:], "--workers=1",
    ], cwd=workspace, env=env)
    if not clean_tree() or git("rev-parse", "HEAD") != source:
        raise ValueError("E2E changed its fixed source checkout")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
