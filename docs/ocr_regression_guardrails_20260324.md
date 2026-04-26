# OCR Regression Guardrails 2026-03-24

This note captures the recurring mistake classes from the March 2026 OCR/order-detail redesign work and maps them to reusable Codex skills.

## Why the same mistakes repeated

The repeated failures were not random. They came from the same process defects:

- fixing one path and assuming the visible path was the same
- leaving legacy fallback reachable from current editing flow
- confusing current evidence with candidate evidence
- verifying helper output instead of the exact live order payload
- deploying after local tests without confirming the target order in live

## Hard recurrence-prevention measures

These are the rules that should prevent the same class of mistakes from recurring.

1. Freeze the winning path before editing
- For every production-visible bug, write down the exact endpoint or page payload that currently drives the symptom.

2. Freeze the visible success condition before editing
- State the expected result in one line.
- Example: `ORDe9cabe7e` must show `03/27` in `draft-sheet`.

3. Require one canonical owner for the changed concept
- Date extraction, quantity mapping, week resolution, and current draft adoption must each have an identified winning path.
- If multiple active paths remain, the task is not finished.

4. Treat candidate/current state as separate by default
- Rerun success is not a visible fix until current draft/evidence is switched or rebuilt.

5. Require representation-matched regressions
- If production failed on `tables.rows`, the regression must use `tables.rows`.

6. Require exact live before/after verification
- Health checks and helper output are never enough by themselves.

7. Do not close on symptom movement
- `empty -> stale`
- `col3 -> wrong semantic rows`
- `candidate fixed -> current still stale`
  These are not completions.

8. Separate commit history from deploy source
- Commit groups may be separated for review and rollback clarity.
- Deploy source must still include every change already running in production.
- If production was deployed from a local dirty tree or clean deploy copy, do not redeploy from clean branch HEAD until those changes are integrated into git.

9. Do not redeploy clean git when prod is ahead of branch
- If live `worker` or `web` revision/image is newer than the branch tip, treat production as ahead of git.
- First sync the prod-applied local changes into a branch or integration tree.
- Then stack the new fix on top of that tree.

10. Minimize service blast radius
- If only `worker` changed, do not redeploy `web`.
- If only `web` changed, do not redeploy `worker`.
- Do not refresh both services from an older tree just because one side needs a fix.

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

### 7. Deploy-source divergence

Git branch and live production came from different trees.

Examples:
- prod worker was deployed from a local clean-copy image while branch still pointed to an older commit
- a later OCR fix from clean HEAD would have rolled back shipping/order-form changes already live in prod

### 8. Refresh-policy divergence

The same draft was read through different refresh policies depending on the endpoint.

Examples:
- `draft-sheet` used semantic refresh, but `workflow-state` loaded the raw saved draft
- fixing stale rows in `ocr-sheet` did not clear stale blockers in `workflow-state`
- forcing semantic refresh everywhere then rewrote clean operator drafts during internal post-save refresh

## Additional non-negotiable rules from the March 26 regressions

11. Separate read-time refresh from write-time refresh
- A user-facing read path may refresh a stale blocked draft from semantic truth.
- A post-save internal refresh must not silently rebuild a clean operator-authored draft.
- If these two policies differ, they must be different code paths or different explicit modes.

12. Do not mix raw draft readers with canonical refresh readers on the same visible flow
- If `draft-sheet`, `ocr-sheet`, and `workflow-state` represent the same current draft, they must agree on whether semantic refresh has already been applied.
- A direct read from `draft_sheet_service.get_latest_sheet_draft()` is not interchangeable with a canonical helper that refreshes stale semantic state.

13. Add same-order endpoint parity tests for stale-draft fixes
- If a bug is visible in one of `workflow-state`, `draft-sheet`, or `ocr-sheet`, add a regression that proves the same saved draft behaves consistently across the affected endpoints/helpers.

14. Clean drafts and stale drafts require opposite merge policies
- Clean operator-authored drafts: preserve unmatched rows and warnings authored as part of the draft.
- Stale blocked auto drafts: prune unmatched stale rows and clear blockers that became false after semantic refresh.

15. No-op refreshes must not churn draft identity
- If semantic refresh produces no material change, it must not persist a new draft ID just to reshuffle warnings.
- LLM patch baselines and candidate lineage depend on this.

## Core regression invariants

These invariants are stricter than individual symptom fixes. If any of them fail, the incident is not closed.

### 1. Visible truth source is singular

- Freeze the exact current-editor source before editing.
- For Step2 issues, identify which endpoint actually feeds the visible sheet and treat that as the canonical owner for the incident.
- Do not accept helper output, alternate endpoints, or internal refresh payloads as proof until they match the visible source.

### 2. Saved-draft and no-saved-draft paths are different systems

- Always split investigation and testing into `saved draft present` and `saved draft missing`.
- `saved draft present` path:
  - prefer the persisted semantic draft
  - do not silently rebuild from raw OCR unless the user explicitly triggers recovery
- `saved draft missing` path:
  - bootstrap through semantic construction first
  - if semantic shell exists, a warning alone must not force raw-fallback

