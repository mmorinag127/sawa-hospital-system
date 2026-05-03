# workspace Development Guidelines

Auto-generated from all feature plans. Last updated: 2026-01-05

## Active Technologies
- Python 3.11 + FastAPI, Pydantic, SQLAlchemy, Celery (async ingest/exports), pdfplumber + Tesseract OCR, pandas for CSV/Excel generation (001-order-spec)
- Next.js 14 + React 18 + TypeScript, Axios, Playwright (UI e2e) (001-order-spec)
- PostgreSQL (orders, menus, configs), Redis for queues, S3-compatible object storage or filesystem for PDFs/outputs (001-order-spec)

## Project Structure

```text
backend/
frontend/
tests/
```

## Commands

- Backend run: `task backend_run`
- Frontend dev (Cloud Run proxy): `task frontend_run_cloudrun_proxy`
- Backend tests: `task backend_test_us_all` or `task backend_test`
- Frontend E2E: `task frontend_test_e2e`

## Code Style

- Follow existing conventions; prefer small, explicit helpers over magic.

## Recent Changes
- 001-order-spec: Added auth guard in frontend and case-insensitive email checks in backend auth.
- 001-order-spec: Added output error handling, menu validation, retention worker, and monitoring placeholders.
- 001-order-spec: Added quickstart runbook and golden/perf tests scaffolding.

<!-- MANUAL ADDITIONS START -->
## Operator Rules (Manual)
- Do not use partial `gcloud run services update` that can drop env vars. If Cloud Run env changes are required, update all env vars in one operation or use IaC only.
- Do not change behavior in areas already working. Only modify what the user explicitly requests.
- Before touching Cloud Run settings, enumerate and verify all required env vars; do not proceed if any are missing.
- For GCP Console guidance, always specify exact navigation paths (menu names) and precise click targets; avoid vague directions.
- Always read this `workspace/AGENTS.md` before starting any task. Do not simplify or reinterpret user requirements; confirm them explicitly if anything is ambiguous, and implement exactly what was requested.
- Separate commit history from deploy source. It is acceptable to keep fixes in separate commits, but never deploy from a clean branch tree if production already contains newer local or out-of-band changes that are not present in that tree.
- Before any deploy, verify deploy-source parity: identify the exact prod revision/image currently serving traffic and confirm the deploy source includes those changes. If prod is ahead of the branch, first sync those changes into an integration tree or clean deploy copy, then stack the new fix on top.
- For web deploys, do not hand-pick or reuse an old clean deploy copy. Use the standard prepare step to create a fresh copy and require deploy-source sentinel parity before building.
- When only one service needs a fix, do not redeploy the other service from an older tree. Minimize blast radius and avoid rolling back unrelated live behavior.
- If a clean saved draft or explicit user correction exists, do not let stale OCR evidence warnings or legacy fallback paths override that newer source of truth without an explicit blocker that still applies after the correction.
- For any Step2 or OCR-order bug, freeze the visible truth path before editing: `page -> endpoint -> saved draft present? -> bootstrap path -> fallback condition`.
- Treat `saved draft present` and `saved draft missing` as separate execution paths. Do not assume a fix on one path applies to the other.
- `draft-sheet`, `ocr-sheet`, and `workflow-state` must be treated as a parity tuple for the current order. Do not call a fix complete until all three agree on the same current order state.
- A generic raw sheet (`col1`, `col2`, `col3`, ...) is not an acceptable current editor surface. Warnings may require review, but they do not by themselves justify downgrading the visible Step2 sheet to a generic raw draft.
- Keep current and candidate evidence separate by default. A rerun or candidate result is not a visible fix until the current editor has explicitly kept or switched state.
- Never merge or declare fixed while current/candidate ambiguity, stale saved-revision rebase, or stale evidence mismatch is still unresolved.
- Exact-order live verification must include the user-visible surface, not only helper APIs. If the user reports a specific order, verify the same order through the same visible flow before claiming success.
- Before any non-trivial edit, show the user these three items explicitly and keep them aligned until the task is actually complete:
  - fixed requirements
  - forbidden actions
  - completion criteria
