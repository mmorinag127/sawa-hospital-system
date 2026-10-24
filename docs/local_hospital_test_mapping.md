# Hospital Local Test Migration

Latest diagnostic source: `7be11d7b67945b7873484307ee162acefcc2853b`.
That source is local, not integrated into develop. Its diagnostic batch failed; no signed
receipt or successful staging acceptance is claimed. The current 30-command
definition and failure-class repairs still need a clean integrated-source run.

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

Each general backend pytest file is collected by the backend command once.
The complete PG/read-only/adoption modules listed in
`scripts/run_local_backend_tests.py:DEDICATED_MODULES` run only in their named
dedicated commands, including their SQLite and negative ownership cases. They
are not collected a second time without their required database profile.
The runner partition contract verifies every excluded file's dedicated mapping.
Frontend `test:config` already includes root `tests/*.test.cjs`, including dataset,
shell-controls and API-session contracts; those are not run again individually.
Frontend lint, config, inspection-demand-read, typecheck and E2E each run once.
Dedicated PG, image and live-UI commands retain their distinct checks.
PG runners create a fresh physical PG16 cluster/socket for each local run,
validate source OWNER, postmaster PID, server and database identities, and verify
shutdown. Historical fixed paths and unverified server profiles are not used.
Frontend E2E uses a hash-checked tracked-source copy and independent installed
dependencies; ignored private environment files are not copied. Next-generated
files cannot modify the source checkout, whose clean-source guard is unchanged.
Native workflow cases run serially inside the matching Linux agent runtime with
a separately owned local PostgreSQL network namespace. The test-only image adds
explicit gateway test dependencies, not changes to the serving image/native
environments. Both containers receive cleanup attempts even if one fails;
cleanup failures prevent a successful receipt.
`runtime-image-audits` additionally inventories and audits six actual isolated
image environments: backend, producer and agent hospital/gateway/shift/school.
`runtime-audit-contracts` verifies source refusal and unsuccessful-audit behavior.
Neither command installs an auditor into, or starts the application in, an image.
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
| ci.yml | frontend: Frontend lint | `frontend-lint` |
| ci.yml | frontend: Frontend route contract tests | `frontend-config` |
| ci.yml | frontend: Frontend typecheck | `frontend-types` |
| verify-staging-data.yml | verify: Run isolated dataset regressions | `frontend-e2e` |
| ci.yml | backend: Check backend undefined names | `backend-lint` |
| ci.yml | backend: Backend dependency audit | `backend-audit` |
| ci.yml | frontend: Frontend dependency audit | `frontend-audit` |
| ci.yml | infra: Infra workflow guard tests | `secret-iam-contracts` |
| ci.yml | infra: Verify TLS trust and workflow guards | `tls-trust-contracts` |
| ci.yml | infra: Verify TLS trust and workflow guards | `tls-workflow-contracts` |
| ci.yml | backend: Verify parallel CI coverage and required gate | `backend-partition-contracts` |
| ci.yml | backend: Verify serving and frozen image dependency alignment | `runtime-cv-contracts` |
| ci.yml | backend: Verify per-test selection safety | `impact-contracts` |
| ci.yml | frontend: Verify Node test selection safety | `impact-contracts` |
| ci.yml | backend: Verify packaged hospital baseline migration | `agent-assistant-tests` |
| ci.yml | backend: Verify staging copy refresh lifecycle and native PostgreSQL isolation | `copy-refresh-owned-postgres` |
| modernization-image-ci.yml | frontend-image: Build actual frontend image and verify vendored dependency | `modernization-image-proof` |
| modernization-pg-output.yml | pg-output-boundary: Formal Alembic migration | `modernization-pg-output` |
| modernization-pg-output.yml | pg-output-boundary: Real PG transaction boundaries | `modernization-pg-output` |
| modernization-pg-output.yml | formal-pg-chain: Start exclusively owned Unix socket PG16 | `modernization-pg-output` |
| modernization-pg-output.yml | formal-pg-chain: Existing formal migration module (all cases) | `modernization-pg-output` |
| modernization-pg-output.yml | formal-pg-chain: Stop only owned PG16 | `modernization-pg-output` |
| live-schema-adoption-test.yml | live-schema-adoption: Generate fresh PostgreSQL 0035 contract and run adoption tests | `live-schema-adoption` |
| modernization-readonly-pg.yml | readonly-boundary: Existing selected module, all SQLite and PostgreSQL cases | `hospital-readonly-pg` |
| modernization-readonly-pg.yml | readonly-boundary: Existing selected module, all SQLite and PostgreSQL cases | `hospital-c1-pg` |
| modernization-readonly-pg.yml | readonly-boundary: Existing selected module, all SQLite and PostgreSQL cases | `hospital-c1-guard-pg` |
| modernization-readonly-pg.yml | readonly-boundary: Existing selected module, all SQLite and PostgreSQL cases | `hospital-menu-identity-pg` |
| verify-stg-ocr-ui.yml | verify: Validate existing staging OCR artifact provenance | `stg-ocr-ui-readonly` |
| verify-stg-ocr-ui.yml | verify: Verify current web before and after existing read-only UI checks | `stg-ocr-ui-readonly` |

The 28 pre-deploy command groups are independent. Frontend lint,
config, read-only browser inspection, types and E2E cannot mask one another.
Backend lint/audit, each security contract and the producer suite also have
separate exit/log records. All groups run sequentially; ordinary failures are
collected, but a source change aborts the batch without a success receipt.

Schema/adoption and formal-chain commands preserve generated contracts, all
cases, evidence and owned PG shutdown. Image proof preserves Docker context
comparisons, runtime checks, vendor byte parity and TLS. Local OCR UI remains
post-deploy with provenance and before/after serving-source checks.

## Local Prerequisites

Dependency installation is preparation, not a test command or successful receipt.
Use Linux containers for image checks and native PostgreSQL16 tools for PG checks. Install the
current checkout's backend dev/locked dependencies, PyYAML, ruff, pip-audit,
pytest-testmon==2.2.0, frontend lockfile dependencies, Playwright/WebKit, Docker and PG16.
Use the serving/frozen lockfiles for OCR runtime dependencies, not the retired
CI's different tensor pin. The f50d8118 audit reports vulnerabilities in torch
2.6.0; updating it requires result parity and is not waived by this migration.
No install step is counted as a domain test.
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
The parent reviews and integrates all known repairs before running the next
clean exact-source signed full test entry. A failed diagnostic is never a receipt.
