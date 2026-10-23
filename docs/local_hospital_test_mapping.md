# Hospital Local Test Migration

Integration base: `c504c11cb49fcc6372f7fd11798e9cbfbd43ac56`.
Inspected remote develop: `1097e55ff2668d909674e30c2446b585f814b938`.
The two additional commits are `f8c2d201` and merge `1097e55f`; they change
copy-refresh implementation/tests, not workflows. Their implementation has not
been copied into this worktree; the parent integrates onto the latest develop.

The seven removed test workflows are preserved as original blobs under
`local-checks/legacy/`. The native sibling workflow is retained only as an
inventory source from `25050a80d60123f05033775216e26a6dfa8b44db`
(`origin/codex/modernization-native-ci-20261013`). Its four implementation
scripts were removed from this worktree; none are included in this patch.
The native harness and workflow are absent from current develop. This unmerged
inventory is not a mandatory release command; its tests are not claimed as run.
`legacy/deploy-stg.yml` is an additional baseline fixture for retired diagnostic
contracts, not an executable GitHub workflow.

## Command Mapping

Each regular backend pytest file is collected by `task backend_test` once.
Frontend `test:config` already includes root `tests/*.test.cjs`, including dataset,
shell-controls and API-session contracts; those are not run again individually.
Frontend lint, config, inspection-demand-read, typecheck and E2E each run once.
Dedicated PG, image and live-UI commands retain their distinct checks.
The live-UI command is recorded under `post_deploy_commands` and run locally
after deployment. It checks the actual deployed source; it cannot be a
prerequisite for deploying that same source. Pre-deploy receipts cover only
`commands` and never claim that live verification already passed.

| Original Workflow | Original Step | Local Manifest Command |
| --- | --- | --- |
| ci.yml | backend: Run backend tests (US1-4) | `backend-tests` |
| ci.yml | backend: Verify initial saved-source preservation | `backend-tests` |
| ci.yml | backend: Verify OCR review-resolution root regression | `backend-tests` |
| ci.yml | backend: Verify portal login and server session | `backend-tests` |
| ci.yml | backend: Verify staging copy refresh lifecycle and native PostgreSQL isolation | `backend-tests` |
| verify-staging-data.yml | verify: Run isolated dataset regressions | `backend-tests` |
| ci.yml | frontend: Frontend lint | `frontend-tests` |
| ci.yml | frontend: Frontend route contract tests | `frontend-tests` |
| ci.yml | frontend: Frontend typecheck | `frontend-tests` |
| verify-staging-data.yml | verify: Run isolated dataset regressions | `frontend-tests` |
| ci.yml | backend: Check backend undefined names | `backend-lint-audit` |
| ci.yml | backend: Backend dependency audit | `backend-lint-audit` |
| ci.yml | frontend: Frontend dependency audit | `frontend-audit` |
| ci.yml | infra: Infra workflow guard tests | `workflow-security-contracts` |
| ci.yml | infra: Verify TLS trust and workflow guards | `workflow-security-contracts` |
| ci.yml | backend: Verify parallel CI coverage and required gate | `workflow-security-contracts` |
| ci.yml | backend: Verify serving and frozen image dependency alignment | `workflow-security-contracts` |
| ci.yml | backend: Verify per-test selection safety | `impact-contracts` |
| ci.yml | frontend: Verify Node test selection safety | `impact-contracts` |
| ci.yml | backend: Verify packaged hospital baseline migration | `packaged-baseline` |
| ci.yml | backend: Verify staging copy refresh lifecycle and native PostgreSQL isolation | `copy-refresh-owned-postgres` |
| modernization-image-ci.yml | frontend-image: Build actual frontend image and verify vendored dependency | `modernization-image-proof` |
| modernization-pg-output.yml | pg-output-boundary: Formal Alembic migration | `modernization-pg-output` |
| modernization-pg-output.yml | pg-output-boundary: Real PG transaction boundaries | `modernization-pg-output` |
| modernization-pg-output.yml | formal-pg-chain: Start exclusively owned Unix socket PG16 | `modernization-pg-output` |
| modernization-pg-output.yml | formal-pg-chain: Existing formal migration module (all cases) | `modernization-pg-output` |
| modernization-pg-output.yml | formal-pg-chain: Stop only owned PG16 | `modernization-pg-output` |
| live-schema-adoption-test.yml | live-schema-adoption: Generate fresh PostgreSQL 0035 contract and run adoption tests | `live-schema-adoption` |
| verify-stg-ocr-ui.yml | verify: Validate existing staging OCR artifact provenance | `stg-ocr-ui-readonly` |
| verify-stg-ocr-ui.yml | verify: Verify current web before and after existing read-only UI checks | `stg-ocr-ui-readonly` |
| modernization-readonly-pg.yml | readonly-boundary: Existing selected module, all SQLite and PostgreSQL cases | `hospital-readonly-pg` |
| modernization-readonly-pg.yml | readonly-boundary: Existing selected module, all SQLite and PostgreSQL cases | `hospital-c1-pg` |
| modernization-readonly-pg.yml | readonly-boundary: Existing selected module, all SQLite and PostgreSQL cases | `hospital-c1-guard-pg` |
| modernization-readonly-pg.yml | readonly-boundary: Existing selected module, all SQLite and PostgreSQL cases | `hospital-menu-identity-pg` |

