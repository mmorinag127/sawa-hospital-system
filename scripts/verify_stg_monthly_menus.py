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
from urllib.parse import urlencode

BASE = "https://web-stg-avlnzjjrca-dt.a.run.app/api"
BUCKET = "gs://sawahospitalsystem-stg-raw/"


def request(path, body=None, content_type=None, *, revision=None):
    headers = {"Authorization": "Bearer " + os.environ["VERIFICATION_TOKEN"]}
    if content_type:
        headers["Content-Type"] = content_type
    if revision is not None:
        headers["If-Match"] = revision
    with urllib.request.urlopen(urllib.request.Request(BASE + path, body, headers), timeout=180) as response:
        return json.load(response)


def download(uri):
    if not uri.startswith(BUCKET):
        raise ValueError("Only staging fixture objects are permitted")
    return subprocess.run(["gcloud", "storage", "cat", uri], capture_output=True, check=True).stdout


def authored_selection(source):
    if not isinstance(source, dict) or not {"scope_override", "diet_types"}.issubset(source):
        raise RuntimeError("Authored scope_override and diet_types are required; absence is not null")
    scope, diets = source["scope_override"], source["diet_types"]
    if scope is not None and (not isinstance(scope, str) or not scope.strip()):
        raise RuntimeError("Authored scope is unresolved")
    if diets is not None and (not isinstance(diets, list) or not diets
        or any(not isinstance(value, str) or not value.strip() for value in diets)):
        raise RuntimeError("Authored food types are unresolved")
    return scope, diets


def upload(case, raw):
    scope, diets = authored_selection(case)
    boundary = uuid.uuid4().hex
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="original.xlsm"\r\n'
        + 'Content-Type: application/vnd.ms-excel.sheet.macroEnabled.12\r\n\r\n'
    ).encode() + raw + f"\r\n--{boundary}--\r\n".encode()
    params = {"month_id": case["month"]}
    if case.get("sheet_name"):
        params["sheet_name"] = case["sheet_name"]
    if diets is not None:
        params["diet_types"] = json.dumps(diets, ensure_ascii=False)
    if scope is not None:
        if not isinstance(scope, str) or not scope:
            raise ValueError("explicit source scope is unresolved")
        params.update({"scope_type": "tag" if scope.startswith("TAG:") else "facility",
                       "scope_value": scope[4:] if scope.startswith("TAG:") else scope})
    return request("/monthly-menus?" + urlencode(params), body,
                   "multipart/form-data; boundary=" + boundary)


def validate_receipt(receipt, *, month, filename, digest, scope, diet_types, sheet_name=None, import_id=None):
    authored_selection({"scope_override": scope, "diet_types": diet_types})
    source = receipt.get("source")
    source_keys = {"filename", "file_uri", "content_sha256", "sheet_name", "media_type", "scope_override", "diet_types"}
    if (receipt.get("month_id") != month or not isinstance(receipt.get("id"), str) or not receipt["id"]
        or receipt.get("state") != "review" or not isinstance(source, dict)
        or not source_keys.issubset(source)
        or any(not isinstance(source[key], str) or not source[key].strip() for key in ("filename", "file_uri", "sheet_name", "media_type"))
        or (source["scope_override"] is not None and (not isinstance(source["scope_override"], str) or not source["scope_override"].strip()))
        or (source["diet_types"] is not None and (not isinstance(source["diet_types"], list) or not source["diet_types"]
            or any(not isinstance(value, str) or not value.strip() for value in source["diet_types"])))
        or (import_id is not None and receipt["id"] != import_id)
        or source.get("filename") != Path(filename).name or source.get("content_sha256") != digest
        or "scope_override" not in source or source["scope_override"] != scope
        or source["diet_types"] != diet_types
        or not source.get("file_uri") or not isinstance(source.get("sheet_name"), str) or not source["sheet_name"]
        or (sheet_name is not None and source["sheet_name"] != sheet_name)):
        raise RuntimeError("Saved import does not match the exact original/month/scope; no apply")
    review_material(receipt)


