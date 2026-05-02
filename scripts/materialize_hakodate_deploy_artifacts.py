#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: materialize_hakodate_deploy_artifacts.py WORKSPACE_DIR DEST_BACKEND_DIR MANIFEST", file=sys.stderr)
        return 2
    workspace = Path(sys.argv[1])
    dest_backend = Path(sys.argv[2])
    manifest = Path(sys.argv[3])
    items = json.loads(manifest.read_text(encoding="utf-8")).get("results", [])
    local_prefix = str(workspace) + "/"
    deploy_anchor = Path("tmp/outer_quad_eval_correct_20260426")

    for item in items:
        if not isinstance(item, dict):
            continue
        for key in ("fax_pdf", "template_pdf", "step2_png"):
            raw = str(item.get(key) or "")
            if not raw:
                continue
            src = Path(raw)
            if not src.exists() and raw.startswith(local_prefix):
                src = workspace / raw[len(local_prefix):]
            if not src.exists():
                print(f"required Hakodate artifact missing: {key}={raw}", file=sys.stderr)
                return 1
            if raw.startswith(local_prefix):
                rel = Path(raw[len(local_prefix):])
            else:
                parts = src.parts
                anchor_parts = deploy_anchor.parts
                anchor_index = None
                for idx in range(0, len(parts) - len(anchor_parts) + 1):
                    if parts[idx : idx + len(anchor_parts)] == anchor_parts:
                        anchor_index = idx
                        break
                if anchor_index is None:
                    rel = src.relative_to(workspace)
                else:
                    rel = Path(*parts[anchor_index:])
            dst = dest_backend / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