`Verify staging copy refresh lifecycle and native PostgreSQL isolation` is split:
its ordinary pytest files are included in `backend-tests`; its explicitly owned
PG16 fixture and OCR-producer unittest run once in `copy-refresh-owned-postgres`.
The schema-adoption and formal-chain commands preserve generated-contract checks,
all explicit cases, evidence and owned PG shutdown. The image command preserves
source preparation, Docker context comparisons, container dependency checks and
vendor byte parity. Live OCR UI keeps provenance and both before/after source checks.

## Local Prerequisites

Dependency installation is preparation, not a test command or successful receipt.
Use local Linux for Linux-only fixtures and native image checks. Install the
current checkout's backend dev/locked dependencies, PyYAML, ruff, pip-audit,
pytest-testmon, frontend lockfile dependencies, Playwright/WebKit, Docker and PG16.
The retired CI's CPU tensor preparation required `torch==2.10.0+cpu`,
`huggingface-hub==0.36.2` and `safetensors==0.7.0`; provision these before the
corresponding OCR contracts. No install step is counted as a domain test.
The archived native sibling is not part of this checkout's executable test set.

`TEST_BOOTSTRAP_DB_URI` must identify an explicitly owned local bootstrap test DB.
`REFRESH_TEST_PG_BIN` and `PG_BIN` select local PG16 binaries. The unchanged C1
fixtures retain their fixed owned paths and refuse existing/aliased profiles.
`local-image-tls` requires local same-source image names in
`SAWA_LOCAL_TEST_IMAGE_BACKEND`, `SAWA_LOCAL_TEST_IMAGE_OCR_PIPELINE` and
`SAWA_LOCAL_TEST_IMAGE_AGENT_RUNTIME`; SHA labels must match HEAD.
Repeated image IDs are tested once. Frontend image TLS is checked with its
single local frontend image proof. Hosted image probes were moved here.

GitHub-specific owner/triggering-owner dispatch, remote HEAD equality, OIDC,
production approval and actual rollout checks remain in deploy workflows.
Archived diagnostic workflow tests are explicitly historical contract fixtures;
current receipt/source/auth gates are exercised by `test_local_hospital_test_runner.py`.
The PG transport regression tests still cover PID/role/database/OWNER mismatch,
startup failure, nonzero pytest, stale socket and unsuccessful cleanup.

## Validation Scope

Local migration/security regression is separate from full PG/domain/image/live-UI
execution. No complete manifest run or signed deploy receipt was produced.
The worktree remains intentionally uncommitted; the parent reviews and integrates
before running a clean exact-source signed full test entry.
