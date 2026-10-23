# Agent/native batch remediation

## Scope and evidence

- Base: f50d8118fcccec8a674428beb167191f4b3a78da.
- Worktree: /Users/mmorinag/Sawa/2025.12/worktrees/agent-batch-remediation-20261024.
- Branch: codex/agent-batch-remediation-20261024.
- Existing evidence: /private/tmp/sawa-hospital-full-f50d8118-20261024/06-agent-assistant-tests.log, 412 passed / 10 failed, all 422 executed by the parent. This is not a result for the changed source.
- No tests, collection, application/test imports, typecheck, builds, cloud calls, process termination, commit, push, merge or deployment were run in this worktree.

## Classification fixed before implementation

| Failed nodes | Root/shared decision | Invariant and disposition |
| --- | --- | --- |
| test_gemini.test_stop_during_create_keeps_receipt_without_executing_late_tools [completed, in_progress, rejected, timeout] (4) | Managed.stop used create_task(_stop) before fencing. One event-loop turn could still observe generation 0 while a provider create was outstanding. This was a real scheduling window, not an incorrect generation assertion. | Fence synchronously in the stop coroutine before its first await, then pass the already captured fence to shielded cleanup. Managed and Gemini share this entry. Preserve the original four outcomes, no late native tools, remote cancellation when needed, unknown reservation retention and exact receipts. |
| test_native_workflows.test_real_shift_approval_restart_confirmation_output_and_rollback (1) | The traceback reaches native shift output then fails importing openpyxl. | Parent-owned prepared runtime must provide openpyxl. No dependency edits, skipped output, guessed file or application fallback added. Approval/restart/output/rollback assertions unchanged. Environment prerequisite remains outstanding. |
| test_native_workflows.test_real_lunch_questions_save_approval_restart_and_three_official_outputs (1) | Cleanup fails on killpg(pid, 0) with EPERM. A denied probe does not establish disappearance. | Treat OS-denied inspection/signalling/probes as explicit unresolved cleanup, never successful stop. Retain process markers/maps and business/before data. Native lunch flow and its three outputs unchanged. OS authority must be checked in parent runtime; this patch cannot grant it. |
| test_native_workflows.test_real_shift_business_condition_question_parse_confirm_resolve_and_output (1) | Cleanup fails the leader command/time/group ownership check. An already-exited child can be inspected before its wait task reaps it; the log does not identify which ownership field failed, so environment restriction/PID mismatch is not ruled out. | Allow the owned wait task to reap first. Only absent process group plus successful child wait establishes cleanup success. Still reject live mismatched owner, reused PID, unknown surviving group or denied inspection. No unconditional kill or weakened owner check. Parent runtime confirmation remains necessary. |
| test_staging_runtime.test_lookup_does_not_copy_models_or_persist_orphan_before_images; test_stop_reclaims_only_models_and_retains_business_and_before_data (2) | Tests require an uninstalled pytest-asyncio plugin, although the suite already uses synchronous tests calling asyncio.run. | Convert these two functions to synchronous wrappers around unchanged async scenarios. Retain every existing assertion/operation, including repeated stop, no model copy, no orphan persistence, and exact business/before bytes. No new dependency or skip. |
| test_trial.test_stop_reaps_local_writes_even_when_provider_stop_unknown (1) | Test double accepted only ident and returned None. Current native stop requires expected_generation and explicit True; TypeError occurred before calls.append, correctly resulting in unresolved cleanup. | Update the double to assert the actual fenced generation and explicitly return True. Keep local-call assertion, stop_pending, generation 1 and full reservation retention after unknown provider stop. Do not make application accept None as confirmed cleanup. |

## Application implementation

1. managed.py: stop fences before task scheduling/await. Shielded cleanup remains cancellation-resistant and receives the captured fence instead of incrementing it twice.
2. managed.py and gemini.py: reject superseded cleanup before cancelling tasks and after cancellation awaits. Native stop still uses expected_generation and only explicit True counts as confirmed. Provider uncertainty retains reservation/status; no remote uncertainty is converted to stopped.
3. sandbox.py: give an already-finished child wait a scheduling turn before owner command inspection. A completed wait is not sufficient: process-group absence must also be proved. A live or unknown group still goes through the unchanged PID/create-time/command/PGID checks.
4. sandbox.py: convert psutil.AccessDenied / PermissionError during ownership, signal and group probe to explicit Conflict with unresolved-cleanup wording. Failure leaves existing markers/maps/log handles and data intact. No unknown process is signalled by a failed owner check.
5. managed.py and gemini.py: when provider stop is also unknown, do not announce that local business writes stopped unless native cleanup actually returned True. Both uncertainties retain the pending state and reservation. The existing provider/native uncertainty regression now also covers simultaneous remote uncertainty for both providers and checks the displayed notices.

No bundled native adapter, lock/dependency, Dockerfile, backend, frontend, scripts or other worktree was changed. No optimization was mixed into the fix.

## Regression source changes (not executed)

- The four existing Gemini stop-during-create cases are retained unchanged as the scheduling/late-tool/receipt regressions.
- test_native_lifecycle adds denied-owner inspection with zero signals; denied SIGTERM or group probe with preserved marker/child/log and no SIGKILL; and completed child wait with absent group before success.
- Existing owner timestamp/PGID mismatch, reused PID, cancellation, delayed tool, generation and unknown cleanup tests remain in place.
- test_staging_runtime retains both complete async scenario bodies under asyncio.run.
- test_trial's native stop double now implements the actual generation-aware success contract; all original unknown-provider assertions remain.
- Native shift/lunch approval, condition parsing, save/reload/restart, official outputs and rollback tests were not weakened or bypassed.

## Handoff / unresolved evidence

This is a static implementation freeze, not overall completion. The parent must review/integrate and run the exact integrated-SHA serial batch. The openpyxl runtime prerequisite is parent-owned. EPERM is a concrete failed OS operation in the existing log; this patch preserves honest pending state, not a claim that the OS restriction is resolved. The shift owner mismatch needs the later batch to determine whether reaping fixed it or actual ownership/permission differs. No live native workflow or manual staging proof has been performed here.
