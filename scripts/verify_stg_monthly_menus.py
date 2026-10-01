"""Verify archived originals via staging's normal monthly-menu operator API."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import urllib.error
import urllib.request
import uuid

BASE = "https://web-stg-avlnzjjrca-dt.a.run.app/api"
BUCKET = "gs://sawahospitalsystem-stg-raw/"


def request(path, body=None, content_type=None):
    headers = {"Authorization": "Bearer " + os.environ["VERIFICATION_TOKEN"]}
    if content_type:
        headers["Content-Type"] = content_type
    with urllib.request.urlopen(urllib.request.Request(BASE + path, body, headers), timeout=180) as response:
        return json.load(response)


def download(uri):
    if not uri.startswith(BUCKET):
        raise ValueError("Only staging fixture objects are permitted")
    return subprocess.run(["gcloud", "storage", "cat", uri], capture_output=True, check=True).stdout


def upload(case, raw, resolutions):
    boundary = uuid.uuid4().hex
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="review_resolutions"\r\n\r\n'
        + json.dumps(resolutions, ensure_ascii=False)
        + f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="original.xlsm"\r\n'
        + 'Content-Type: application/vnd.ms-excel.sheet.macroEnabled.12\r\n\r\n'
    ).encode() + raw + f"\r\n--{boundary}--\r\n".encode()
    return request("/monthly-menus?month_id=" + case["month"], body,
                   "multipart/form-data; boundary=" + boundary)


def validate(payload, masters, expected):
    issues = {issue["item_id"]: issue for issue in payload["master_checks"]["issues"]}
    matched = set()
    checked = 0
    for item in payload["items"]:
        master = masters.get(item.get("menu_master_id"))
        if master is not None:
            for field in ("unit_type", "qty_per_serving"):
                assert item.get(field) == master.get(field), (item["name"], field, item.get(field), master.get(field))
            assert not any(diff["field"] in ("unit_type", "qty_per_serving")
                           for diff in issues.get(item["id"], {}).get("field_diffs", [])), item
            checked += 1
        if item["name"] in expected:
            assert [item.get("unit_type"), item.get("qty_per_serving")] == expected[item["name"]], item
            matched.add(item["name"])
    assert matched == set(expected), (matched, expected)
    assert checked > 0
    return {"checked_existing_items": checked, "remaining_issues": len(issues)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(download(args.manifest))
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    summaries = []
    for case in manifest["cases"]:
        assert case["quantity_fields_absent_verified"] is True
        assert case["month"] in ("2026-09", "2026-10", "2026-11", "2026-12")
        folder = args.output_dir / case["month"]
        folder.mkdir(exist_ok=True)
        raw = download(case["uri"])
        assert hashlib.sha256(raw).hexdigest() == case["sha256"]
        if case.get("read_only_baseline"):
            baseline = request("/monthly-menus/" + case["month"])
            (folder / "baseline.json").write_text(json.dumps(baseline, ensure_ascii=False, indent=2))
            summaries.append({"month": case["month"], "mode": "read_only_existing_baseline",
                              "not_a_fixed_upload_verification": True,
                              "existing_issues": baseline["master_checks"]["count"]})
            continue
        try:
            request("/monthly-menus/" + case["month"])
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
        else:
            raise RuntimeError("Existing month must not be overwritten: " + case["month"])
        before = {item["id"]: item for item in request("/menu-masters?limit=10000")["items"]}
        (folder / "masters-before.json").write_text(json.dumps(before, ensure_ascii=False, indent=2))
        try:
            uploaded = upload(case, raw, [])
        except urllib.error.HTTPError as error:
            detail = json.load(error)
            (folder / "review-required.json").write_text(json.dumps(detail, ensure_ascii=False, indent=2))
            if error.code != 409 or detail["detail"]["code"] != "menu_master_review_required":
                raise
            resolutions = []
            for issue in detail["detail"]["issues"]:
                master = manifest["production_masters"][issue["normalized_name"]]
                assert issue["source_name"] == master["name"], issue
                assert not any(item["normalized_name"] == issue["normalized_name"] for item in before.values()), issue
                resolutions.append({**master, "source_name": issue["source_name"], "action": "create"})
            uploaded = upload(case, raw, resolutions)
        assert uploaded["created"] and uploaded["replaced"] is False, uploaded
        (folder / "upload.json").write_text(json.dumps(uploaded, ensure_ascii=False, indent=2))
        payload = request("/monthly-menus/" + case["month"])
        (folder / "menu.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        assert len(payload["entries"]) == case["expected_entries"]
        after = {item["id"]: item for item in request("/menu-masters?limit=10000")["items"]}
        summary = validate(payload, after, case["expected_quantities"])
        reloaded = request("/monthly-menus/" + case["month"])
        assert reloaded == payload, "Reload changed the menu response"
        assert all(after[key] == value for key, value in before.items()), "Existing master was modified"
        summaries.append({"month": case["month"], "sha256": case["sha256"], **summary})
        (args.output_dir / "summary.json").write_text(json.dumps({
            "commit": os.environ.get("GITHUB_SHA"), "cases": summaries,
        }, ensure_ascii=False, indent=2))
        print(json.dumps(summaries[-1], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