- If the user says the interpretation is wrong, discard the previous interpretation immediately and restate the corrected one before further edits.
- If the chosen canonical source, schema, facility template, or success condition is ambiguous, stop and ask instead of guessing.
- If an upstream canonical source is unresolved or missing, block downstream sheet generation or apply/confirm instead of silently falling back to a default or legacy path.
- For repeated production-visible bugs, do not only patch the visible symptom. Harden the common decision point or shared canonicalization path that allowed the failure.
- If a fallback path can still recreate the same class of user-visible corruption, keeping that fallback active counts as incomplete.
- Facility/operator-configured canonical sources must outrank OCR inference, stale drafts, heuristics, and legacy defaults.
- If a facility has multiple configured template candidates, unresolved per-order template selection is a blocker. Do not silently pick the first/default template and continue.
- When the user states a precedence rule such as `configured source is canonical and OCR is auxiliary`, encode that precedence in the shared path, not in a one-off patch.
- For any requested root fix, first define the failure class, the shared decision point that allowed it, and the invariant that should have blocked it. Do not start implementation until those are clear.
- Preferred root-fix order:
  - define the failure class
  - locate the common entry point
  - define the invariant
  - remove or block the dangerous fallback
  - add tests for the failure class and close neighboring cases
  - verify every visible surface that depends on that path
- `Fix the reported order with a local exception` is not a root fix.
- `Patch downstream output after a wrong upstream decision` is not a root fix if the upstream decision can still be wrong elsewhere.
- A root fix is incomplete if the same failure class can still enter through a sibling path.
- Before declaring a root fix complete, state which neighboring failure cases were checked and how they are now blocked or still open.
- Root-fix validation must cover:
  - the reported case
  - at least one close sibling case from the same failure class
  - the explicit stop/block behavior when the invariant cannot be satisfied

## Git/JJ Release Source Discipline
- Failure class: deploy source of truth mismatch. This means a fix exists in a jj working-copy commit, sibling git commit, detached worktree, deploy copy, or old integration tree, but the service is built from another HEAD that does not contain that fix.
- Treat deploy source selection as a hard correctness gate, not as an operational detail.
- Do not deploy Sawa stg or prod from detached HEAD. Create or use a named release branch first.
- Do not treat `git status --short` being clean as deploy readiness. Clean only means the current worktree has no local diff; it does not prove that sibling commits, jj working-copy commits, or other worktrees are integrated.
- Before deploy, list all worktrees with `git worktree list` and all jj repositories with `find /Users/mmorinag/Sawa/2025.12 -maxdepth 3 -name .jj -type d -print`.
- For each relevant jj repository, run `jj status`. If a jj working-copy commit has production-code changes, it must be integrated into the deploy branch or explicitly abandoned before deploy.
- Empty jj descriptions such as `JJ_EMPTY_STRING` or `(no description set)` are deploy blockers when they contain production-code changes.
- Before deploy, inspect sibling history for critical paths with `git log --all --not HEAD -- <path>`. Any candidate fix commit outside HEAD must be checked with `git merge-base --is-ancestor <commit> HEAD`.
- Critical Sawa paths include `backend/src/services/hakodate_fixed_quad_registration_service.py`, `backend/src/services/order_service.py`, `backend/src/services/order_workflow_v2_service.py`, `backend/src/services/uploaded_pdf_service.py`, `backend/src/services/apply_gate_service.py`, `backend/src/api/orders.py`, `frontend/src/pages/orders/[id].tsx`, `frontend/src/pages/orders/[id]/workflow-v2.tsx`, `frontend/src/pages/orders/[id]/inspection-v2.tsx`, `frontend/src/pages/orders/index.tsx`, `scripts/`, and `Taskfile.yml`.
- Generated artifacts and production-code changes must not be treated as one release unit. Separate production-code changes from generated verification artifacts before release judgment.
- Always create a fresh deploy copy after the final release commit. Do not reuse an old deploy copy or old generated deploy source.
- Before claiming deploy completion, verify Cloud Run worker and web revisions/images and verify the exact user-visible live surface after deploy.
- Do not run one-off verification or reconstruction code via `python - <<'PY'` or equivalent heredoc. Save reusable verification code as a tracked or intentionally ignored file, then execute that file so the source can be reviewed and reproduced.
- Before promoting stg to prod, compare stg/prod Cloud Run revisions, source archives, facility configs, and data counts. If prod has existing order data, do not run stg cleanup/reset procedures against prod.
- Facility template drift is a deploy blocker. If a facility resolves to different quantity fields between stg and prod, explicitly choose the canonical source before deploy instead of assuming stg data can be copied to prod.
<!-- MANUAL ADDITIONS END -->
