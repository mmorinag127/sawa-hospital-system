# Secret Manager IAM Two-Phase Actions Runbook

This runbook is the exact operator sequence for the Secret Manager IAM migration described in [docs/google_auth_migration_and_recovery.md](../google_auth_migration_and_recovery.md). The migration is Actions-only. Do not run `terraform apply` locally, do not dispatch prod from any branch other than `release/prod-*`, and do not combine phase 1 and phase 2 in one apply.

## Workflow

- Workflow file: `.github/workflows/secret-manager-iam-migration.yml`
- Workflow name in GitHub Actions: `Secret Manager IAM Migration`
- Supported inputs:
  - `target_environment`: `stg` or `prod`
  - `phase`: `phase1` or `phase2`
  - `mode`: `plan` or `apply`
  - `phase1_record_path`: required only for `phase2`

## Required GitHub configuration

GitHub -> `Settings` -> `Environments`

- Create `staging`
- Create `production`
- Configure required reviewers on both environments

GitHub -> `Settings` -> `Secrets and variables` -> `Actions` -> `Environments` -> `staging`

- Secret `GCP_WORKLOAD_IDENTITY_PROVIDER`
- Secret `GCP_TERRAFORM_SERVICE_ACCOUNT`
- Secret `TF_BACKEND_HCL`
- Secret `TF_TFVARS`

GitHub -> `Settings` -> `Secrets and variables` -> `Actions` -> `Environments` -> `production`

- Secret `GCP_WORKLOAD_IDENTITY_PROVIDER`
- Secret `GCP_TERRAFORM_SERVICE_ACCOUNT`
- Secret `TF_BACKEND_HCL`
- Secret `TF_TFVARS`

`TF_BACKEND_HCL` must contain the exact `backend.hcl` content for the target env directory. `TF_TFVARS` must contain the exact `terraform.tfvars` content for the target env directory. The workflow writes these secrets back to `backend.hcl` and `terraform.tfvars` on the runner so the existing backend/tfvars convention stays intact.

## Recommended Workload Identity constraints

Use workflow-specific providers so prod infra apply cannot piggyback on the app deploy workflow.

Recommended staging attribute condition:

```text
assertion.repository == 'OWNER/REPO' &&
assertion.ref == 'refs/heads/develop' &&
assertion.workflow_ref == 'OWNER/REPO/.github/workflows/secret-manager-iam-migration.yml@refs/heads/develop'
```

Recommended production attribute condition:

```text
assertion.repository == 'OWNER/REPO' &&
assertion.ref.startsWith('refs/heads/release/prod-') &&
assertion.workflow_ref.startsWith('OWNER/REPO/.github/workflows/secret-manager-iam-migration.yml@refs/heads/release/prod-')
```

`GCP_TERRAFORM_SERVICE_ACCOUNT` should point to the service account that is already authorized for Terraform infra apply in the target environment. Do not reuse any local key-based path.

## Source branch rules

- `stg` must run from `develop`, and the workflow refuses any commit that is not exactly `origin/develop`.
- `prod` must run from `release/prod-*`, and the workflow refuses any commit that is not exactly `origin/release/prod-*`.
- `prod` additionally requires `origin/develop` to be an ancestor of the selected release branch.

## Record files required before phase 2

Phase 2 is blocked until a tracked, committed phase-1 verification record exists at one of these exact paths:

- `infra/terraform/migration_records/secret_manager_iam/stg-phase1-verified.json`
- `infra/terraform/migration_records/secret_manager_iam/prod-phase1-verified.json`

The record format is documented in [infra/terraform/migration_records/secret_manager_iam/README.md](../../infra/terraform/migration_records/secret_manager_iam/README.md).

The workflow checks all of the following before allowing `phase2`:

- the record file exists at the exact path for the target env
- the record is tracked by git
- the record says `phase=phase1`
- the record says `retain_legacy_project_secret_accessor=true`
- the record says verification is complete
- the referenced GitHub Actions run id exists and concluded with `success`
- the referenced run used the `Secret Manager IAM Migration` workflow on the correct branch class

