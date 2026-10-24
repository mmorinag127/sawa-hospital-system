# School native upstream synchronization, 2026-10-24

## Fixed checkpoint

- Hospital worktree: /Users/mmorinag/Sawa/2025.12/worktrees/agent-batch-remediation-20261024
- Hospital branch: codex/agent-batch-remediation-20261024
- Clean worktree checked before synchronization.
- Fast-forward only: eb85ebd86f0c90e61d7ef5ecf5245b01bb358ef5 -> eca1c60c723c1885e7655c22d9b7962da5a0da71.
- Parent branch resolved to exactly eca1c60c723c1885e7655c22d9b7962da5a0da71.
- School source repository: /Users/mmorinag/Sawa/2025.12/worktrees/school-torch-security-20261024
- Manifest upstream base: 9ad04a60a7038ad3c751d944f86749dda912ee1e
- Actual school target commit: 33b30f85703967083cf68b3537f485e605570a25
- Report: /private/tmp/sawa-native-school-sync-20261024.json
- Upstream delta SHA-256: d5a529bbfc7811eeac1825743a0d39388ada28f4a51ccf585a4245090816b562

## Exact operation

The explicitly requested source-integration utility was run with bytecode writes
disabled. No application startup/import checks, collection, tests, typecheck,
build, dependency installation, cloud, Actions, commit, push or deployment ran.

```sh
env PYTHONDONTWRITEBYTECODE=1 /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python -m experiments.agent_assistant.update_native --repo /Users/mmorinag/Sawa/2025.12/worktrees/school-torch-security-20261024 --bundle agent_runtime/native/school-lunch --manifest agent_runtime/manifests/school-lunch.json --target 33b30f85703967083cf68b3537f485e605570a25 --report /private/tmp/sawa-native-school-sync-20261024.json
```

Exit 0: eight changed files, no conflicts. The utility compared base/current/
target rather than copying the whole upstream tree. All eight were reported as
upstream_delta because the corresponding old bundled bytes equalled their
upstream base bytes.

## Runtime delta applied

Each path below is relative to agent_runtime/native/school-lunch. Static byte
comparison confirms every changed file equals the actual target commit blob.

- backend/src/migration_catalog.py
- backend/src/services/implementation_price_ocr_delivery_service.py
- backend/src/services/implementation_price_ocr_dispatch_service.py
- backend/src/services/implementation_price_ocr_job_service.py
- backend/src/services/implementation_price_storage.py
- backend/src/services/menu_process_gemini.py
- backend/src/staging_data.py
- worker/requirements.txt

The runtime changes cover restored catalog cast comparison, school-specific
dataset/storage/runtime setting boundaries, durable OCR delivery/dispatch/job
ownership paths, and the torch/vision security pins. No worker recognition
implementation or quantity algorithm was independently rewritten. Existing
upstream changes were kept as the requested source delta, not reinterpreted.

worker/requirements.txt now carries torch 2.13.0(+cpu) and torchvision
0.28.0(+cpu) in the unchanged platform branches. No additional dependency pin
was manually added during this integration.

The source delta also changes six backend test/helper paths. These are outside
the native bundle's published runtime boundary and were not copied:
backend/tests/helpers/school_cloud_dispatch.py;
backend/tests/unit/test_implementation_price_ocr_job_service.py;
backend/tests/unit/test_menu_process_gemini.py;
backend/tests/unit/test_ocr_durable_delivery.py;
backend/tests/unit/test_restored_check_casts.py;
backend/tests/unit/test_staging_database_selection.py.
No changed upstream runtime path under the utility's backend/worker/pyproject
boundary was omitted.

## Retained extensions

All fourteen existing extension files retain exactly their manifest-recorded
bundled SHA-256, not merely their names:

- backend/src/api/implementation_price_tables.py
- backend/src/api/menu_process.py
- backend/src/api/menu_process_contract.py
- backend/src/api/monthly_procurement.py
- backend/src/api/route_diagrams.py
- backend/src/schemas/menu_process.py
- backend/src/services/agent_daily_process.py
- backend/src/services/implementation_price_table_service.py
- backend/src/services/menu_process_service.py
- backend/src/services/process_pattern_service.py
- backend/src/services/process_question_drawing.py
- backend/src/services/process_work_schedule.py
- backend/src/services/route_diagram_service.py
- backend/src/services/shared_menu_service.py

There was no conflict resolution, extension deletion or blind replacement.

## Parent-reviewed seal

The parent reviewed the merge report and runtime delta, then ran the utility's
separate --seal operation with the same repository, target and report. Exit 0.
At this initial synchronization checkpoint, the manifest recorded source_revision
33b30f85703967083cf68b3537f485e605570a25, the prior upstream base, the delta
digest and regenerated file/extension hashes. This records source provenance,
not successful application or packaged runtime verification. No source_revision
was fabricated and no runtime test was executed.

## Remaining work / freeze

Merge conflicts: 0. Unresolved merge edits: 0.
Pending: monitored source commit/integration and later
exact-SHA serial batch; Linux CPU runtime installation, actual runtime versions,
model-hash identity, same-input recognition/business equivalence and authorized
staging remain unverified. This source-integration checkpoint is not overall
completion and does not prove audit/OCR/quantity parity.

Writer stops after this static checkpoint. Other worktrees and the previously
frozen source are not manually edited.

## Security inventory follow-up

The subsequent actual source target is d0f29ec7cef8a455604ee2a8134c63ce8f0de747.
The four-file Pillow/image bootstrap delta was merged and sealed without
conflicts using /private/tmp/sawa-native-school-security-followup-20261024.json.
The current manifest describes that target, not the initial33b30f8 checkpoint.
The subsequent telemetry startup source0e9a8aaf1f2b72b424f97a9ef73e6dcbfe0b814e
was merged and sealed using /private/tmp/sawa-native-school-telemetry-sync-20261024.json.
That current source also includes the OCR app bootstrap, now covered by the
source update utility's OCR-directory query.
Current inventory status is in runtime_dependency_inventory_20261024.md;
the earlier merge evidence does not claim patched runtime acceptance.
