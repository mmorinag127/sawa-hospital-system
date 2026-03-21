# Infra Terraform (hospital-order-system)

## Structure

- `versions.tf`, `backend.tf`, `variables.tf`: provider/setup shared across envs.
- `envs/{dev,stg,prod}`: per-environment root modules (state/workspace separation required).
- `modules/*`: feature modules (apis, storage, firestore, secrets, cloudrun, pubsub, scheduler, iam, monitoring).

## Usage (baseline)

1. Create the tfstate bucket via `bootstrap/` (local state):
   - `cd bootstrap`
   - `cp terraform.tfvars.example terraform.tfvars` (set `bucket_name`, `project_id`)
   - `tofu init && tofu plan` (apply when ready)
2. Create `backend.hcl` from `envs/*/backend.hcl.example` and set the state bucket/prefix.
3. Set env variables or tfvars (use `terraform.tfvars.example` as a template).
4. For each env: `cd envs/dev && tofu init -backend-config=backend.hcl && tofu plan`.
4. Ensure apply is idempotent; rerun `terraform plan` should be no-op.
5. Use `terraform.tfvars.example` in each env as a template for real values.

## Notes

- Secrets must not be stored in state; only create containers in Secret Manager.
- Intake is manual-upload based; no Gmail watch scheduler is required.
- Pub/Sub push must use dedicated SA with `roles/run.invoker`; worker endpoints require auth.
- raw bucket lifecycle: 1〜2ヶ月保持（configurable）。
