# Tasks: GCPインフラ/IaCブートストラップ（hospital-order-system）

**Input**: Design documents from `/specs/001-spec-update/`  
**Prerequisites**: plan.md, spec.md

**Tests**: Validate with `terraform plan/apply` idempotence, Pub/Sub→Cloud Run push integration, Scheduler watch更新動作、outputs確認。

**Organization**: Tasks grouped by user story to enable independent implementation and testing. Ensure env/state分離、最小権限IAM、secret値はtfstate/ログ非出力、watch更新≤7日。

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Setup (Shared Infrastructure)

- [ ] T001 Install Terraform/Terragrunt if used; create `infra/terraform` scaffold with env directories (`envs/dev`, `envs/stg`, `envs/prod`) and backend config placeholders.
- [ ] T002 Define provider versions/backend in `infra/terraform/versions.tf` and `backend.tf` (remote state, per-env state separation).
- [ ] T003 [P] Add README/runbook skeleton in `infra/terraform/README.md` for apply steps, required vars, and state/workspace usage.

## Phase 2: Foundational (Blocking)

- [ ] T004 Implement providers and common variables in `infra/terraform/main.tf` and `variables.tf` (project_id, region, env, naming).
- [ ] T005 [P] Add API enablement module (enable Cloud Run, Pub/Sub, Scheduler, Secret Manager, Firestore, Storage, Document AI) in `infra/terraform/modules/apis`.
- [ ] T006 [P] Configure IAM helper locals for service accounts and least-privilege roles in `infra/terraform/modules/iam`.

**Checkpoint**: Foundation ready; no user story work until Phase 2 complete.

## Phase 3: US1 1コマンドでdev環境をIaC構築 (P1) 🎯

### Tests
- [ ] T007 [US1] Terraform plan/apply in `infra/terraform/envs/dev` succeeds and re-plan shows no diff.
- [ ] T008 [P] [US1] terraform output exposes Cloud Run URL, Pub/Sub topic, service accounts, bucket names, Processor ID placeholders.

### Implementation
- [ ] T009 [US1] Add Storage buckets (raw/templates/exports) with lifecycle (raw 1–2ヶ月) in `infra/terraform/modules/storage`.
- [ ] T010 [US1] Add Firestore enablement in `infra/terraform/modules/firestore`.
- [ ] T011 [US1] Add Secret Manager “containers” (no secret values) in `infra/terraform/modules/secrets`.
- [ ] T012 [US1] Add Cloud Run services (web/worker) and execution SAs in `infra/terraform/modules/cloudrun`.
- [ ] T013 [US1] Wire modules into `infra/terraform/envs/dev/main.tf` with env-specific names/state.

**Checkpoint**: US1 independently testable (apply idempotent, outputs available).

## Phase 4: US2 Pub/Sub pushでCloud Run worker認証呼び出し (P1)

### Tests
- [ ] T014 [US2] Contract test: Pub/Sub push subscription configured with auth to worker URL (no 403) using stub publish script in `infra/terraform/tests/push_publish.sh`.

### Implementation
- [ ] T015 [US2] Create Pub/Sub topic/subscription with push-config to Cloud Run worker in `infra/terraform/modules/pubsub`.
- [ ] T016 [US2] Create push-auth service account with `roles/run.invoker` binding to worker in `infra/terraform/modules/iam`.
- [ ] T017 [US2] Add sample publish helper/runbook in `docs/runbooks/pubsub-push.md`.

**Checkpoint**: US2 independently testable (push reaches worker with auth).

## Phase 5: US3 Gmail watchを自動更新 (P1)

### Tests
- [ ] T018 [US3] Scheduler dry-run log shows watch refresh call executes within ≤7日周期 (ideally daily) in infra/terraform/modules/scheduler.
- [ ] T019 [P] [US3] Verify watch refresh results logged and failure path notifies (placeholder notification target) in infra/terraform/modules/scheduler and docs/runbooks/gmail-watch.md.

### Implementation
- [ ] T020 [US3] Add Cloud Scheduler job in `infra/terraform/modules/scheduler` to call watch-refresh endpoint (env-specific URL/SA).
- [ ] T021 [US3] Add IAM for scheduler SA to invoke watch endpoint in `infra/terraform/modules/iam`.
- [ ] T022 [US3] Document runbook for initial Gmail OAuth/watch setup in `docs/runbooks/gmail-watch.md` (note: manual approval as needed).

**Checkpoint**: US3 independently testable (scheduled watch refresh configured and observable).

## Phase 7: US5 テンプレ/出力格納先を自動準備 (P2)

### Tests
- [ ] T026 [US5] Validate templates/exports buckets exist and paths are emitted in outputs (check terraform output).

### Implementation
- [ ] T027 [US5] Ensure templates/exports buckets with proper prefixes/ACLs in storage module and outputs include paths.
- [ ] T028 [US5] Document label/納品/総量出力パス conventions in `docs/runbooks/outputs-layout.md`.

**Checkpoint**: US5 independently testable (buckets ready with documented paths).

## Phase 8: Polish & Cross-Cutting Concerns

- [ ] T029 Monitoring/alerts: set up minimum detectors (Cloud Run errors, Pub/Sub backlog, Scheduler/watch failures) in `infra/terraform/modules/monitoring`.
- [ ] T030 Security/IAM review: enforce auth on worker endpoints, verify least-privilege bindings in `infra/terraform/modules/iam`.
- [ ] T031 Cleanup docs: update `infra/terraform/README.md` with apply steps, env switching, secret handling, and watch refresh notes.

## Dependencies & Execution Order

- Phase 1 → Phase 2 → US1 → US2 → US3 → US4 → US5 → Polish.
- US2 depends on Cloud Run/SA definitions from US1. US3 depends on Cloud Run endpoint/SA. US4/US5 depend on storage/outputs and env wiring.

## Parallel Opportunities

- Phase 1: T003 can run in parallel after T001 scaffold.
- Phase 2: T005/T006 in parallel after providers set.
- US1: T009–T012 in parallel (modules), then T013 wiring.
- US2: T015/T016 in parallel; T017 independent.
- US3: T020/T021 in parallel; T022 independent doc.
- US4/US5 mostly independent after storage/outputs exist.

## Implementation Strategy

### MVP First (US1 Only)
1. Complete Phase 1–2.
2. Deliver US1 (idempotent apply + outputs) for dev env.

### Incremental Delivery
1. Add US2 (Pub/Sub push auth) then US3 (watch refresh).
2. Add US4 (Document AI references) and US5 (templates/exports paths).
3. Polish with monitoring/IAM/docs.
