#!/usr/bin/env python3
import json
import re
import sys


def load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def normalize_workflow(data):
    apply_gate = data.get("apply_gate") or {}
    return {
        "state": data.get("state"),
        "warnings": data.get("warnings") or [],
        "candidate_evidence_run_id": data.get("candidate_evidence_run_id"),
        "active_evidence_run_id": data.get("active_evidence_run_id"),
        "can_apply": bool(apply_gate.get("can_apply")),
        "blockers": apply_gate.get("blockers") or [],
    }


def main() -> int:
    paths = list(sys.argv[1:])
    reject_generic_draft = False
    if "--reject-generic-draft" in paths:
        paths.remove("--reject-generic-draft")
        reject_generic_draft = True
    ocr_worker_path, ocr_web_path, draft_worker_path, draft_web_path, workflow_worker_path, workflow_web_path = paths[:6]

    ocr_worker = load(ocr_worker_path)
    ocr_web = load(ocr_web_path)
    draft_worker = load(draft_worker_path)
    draft_web = load(draft_web_path)
    workflow_worker = load(workflow_worker_path)
    workflow_web = load(workflow_web_path)

    for label, worker, web in (
        ("ocr-sheet", ocr_worker, ocr_web),
        ("draft-sheet", draft_worker, draft_web),
    ):
        if (worker.get("fields") or []) != (web.get("fields") or []):
            raise SystemExit(f"worker/web mismatch: {label} fields differ")
        if (worker.get("rows") or []) != (web.get("rows") or []):
            raise SystemExit(
                f"worker/web mismatch: {label} rows differ "
                f"worker={len(worker.get('rows') or [])} web={len(web.get('rows') or [])}"
            )

    if reject_generic_draft:
        draft_fields = draft_worker.get("fields") or []
        if draft_fields and all(re.fullmatch(r"col\d+", str(field or "")) for field in draft_fields):
            raise SystemExit("web deploy parity failed: current draft-sheet is generic raw columns")

    if normalize_workflow(workflow_worker) != normalize_workflow(workflow_web):
        raise SystemExit("worker/web mismatch: workflow-state differs")

    label = "exact-order current-state parity" if reject_generic_draft else "worker/web current-order match"
    print(
        f"ok: {label} "
        f"ocr_rows={len(ocr_worker.get('rows') or [])} "
        f"draft_rows={len(draft_worker.get('rows') or [])} "
        f"state={workflow_worker.get('state')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
