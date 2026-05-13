from __future__ import annotations

import hashlib
import sys
from pathlib import Path


BASE_INPUTS = (
    "Dockerfile.base",
    "requirements.txt",
    "constraints-yomitoku-cpu.txt",
    "scripts/check_no_gpu_packages.py",
    "scripts/prewarm_yomitoku_models.py",
)


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    root = root.resolve()
    digest = hashlib.sha256()
    missing: list[str] = []
    for relative in BASE_INPUTS:
        path = root / relative
        if not path.is_file():
            missing.append(relative)
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    if missing:
        print(f"missing backend base fingerprint inputs: {', '.join(missing)}", file=sys.stderr)
        return 2
    print(digest.hexdigest())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
