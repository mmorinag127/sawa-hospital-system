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
