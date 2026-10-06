# C2 CI Contract 2026-10-06

Base: `aed40b6df5314d81ad260bf03d45d46699eea3a0`

Baseline reproduction used a dedicated SQLite database and cache under `/tmp/sawa-c2-ci-contract/baseline`.

- Backend command: `/Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python -m pytest backend/tests/integration/test_line_corrections.py backend/tests/integration/test_status_flow.py -q`
- Backend result: `2 failed, 1 passed`; both normal-output tests failed with `workflow-v2 saved sheet template version is required`.
- Backend log: `/tmp/sawa-c2-ci-contract/logs/baseline-backend.log`
- Backend log SHA256: `11e9b9ac4753add7e519ff645c5621bd3d7dc8d990bcd6d0ad11e5078eda7383`
- Frontend command: `node --test frontend/tests/config/google-auth-deploy.test.js`
- Frontend result: dependency resolution stopped before the contract test because `js-yaml` is unavailable in this isolated worktree.
- Frontend log: `/tmp/sawa-c2-ci-contract/logs/baseline-frontend.log`
- Frontend log SHA256: `b8c0d98f8f1d21cb926c1d21622884fa3a6110db3755cf90c8c09ed5a6439b9a`

Failure class: CI integration tests bypassed the workflow-v2 context, selected-evidence, and saved-sheet path, so the saved-sheet template-version invariant was absent.

Shared decision point: `order_workflow_v2_service.confirm_context` through `select_ocr_result` and `save_sheet`.

Invariant: an output-producing saved sheet and its workflow must reference the same explicit active template version; absent lineage blocks before materialization.

The normal fixture is a synthetic non-OCR component contract: it enters through workflow-v2 context confirmation, selected OCR evidence, and saved-sheet persistence. It is not evidence of a successful legacy PDF upload-to-OCR flow.

Verification after the test-contract repair used dedicated `/tmp/sawa-c2-ci-contract` runtime paths.

- Focused backend: `5 passed`; `/tmp/sawa-c2-ci-contract/logs/focused-backend.log`; SHA256 `7e9c978fd2bc9f6ec2979921447d606c14e6fb8d17ac36a1fea5f6e386590473`.
- CI backend US1-4 exact workflow list: `432 passed, 17 skipped`; `/tmp/sawa-c2-ci-contract/logs/ci-us1-4.log`; SHA256 `eaae5e7bd5b73291747a17ca7a29bd848f85dad8c2420c40acc747a22ed9ce79`.
- Frontend focused Google auth contract: `7 passed`; `/tmp/sawa-c2-ci-contract/logs/focused-frontend.log`; SHA256 `94f8da4e911ec12ca8dda79e06dff98411aea619517fe9852f7f161b958bf2cc`.
- Frontend route-contract suite: `123 passed`; `/tmp/sawa-c2-ci-contract/logs/frontend-route-contract.log`; SHA256 `c25e2247762994a73ad647b03fbd538efe58bb635655ae84cd2c2b3f9210d56e`.

## Post-integration verification

Source commit: `bb5218402a8cf3d0602202abc47c71f6273d151f` on `codex/modernization-20261004` in `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-main`.

All SQLite runtime state used `HOME`, `XDG_CACHE_HOME`, `PYTHONPYCACHEPREFIX`, and a unique `DB_URI` under `/private/tmp/sawa-c2-ci-postintegration-bb52184`; the Python executable was `/Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python`.

- Focused backend command: `pytest backend/tests/integration/test_line_corrections.py backend/tests/integration/test_status_flow.py -q`; start `2026-10-06T01:34:25Z`; end `2026-10-06T01:34:31Z`; exit `0`; `5 passed`; log `/private/tmp/sawa-c2-ci-postintegration-bb52184/logs/focused.log`; SHA256 `4978e985e1e9d4052a56c2c86fdbda97e40cbc38d097b4f8e6760fe98cf1dbb7`.
- CI US1-4 exact command: `pytest` with the exact 19 paths from `.github/workflows/ci.yml`; start `2026-10-06T01:34:44Z`; end `2026-10-06T01:34:50Z`; exit `0`; `432 passed, 17 skipped`; log `/private/tmp/sawa-c2-ci-postintegration-bb52184/logs/ci-us1-4.log`; SHA256 `1502256ca8659f77e1971d83c12fe91995fdc9a937f64d78488862f16f0c98c7`.
- Frontend route-contract command: `NODE_PATH=/Users/mmorinag/Sawa/2025.12/workspace/frontend/node_modules npm run test:config`; start `2026-10-06T01:34:57Z`; end `2026-10-06T01:35:00Z`; exit `0`; `123 passed`; log `/private/tmp/sawa-c2-ci-postintegration-bb52184/logs/frontend-route-contract.log`; SHA256 `852bdaf31659574a41c80a8ab5c772b8d09c1ebc8870f05ef5a9bb2532f785d5`.

