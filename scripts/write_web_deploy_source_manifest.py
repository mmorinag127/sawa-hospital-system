#!/usr/bin/env python3
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys


SENTINELS = [
    "src/pages/orders/index.tsx",
    "src/pages/orders/[id].tsx",
    "src/pages/orders/[id]/workflow-v2.tsx",
    "src/pages/orders/[id]/inspection-v2.tsx",
    "src/pages/facility-master.tsx",
    "src/pages/index.tsx",
]


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    source_dir, deploy_dir, manifest_path, label = sys.argv[1:5]
    entries = []
    for rel_path in SENTINELS:
        src_path = os.path.join(source_dir, rel_path)
        dst_path = os.path.join(deploy_dir, rel_path)
        if not os.path.exists(src_path):
            continue
        if not os.path.exists(dst_path):
            raise SystemExit(f"deploy source missing sentinel file: {rel_path}")
        entries.append(
            {
                "path": rel_path,
                "source_sha256": sha256(src_path),
                "deploy_sha256": sha256(dst_path),
            }
        )

    git_head = ""
    try:
        git_head = subprocess.check_output(
            ["git", "-C", os.path.dirname(source_dir), "rev-parse", "HEAD"],
            text=True,
        ).strip()
    except Exception:
        git_head = ""

    payload = {
        "version": 1,
        "label": label,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source_frontend_dir": source_dir,
        "deploy_frontend_dir": deploy_dir,
        "source_git_head": git_head,
        "sentinels": entries,
    }

    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
