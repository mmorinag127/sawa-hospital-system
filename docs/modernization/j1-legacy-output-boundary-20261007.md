# J1 legacy output boundary

Base: `c93d54a18458c740bbaeca085d6047d66b7a8370`.
Branch: `codex/modernization-j1-legacy-output-stop-20261007`.
Source-only; no runtime, tests, database, broker/network, build or deployment performed.

## Root boundary

The legacy `enqueue_outputs(order_id)` cannot record an original execution version. Previously it could send an unversioned task, generate inline when Celery was disabled or broker submission failed, mutate order status, and retry business output failure. Both its acceptance entrance and delivered task now raise `OriginalOutputExecutionRequired` (subclass of existing `OutputBuildError`) before any domain operation. There is no producer, guessed current capture, result, status update, fallback or migration added.

Task name `backend.src.workers.output_worker.generate_outputs` remains registered with its original order_id argument and exports route. It has max_retries=0 and empty autoretry_for; it does not call retry. The existing app retry delay of 10 does not cause retry without a retry call or autoretry. An ordinary exception is a Celery failure, not synthetic success, Ignore or unknown-task dropping. Existing external broker redelivery/operator resubmission is not asserted impossible: each such old delivery still fails without domain writes. Historical queued messages cannot be upgraded by inventing execution provenance.

## Actual source search and preserved paths

Read `AGENTS.md`, `backend/src/workers/output_worker.py`, `backend/src/workers/__init__.py`, output API routes and relevant Taskfile source. `rg` across `backend`, `scripts`, `Taskfile.yml` for enqueue_outputs/generate_outputs/output_worker and configured retry/ack annotations found only the legacy definition/self-sender and exports route; a contract-test string `_enqueue_outputs_after_confirm` is not a call to this worker. This is scoped source evidence, not proof about external messages, dynamically supplied plugins or database history.

Normal `backend/src/api/outputs.py` preview/download/daily bundle functions still call their existing builders. `/order-form-saved-sheet` still calls `order_form_service.build_saved_sheet_order_form_excel`. `output_builder.build_outputs` and its original-adoption/write-session contract are unchanged, as are normal order APIs, saved-sheet guards, quantities and recognizers. No task registration adjustment is necessary; workers/__init__.py remains unchanged.

## Authored definitions and residual proof

`backend/tests/unit/test_legacy_output_worker_boundary.py` calls actual enqueue and actual registered task.run. Side-effect tripwires cover builder, order status, broker send and retry. Definitions cover unset/false/true USE_CELERY, old/superseded/empty IDs, exhausted or nonzero historical retry headers, concrete exception, retained task name/route and no inline helper. The actual worker is not replaced with an always-failing mock.

No definitions were executed. Parent technical work remains installed-Celery registration/failure-state checks, old-message delivery and compatibility verification, plus normal saved-sheet/output API regressions after integration. No broker settings or domain retry policy outside this retired boundary were changed. This is not overall J1/runtime/stg acceptance.