def validate_review(receipt, review):
    required = {"issues", "master_diffs", "resolutions", "plans", "items", "entries", "item_count", "scope_override", "input_snapshot"}
    if (not isinstance(review, dict) or not required.issubset(review)
        or any(not isinstance(review[key], list) or any(not isinstance(value, dict) for value in review[key])
               for key in ("issues", "master_diffs", "resolutions", "items", "entries"))
        or not isinstance(review["plans"], dict) or any(not isinstance(value, dict) for value in review["plans"].values())
        or type(review["item_count"]) is not int or review["item_count"] < 0
        or review["scope_override"] != receipt["source"]["scope_override"]
        or not isinstance(review["input_snapshot"], dict)
        or not {"revision", "digest"}.issubset(review["input_snapshot"])
        or type(review["input_snapshot"]["revision"]) is not int or review["input_snapshot"]["revision"] <= 0
        or not isinstance(review["input_snapshot"]["digest"], str) or len(review["input_snapshot"]["digest"]) != 64
        or any(value not in "0123456789abcdef" for value in review["input_snapshot"]["digest"])
        or not isinstance(receipt.get("revision"), str) or not receipt["revision"]
        or not isinstance(receipt.get("current_input_revision"), str) or not receipt["current_input_revision"]):
        raise RuntimeError("Saved review/input is unresolved; no apply")


def review_material(receipt):
    validate_review(receipt, receipt.get("review"))
    review = receipt.get("current_review")
    validate_review(receipt, review)
    return {"receipt_id": receipt["id"], "month_id": receipt["month_id"], "source": receipt["source"],
            "revision": receipt["revision"], "input_revision": receipt["current_input_revision"], "current_review": review}


def apply_saved_import(receipt, approval, send):
    material = review_material(receipt)
    if (receipt.get("state") != "review" or not isinstance(approval, dict)
        or any(approval.get(key) != value for key, value in material.items())
        or not isinstance(approval.get("resolutions"), list)):
        raise RuntimeError("Explicit operator review of this exact receipt/input is required; saved source retained")
    path = f"/monthly-menus/{receipt['month_id']}/imports/{receipt['id']}"
    resolutions = approval["resolutions"]
    for issue in material["current_review"]["issues"] + material["current_review"]["master_diffs"]:
        name = issue.get("source_name") if isinstance(issue, dict) else None
        choices = [choice for choice in resolutions if isinstance(choice, dict) and choice.get("source_name") == name]
        if not name or len(choices) != 1 or choices[0].get("action") not in {"existing", "create", "update"}:
            raise RuntimeError("Unresolved difference requires one explicit operator resolution; no apply")
    own_review = send(path + "/review", json.dumps({"resolutions": resolutions,
        "input_revision": material["input_revision"]}, ensure_ascii=False).encode(), "application/json",
        revision=material["revision"])
    if (own_review.get("id") != receipt["id"] or own_review.get("month_id") != receipt["month_id"]
        or own_review.get("source") != receipt["source"] or own_review.get("state") != "review"
        or own_review.get("input_revision") != material["input_revision"]
        or own_review.get("current_input_revision") != material["input_revision"]
        or own_review.get("review", {}).get("resolutions") != resolutions
        or own_review.get("current_review", {}).get("issues") != []):
        raise RuntimeError("Own saved review remains unresolved; no apply")
    own_material = review_material(own_review)
    result = send(path + "/apply", b"", revision=own_material["revision"])
    own = result.get("receipt")
    snapshot = result.get("snapshot")
    if (result.get("created") is not True or not isinstance(own, dict) or own.get("state") != "applied"
        or own.get("id") != receipt["id"] or own.get("month_id") != receipt["month_id"]
        or own.get("source") != receipt["source"] or not isinstance(snapshot, dict)
        or snapshot.get("source_month_id") != receipt["month_id"]
        or not isinstance(snapshot.get("menu"), dict) or snapshot["menu"].get("id") != receipt["month_id"]
        or not isinstance(snapshot.get("items"), list) or not isinstance(snapshot.get("entries"), list)
        or not isinstance(snapshot.get("revision"), str) or not snapshot["revision"]):
        raise RuntimeError("Applied own result is unresolved; saved upload is not an applied result")
    return result


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