The assertions verified on the post-integration commit include the preserved `len(rows) == 2`, output quantities `10` and `7`, zero-row exclusion, persisted `quantity_corrected == 7`, HTTP `410` legacy-workflow replacement, saved-sheet absence output blocking with no artifact, inactive explicit-template blocking, and named OIDC step audience/include-email/WIF-source/manual-production-gate checks.

Owned PostgreSQL attempt: `/opt/homebrew/bin/initdb -D /private/tmp/sawa-c2-ci-postintegration-bb52184/pg/data -A trust -U postgres --no-locale`, with the intended short socket `/private/tmp/sawa-c2-ci-postintegration-bb52184/pg/socket` and port `55484`. Start `2026-10-06T01:35:38Z`; test exit `1`; post-stop exit `1` because initialization failed before the server started. `initdb` failed with `could not create shared memory segment: Operation not permitted` (`shmget`); no PostgreSQL cluster or other cluster was touched. The 17 PostgreSQL tests therefore remain unexecuted and SQLite results are not PostgreSQL evidence. PostgreSQL test log SHA256: `f84e5275cf9aa59b14c04b9aaaa942e521a7ed695b32ba8ecce13191ef7298b5`; initdb log SHA256: `0708f40a22415bd279d5dcf5082f8bd64c6afc9c7a29154bc70527739175fb73`.

These local results are post-integration evidence only. They do not claim GitHub full-CI success.

Post-sandbox-escalation owned PostgreSQL rerun: a fresh cluster was created only at `/private/tmp/sawa-c2-ci-postintegration-bb52184/pg-escalated`, with its own socket directory and unused port `55485`. `initdb`, `pg_ctl start`, and pre-test `pg_isready` succeeded before pytest began. `pytest backend/tests/contract/test_automation_bootstrap_postgres.py -q` then completed with `17 passed` (start `2026-10-06T01:36:50Z`, end `2026-10-06T01:36:52Z`, exit `0`). `pg_ctl stop -m fast` exited `0`; post-stop `pg_ctl status` exited `3` and `pg_isready` exited `2`, proving the owned server/socket were no longer listening. No cloud or existing PostgreSQL cluster was used.

- PostgreSQL test log: `/private/tmp/sawa-c2-ci-postintegration-bb52184/logs/postgres-owned-escalated.log`; SHA256 `53c4c94ca6df3e616665b9425ab916726499cd3e8d3cf4c4ad903b9258b7d164`.
- Init/start/stop log: `/private/tmp/sawa-c2-ci-postintegration-bb52184/pg-escalated/init-start.log`; SHA256 `2edb352df95acdec662204037c32fae446b51afe9fdef7a44fb278de0519a52e`.
- Pre-test ready log: `/private/tmp/sawa-c2-ci-postintegration-bb52184/pg-escalated/ready.log`; SHA256 `112a08b48d45f757f863bc576c862c39ab43bbebbaad41b8010b7c495cf3ad50`.
- Post-stop status log: `/private/tmp/sawa-c2-ci-postintegration-bb52184/pg-escalated/status-after-stop.log`; SHA256 `e138ccb54fdb08283c6eb21159c53207bbbbf07077246e2804e18d22efe6e250`.
- Post-stop socket log: `/private/tmp/sawa-c2-ci-postintegration-bb52184/pg-escalated/socket-after-stop.log`; SHA256 `d6098d353447af9518918a37edf3e459ee042b27a1c55770452c4411bdd16dbc`.
