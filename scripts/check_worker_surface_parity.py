#!/usr/bin/env python3
import json
import re
import sys


def main() -> int:
    draft_path, ocr_path, workflow_path = sys.argv[1:4]
    with open(draft_path, "r", encoding="utf-8") as fh:
        draft = json.load(fh)
    with open(ocr_path, "r", encoding="utf-8") as fh:
        ocr = json.load(fh)
    with open(workflow_path, "r", encoding="utf-8") as fh:
        workflow = json.load(fh)

    draft_fields = draft.get("fields") or []
    draft_rows = draft.get("rows") or []
    ocr_fields = ocr.get("fields") or []
    ocr_rows = ocr.get("rows") or []
    ocr_can_apply = bool(ocr.get("can_apply"))
    ocr_apply_blockers = ocr.get("apply_blockers") or []
    apply_gate = workflow.get("apply_gate") or {}
    workflow_can_apply = bool(apply_gate.get("can_apply"))
    workflow_ocr_can_apply = bool(workflow.get("ocr_can_apply_draft", workflow_can_apply))
    workflow_blockers = apply_gate.get("blockers") or []

    if not draft_fields or not draft_rows:
        draft_apply_blockers = draft.get("apply_blockers") or []
        if not draft_fields:
            raise SystemExit("surface parity failed: draft-sheet fields are empty")
        if not draft_rows:
            if "rows_empty" not in draft_apply_blockers:
                raise SystemExit("surface parity failed: draft-sheet rows are empty without rows_empty blocker")
            if workflow_can_apply or ocr_can_apply:
                raise SystemExit(
                    "surface parity failed: blocked empty draft-sheet disagrees with apply gate "
                    f"workflow_can_apply={workflow_can_apply} ocr_can_apply={ocr_can_apply}"
                )

    generic_pattern = re.compile(r"col\d+$")
    draft_is_generic = all(generic_pattern.fullmatch(str(field or "")) for field in draft_fields)
    if draft_is_generic:
        raise SystemExit(
            "surface parity failed: draft-sheet is generic raw columns "
            f"fields={draft_fields}"
        )

    draft_has_menu = "menu" in draft_fields
    draft_has_qty = any(str(field).startswith("qty.") for field in draft_fields)
    ocr_has_menu = "menu" in ocr_fields
    ocr_has_qty = any(str(field).startswith("qty.") for field in ocr_fields)

    if ocr_has_menu and ocr_has_qty and not (draft_has_menu and draft_has_qty):
        raise SystemExit(
            "surface parity failed: ocr-sheet is semantic but draft-sheet is not "
            f"draft_fields={draft_fields} ocr_fields={ocr_fields}"
        )

    if ocr_can_apply and not workflow_ocr_can_apply:
        raise SystemExit(
            "surface parity failed: ocr-sheet can_apply=true but workflow-state blocks apply "
            f"workflow_blockers={workflow_blockers}"
        )

    if workflow_can_apply and ocr_apply_blockers:
        raise SystemExit(
            "surface parity failed: workflow-state can_apply=true but ocr-sheet still has blockers "
            f"ocr_apply_blockers={ocr_apply_blockers}"
        )

    print(
        "ok: draft/ocr/workflow parity "
        f"draft_rows={len(draft_rows)} ocr_rows={len(ocr_rows)} "
        f"workflow_can_apply={workflow_can_apply} workflow_ocr_can_apply={workflow_ocr_can_apply} "
        f"ocr_can_apply={ocr_can_apply}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
