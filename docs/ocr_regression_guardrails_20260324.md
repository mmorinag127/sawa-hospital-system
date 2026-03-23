# OCR Regression Guardrails 2026-03-24

This note captures the recurring mistake classes from the March 2026 OCR/order-detail redesign work and maps them to reusable Codex skills.

## Why the same mistakes repeated

The repeated failures were not random. They came from the same process defects:

- fixing one path and assuming the visible path was the same
- leaving legacy fallback reachable from current editing flow
- confusing current evidence with candidate evidence
- verifying helper output instead of the exact live order payload
- deploying after local tests without confirming the target order in live

## Recurrent failure classes

### 1. Duplicated extraction logic

The same concept existed in multiple helpers and only one got patched.

Examples:
- OCR date inference in an OCR-only helper vs current sheet filter
- structured `tables.rows` vs `table_rows` vs `table_raw`

### 2. Legacy fallback leakage

Current editing flow still reused old draft/revision/cache content.

Examples:
- saved draft blocked fresh facility-template rebuild
- old edited OCR content leaked into evidence-first paths

### 3. Current vs candidate evidence confusion

Rerun succeeded but the screen still showed the previous current draft.

Examples:
- `new_evidence_available` existed, but the visible sheet still came from old `base_evidence_run_id`

### 4. Semantic shell vs numeric trust confusion

The table looked structurally valid, but quantities were still low-confidence or misprojected.

### 5. Workflow/UI mismatch

Backend state and frontend state diverged.

Examples:
- backend fixed `col3`, frontend still showed stale local state
- workflow-state changed, current draft did not

### 6. Apply/confirm divergence

Step2 edited one source while apply/confirm wrote from another.

## Skills created from these mistakes

### 1. `sawa-ocr-implementation-guardrails`

Use for any Sawa OCR/order implementation change.

Purpose:
- force end-to-end path tracing before editing
- prevent legacy fallback from re-entering current flow
- require regression tests for the exact failed representation

Location:
- `/Users/mmorinag/.codex/skills/sawa-ocr-implementation-guardrails`

### 2. `sawa-live-verification-guardrails`

Use for any production-facing Sawa OCR/order fix or deploy.

Purpose:
- require baseline capture on the exact reported order
- require live verification after deploy
- prevent “fixed” claims before the exact user-visible symptom is checked

Location:
- `/Users/mmorinag/.codex/skills/sawa-live-verification-guardrails`

## Intended usage

For most future OCR/order changes, use both skills together:

1. `$sawa-ocr-implementation-guardrails`
2. `$sawa-live-verification-guardrails`

The first prevents path-confusion during implementation.  
The second prevents false closure during production verification.