### 3. Three-surface parity is required

- `draft-sheet`, `ocr-sheet`, and `workflow-state` must be compared on the same order and same snapshot.
- Minimum parity tuple:
  - source
  - fields / row_count
  - blockers / warnings
  - apply-readiness
- A mismatch across these surfaces blocks closure.

### 4. Current and candidate evidence are separate by default

- `current` means the operator-visible active draft/order state.
- `candidate` means rerun/reparse output that may be adopted later.
- Candidate success is not a fix until the current editor explicitly keeps or switches state and the visible Step2 sheet reflects that choice.

### 5. Current editor must never be generic raw sheet when semantic exists

- `col1`, `col2`, `col3`, ... are not acceptable current-editor fields if a semantic shell is available.
- Low confidence may keep blockers active, but it does not justify silently downgrading the visible editor to generic raw OCR.
- If semantic mapping is incomplete, prefer structured review / choice UI over generic fallback.

## Operational guardrails for rerun / repair

- If new OCR arrives while the current draft is clean, prefer continuity of the current draft until the user explicitly switches.
- Persist draft source transitions and blocker reasons so read paths can explain which path won.
- Do not allow silent recovery or refresh to change row or column semantics without explicit user intent or an explicit recovery mode.

## Exact-order live verification

- For each production-visible OCR incident, verify the exact reported order before and after the fix.
- Record at minimum:
  - `draft-sheet`: source, fields, row_count
  - `ocr-sheet`: source, blockers, warnings, can_apply
  - `workflow-state`: state, apply_gate, candidate/active evidence IDs
- If the user reports a UI symptom, verify the same UI path, not only backend helper output.

16. Freeze the visible Step2 truth path before root-cause claims
- For Step2 bugs, identify the exact chain:
  - page surface
  - endpoint
  - saved-draft present vs missing
  - bootstrap helper
  - fallback condition
- Do not accept `/ocr-sheet` or helper output as proof if the screen is actually driven by `/draft-sheet`.

17. Treat `draft-sheet`, `ocr-sheet`, and `workflow-state` as one parity tuple
- For the same order and same moment, these three surfaces must be checked together.
- A fix is incomplete if only one of the three is correct.

18. Do not show generic raw columns in the current editor
- `col1`, `col2`, `col3`, ... may exist as debug or recovery representations.
- They must not be used as the user-facing current Step2 sheet if a semantic shell exists.

19. Saved revision history must not overwrite the current draft by default
- History is for comparison and recovery.
- It is not allowed to silently rebase stale saved revisions onto the current editor path without an explicit compatibility rule and regression coverage.

20. Auto-refresh must preserve surface parity
- If `/orders/{id}` is auto-refreshed, the UI must also refresh or invalidate the current draft/history state that can change Step2 behavior.
- A refresh that updates `workflow_state` but leaves `draft-sheet` or saved revision refs stale is a process failure, not a cosmetic issue.

## Skills created from these mistakes

### 1. `sawa-ocr-implementation-guardrails`

Use for any Sawa OCR/order implementation change.

Purpose:
- force end-to-end path tracing before editing
- prevent legacy fallback from re-entering current flow
- require regression tests for the exact failed representation
- require explicit success criteria and hard stop conditions

Location:
- `/Users/mmorinag/.codex/skills/sawa-ocr-implementation-guardrails`

### 2. `sawa-live-verification-guardrails`

Use for any production-facing Sawa OCR/order fix or deploy.

Purpose:
- require baseline capture on the exact reported order
- require live verification after deploy
- prevent “fixed” claims before the exact user-visible symptom is checked
- require before/after proof on the same order and same action sequence

Location:
- `/Users/mmorinag/.codex/skills/sawa-live-verification-guardrails`

## Intended usage

For most future OCR/order changes, use both skills together:

1. `$sawa-ocr-implementation-guardrails`
2. `$sawa-live-verification-guardrails`

The first prevents path-confusion during implementation.  
The second prevents false closure during production verification.

For deploy work where prod may be ahead of git, also enforce this rule explicitly:

1. identify the actual deploy source currently in prod
2. integrate that source into branch history or a dedicated integration tree
3. only then stack the next fix

## Operational changes required by the March 28 review

The current process must be tightened in four specific ways:

1. Worker deploy gates must validate exact-order parity, not only `ocr-sheet` quality
- At minimum, the target order must be checked across:
  - `/draft-sheet`
  - `/ocr-sheet`
  - `/workflow-state`
- Fail deploy if the current editor is generic raw columns or if the three surfaces disagree on apply readiness.

2. Web deploy verification must include the same user-visible order surface
- For UI-only fixes, do not stop at root/login and proxy health.
- Verify the exact order page or equivalent UI contract for the changed flow.

3. Regression suites must include the no-saved-draft bootstrap path
- Required fixture shape:
  - saved draft missing
  - semantic shell present
  - recovery warning present
  - current editor must stay semantic

4. Candidate/current state changes require an explicit end-to-end test
- Any flow that keeps current, switches candidate, or suppresses stale failure banners must have a UI/API parity regression, not only backend tests.