If any of those checks fail, `phase2` stops before Terraform plan.

## Operator sequence

### Staging phase 1

1. Update `develop` so the workflow code and Terraform code are present.
2. GitHub -> `Actions` -> `Secret Manager IAM Migration` -> `Run workflow`.
3. Select branch `develop`.
4. Set `target_environment=stg`, `phase=phase1`, `mode=plan`.
5. Approve the `staging` environment so the plan job can access its secrets.
6. Review the uploaded artifact `secret-manager-iam-stg-phase1`. Verify:
   - `secret-manager-iam.tfplan.sha256` exists
   - `secret-manager-iam.txt` only contains the expected phase-1 per-secret IAM additions and any allowed legacy accessor creates
   - there are no unrelated updates, replacements, or deletes
7. Re-run the workflow with the same inputs but `mode=apply`.
8. Approve the `staging` environment for plan, review the artifact again, then approve the second `staging` gate for apply.
9. After apply succeeds, verify service startup, Secret access, Google login, and CI validation.
10. Commit `infra/terraform/migration_records/secret_manager_iam/stg-phase1-verified.json` on `develop` with the successful run id, head SHA, plan SHA256, and verification notes.

### Staging phase 2

1. Confirm `develop` already contains `infra/terraform/migration_records/secret_manager_iam/stg-phase1-verified.json`.
2. GitHub -> `Actions` -> `Secret Manager IAM Migration` -> `Run workflow`.
3. Select branch `develop`.
4. Set `target_environment=stg`, `phase=phase2`, `mode=plan`, `phase1_record_path=infra/terraform/migration_records/secret_manager_iam/stg-phase1-verified.json`.
5. Approve the `staging` environment and review the artifact `secret-manager-iam-stg-phase2`.
6. Confirm the plan deletes only the legacy project-wide accessor bindings.
7. Re-run with the same inputs but `mode=apply`.
8. Approve `staging` for plan, review the artifact again, approve `staging` for apply, then re-run the same post-apply verification set.

### Production phase 1

1. Create or update a named release branch from `origin/develop`, for example `release/prod-20260730-secret-manager-iam`.
2. Push that branch so `origin/release/prod-20260730-secret-manager-iam` exactly matches the intended source.
3. GitHub -> `Actions` -> `Secret Manager IAM Migration` -> `Run workflow`.
4. Select that `release/prod-*` branch.
5. Set `target_environment=prod`, `phase=phase1`, `mode=plan`.
6. Approve `production` for plan, then review `secret-manager-iam-prod-phase1`.
7. Re-run with `mode=apply`.
8. Approve `production` for plan, review the artifact again, then approve `production` for apply.
9. After apply succeeds, verify service startup, Secret access, Google login, and CI validation.
10. Commit `infra/terraform/migration_records/secret_manager_iam/prod-phase1-verified.json` to the same `release/prod-*` branch.

### Production phase 2

1. Confirm the release branch already contains `infra/terraform/migration_records/secret_manager_iam/prod-phase1-verified.json`.
2. GitHub -> `Actions` -> `Secret Manager IAM Migration` -> `Run workflow`.
3. Select the same `release/prod-*` branch.
4. Set `target_environment=prod`, `phase=phase2`, `mode=plan`, `phase1_record_path=infra/terraform/migration_records/secret_manager_iam/prod-phase1-verified.json`.
5. Approve `production` for plan and review `secret-manager-iam-prod-phase2`.
6. Confirm the plan deletes only legacy project-wide accessor bindings.
7. Re-run with `mode=apply`.
8. Approve `production` for plan, review the artifact again, approve `production` for apply, then re-run the same post-apply verification set.

## Stop conditions

- If plan shows any resource change outside the Secret Manager IAM allowlist, stop. The workflow is designed to fail instead of applying unrelated drift.
- If phase 1 verification fails, do not create the record file and do not run phase 2.
- If `phase2` cannot validate the recorded successful phase-1 run, stop and repair the record or rerun phase 1. Do not bypass the gate.
