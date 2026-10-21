"""Read virtual-environment package metadata without executing their Python."""

import argparse
import importlib.metadata
import json
import os
from pathlib import Path


def inventory(root):
    records = []
    for directory, children, files in os.walk(root):
        children[:] = [name for name in children if name not in {".git", "node_modules", ".next", ".cache"}]
        if "pyvenv.cfg" not in files:
            continue
        environment = Path(directory)
        sites = list(environment.glob("lib/python*/site-packages"))
        versions = {}
        for site in sites:
            for package in importlib.metadata.distributions(path=[str(site)]):
                name = package.metadata.get("Name", "").lower()
                if name in {"pip", "certifi", "uv"}:
                    versions[name] = package.version
        records.append({"environment": str(environment), "packages": versions,
                        "execution_status": "not_inferred", "modified": False})
        children.clear()
    return records


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    records = inventory(args.root.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"environments": len(records), "output": str(args.output), "modified": 0}))


if __name__ == "__main__":
    main()
