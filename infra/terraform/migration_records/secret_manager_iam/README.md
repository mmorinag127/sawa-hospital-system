# Secret Manager IAM Phase-1 Verification Records

These files are the required phase gate for the Secret Manager IAM migration workflow.

- `stg-phase1-verified.json`
- `prod-phase1-verified.json`

`phase2` in `.github/workflows/secret-manager-iam-migration.yml` refuses to run unless the target environment already has its matching tracked record file.

## Required JSON shape

```json
{
  "environment": "stg",
  "phase": "phase1",
  "retain_legacy_project_secret_accessor": true,
  "github_environment": "staging",
  "phase1_apply_run_id": 123456789,
  "phase1_apply_run_url": "https://github.com/OWNER/REPO/actions/runs/123456789",
  "phase1_apply_head_sha": "0123456789abcdef0123456789abcdef01234567",
  "phase1_plan_sha256": "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
  "verification_completed": true,
  "verified_at": "2026-07-30T09:10:11Z",
  "verified_by": "reviewer@example.com",
  "verification_summary": "Secret access, startup, Google login, and CI checks passed."
}
```

## Contract

- `environment` must be `stg` or `prod` and must match the filename.
- `phase` must be `phase1`.
- `retain_legacy_project_secret_accessor` must stay `true`.
- `github_environment` must be `staging` for `stg` and `production` for `prod`.
- `phase1_apply_run_id` must be the successful Actions apply run id.
- `phase1_apply_run_url` must end with `/actions/runs/<phase1_apply_run_id>`.
- `phase1_apply_head_sha` must be the exact head SHA that produced the successful phase-1 apply.
- `phase1_plan_sha256` must be copied from the uploaded plan artifact checksum for that run.
- `verification_completed` must be `true`.
- `verified_at`, `verified_by`, and `verification_summary` are required. Leave phase 2 blocked until those fields are truthful.
