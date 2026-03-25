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

## Current staging target

- `stg` is currently aimed at `sawahospitalsystem / asia-northeast2`.
- `stg` shares the same GCP project as `prod`, so do not re-manage project-wide APIs from the `stg` state.
- Leave `project_services = []` and `firestore_enabled = false` in `envs/stg/terraform.tfvars`.
- Keep Firestore collection names environment-scoped (`templates-stg`, `jobs-stg`, `facilities-stg`, etc.).

## Current staging commands

`stg` backend init needs either ADC or `GOOGLE_APPLICATION_CREDENTIALS` with access to the tfstate bucket. The current repo also carries `project_number` explicitly so Cloud Scheduler IAM does not depend on a `google_project` data lookup.

```bash
cd envs/stg
GOOGLE_APPLICATION_CREDENTIALS=/path/to/infra-admin-key.json \
tofu init -backend-config=backend.hcl -input=false

GOOGLE_APPLICATION_CREDENTIALS=/path/to/infra-admin-key.json \
tofu plan -input=false -lock=false -var-file=terraform.tfvars
```

Task wrapper:

```bash
task infra_stg_plan
task infra_stg_apply
```

## Notes

- Secrets must not be stored in state; only create containers in Secret Manager.
- Intake is manual-upload based; no Gmail watch scheduler is required.
- Pub/Sub push must use dedicated SA with `roles/run.invoker`; worker endpoints require auth.
- raw bucket lifecycle: 1〜2ヶ月保持（configurable）。