def canonical_snapshot(payload):
    normalized = dict(payload)
    normalized["items"] = sorted(payload["items"], key=lambda item: item["id"])
    return json.dumps(normalized, sort_keys=True, ensure_ascii=False)


def verify_replace_snapshot(payload, expected_sha256):
    actual = hashlib.sha256(canonical_snapshot(payload).encode()).hexdigest()
    if not expected_sha256 or actual != expected_sha256:
        raise RuntimeError("Existing month differs from the explicitly authorized test snapshot")


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
        scope, diets = authored_selection(case)
        replacing = False
        try:
            current = request("/monthly-menus/" + case["month"])
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
        else:
            (folder / "before-replace.json").write_text(json.dumps(current, ensure_ascii=False, indent=2))
            verify_replace_snapshot(current, case.get("replace_test_snapshot_sha256"))
            replacing = True
        before = {item["id"]: item for item in request("/menu-masters?limit=10000")["items"]}
        (folder / "masters-before.json").write_text(json.dumps(before, ensure_ascii=False, indent=2))
        import_id = case.get("saved_import_id")
        if import_id is not None and (not isinstance(import_id, str) or not import_id):
            raise RuntimeError("saved_import_id is unresolved")
        receipt = request(f"/monthly-menus/{case['month']}/imports/{import_id}") if import_id else upload(case, raw)
        (folder / "saved-import.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
        validate_receipt(receipt, month=case["month"], filename="original.xlsm", digest=case["sha256"],
                         scope=scope, diet_types=diets, sheet_name=case.get("sheet_name"), import_id=import_id)
        (folder / "review-material.json").write_text(json.dumps(review_material(receipt), ensure_ascii=False, indent=2))
        approval = case.get("import_review")
        if isinstance(approval, dict):
            for issue in receipt["current_review"]["issues"]:
                choices = [choice for choice in approval.get("resolutions", []) if isinstance(choice, dict)
                           and choice.get("source_name") == issue["source_name"]]
                if len(choices) != 1:
                    raise RuntimeError("Each unresolved source needs one explicit operator choice")
                choice = choices[0]
                if choice.get("action") == "create":
                    master = manifest["production_masters"][issue["normalized_name"]]
                    assert issue["source_name"] == master["name"], issue
                    assert not any(item["normalized_name"] == issue["normalized_name"] for item in before.values()), issue
                    assert all(choice.get(key) == value for key, value in master.items()), choice
        def command(path, body=None, content_type=None, *, revision=None):
            own = request(path, body, content_type, revision=revision)
            name = "saved-review.json" if path.endswith("/review") else "apply-response.json"
            (folder / name).write_text(json.dumps(own, ensure_ascii=False, indent=2))
            return own
        uploaded = apply_saved_import(receipt, approval, command)
        assert uploaded["created"] and uploaded["replaced"] is replacing, uploaded
        (folder / "upload.json").write_text(json.dumps(uploaded, ensure_ascii=False, indent=2))
        payload = request("/monthly-menus/" + case["month"])
        (folder / "menu.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        assert len(payload["entries"]) == case["expected_entries"]
        after = {item["id"]: item for item in request("/menu-masters?limit=10000")["items"]}
        summary = validate(payload, after, case["expected_quantities"])
        reloaded = request("/monthly-menus/" + case["month"])
        assert canonical_snapshot(reloaded) == canonical_snapshot(payload), "Reload changed the menu response"
        assert all(after[key] == value for key, value in before.items()), "Existing master was modified"
        summaries.append({"month": case["month"], "sha256": case["sha256"], **summary})
        (args.output_dir / "summary.json").write_text(json.dumps({
            "commit": os.environ.get("GITHUB_SHA"), "cases": summaries,
        }, ensure_ascii=False, indent=2))
        print(json.dumps(summaries[-1], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
