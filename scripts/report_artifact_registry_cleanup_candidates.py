#!/usr/bin/env python3
"""Report safe-ish Artifact Registry cleanup candidates for prod services.

This script does not delete anything. It inspects:
- current Cloud Run prod services and their active image digests
- Artifact Registry image versions/tags for backend/frontend/ocr-pipeline

It then emits a JSON report with:
- digests currently referenced by Cloud Run
- recent versions to keep
- untagged / old tagged candidate digests

Usage:
  python workspace/scripts/report_artifact_registry_cleanup_candidates.py
  python workspace/scripts/report_artifact_registry_cleanup_candidates.py \
      --output /tmp/artifact_cleanup_report.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


PROJECT = "sawahospitalsystem"
LOCATION = "asia-northeast2"
REPOSITORY = "backend"
PACKAGES = ["backend", "frontend", "ocr-pipeline"]
SERVICES = {
    "web-prod": "frontend",
    "worker-prod": "backend",
    "ocr-pipeline-prod": "ocr-pipeline",
}


def run_json(cmd: list[str]) -> Any:
    return json.loads(subprocess.check_output(cmd, text=True))


def run_text(cmd: list[str]) -> str:
    return subprocess.check_output(cmd, text=True)


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


@dataclass
class VersionRecord:
    package: str
    version: str
    tags: list[str]
    update_time: datetime | None
    size_bytes: int | None


def get_service_image_map() -> dict[str, str]:
    result: dict[str, str] = {}
    for service, package in SERVICES.items():
        data = run_json(
            [
                "gcloud",
                "run",
                "services",
                "describe",
                service,
                "--region",
                LOCATION,
                "--format=json",
            ]
        )
        image = data["spec"]["template"]["spec"]["containers"][0]["image"]
        result[service] = image
    return result


def get_package_versions(package: str) -> list[VersionRecord]:
    path = f"{LOCATION}-docker.pkg.dev/{PROJECT}/{REPOSITORY}/{package}"
    data = run_json(
        [
            "gcloud",
            "artifacts",
            "docker",
            "images",
            "list",
            path,
            "--include-tags",
            "--format=json",
        ]
    )
    records: list[VersionRecord] = []
    for row in data:
        size_bytes = row.get("imageSizeBytes")
        try:
            size_value = int(size_bytes) if size_bytes is not None else None
        except (TypeError, ValueError):
            size_value = None
        records.append(
            VersionRecord(
                package=package,
                version=row["version"],
                tags=row.get("tags", []),
                update_time=parse_time(row.get("updateTime")),
                size_bytes=size_value,
            )
        )
    records.sort(key=lambda x: x.update_time or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return records


def summarize(
    service_images: dict[str, str],
    package_versions: dict[str, list[VersionRecord]],
    recent_keep: int,
    old_days: int,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    old_cutoff = now - timedelta(days=old_days)
    referenced_digests = {
        service: image.split("@", 1)[1] if "@" in image else image.rsplit(":", 1)[-1]
        for service, image in service_images.items()
    }
    keep_by_package: dict[str, set[str]] = defaultdict(set)
    for service, package in SERVICES.items():
        keep_by_package[package].add(referenced_digests[service])

    report: dict[str, Any] = {
        "generated_at": now.isoformat(),
        "project": PROJECT,
        "location": LOCATION,
        "repository": REPOSITORY,
        "recent_keep": recent_keep,
        "old_days": old_days,
        "services": service_images,
        "packages": {},
    }

    for package, versions in package_versions.items():
        recent = versions[:recent_keep]
        for item in recent:
            keep_by_package[package].add(item.version)

        package_report = {
            "total_versions": len(versions),
            "referenced_digests": sorted(keep_by_package[package]),
            "recent_keep_versions": [v.version for v in recent],
            "untagged_candidates": [],
            "old_tagged_candidates": [],
            "keep_versions": sorted(keep_by_package[package]),
        }

        for item in versions:
            if item.version in keep_by_package[package]:
                continue
            candidate = {
                "version": item.version,
                "tags": item.tags,
                "update_time": item.update_time.isoformat() if item.update_time else None,
                "size_bytes": item.size_bytes,
            }
            if not item.tags:
                package_report["untagged_candidates"].append(candidate)
            elif item.update_time and item.update_time < old_cutoff:
                package_report["old_tagged_candidates"].append(candidate)

        report["packages"][package] = package_report

    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="", help="Optional output JSON path")
    parser.add_argument("--recent-keep", type=int, default=15)
    parser.add_argument("--old-days", type=int, default=45)
    args = parser.parse_args()

    service_images = get_service_image_map()
    package_versions = {package: get_package_versions(package) for package in PACKAGES}
    report = summarize(service_images, package_versions, args.recent_keep, args.old_days)

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        print(path)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
