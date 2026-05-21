# Deploy agent quick reference

Last updated: 2026-05-21

This is the short deploy rule page for agents working in this repo.

## Normal deploy path

- stg deploy: GitHub Actions `Deploy Staging` from `origin/develop` only.
- prod deploy: GitHub Actions `Deploy Production` from `release/prod-*` only.
- Prod release branch must contain `origin/develop`.
- Do not deploy stg/prod directly from a local worktree during normal work.
- Do not deploy from detached HEAD.

## Emergency deploy path

`terraform-admin@sawahospitalsystem.iam.gserviceaccount.com` is retained only as an emergency/infra break-glass path.

Important limitation:

- GCP service accounts do not support per-deploy password prompts.
- A password-like gate must be outside GCP IAM, for example an encrypted key, OS keychain access, or a local wrapper script.
- Agents must not bypass GitHub Actions by using `terraform-admin` unless the user explicitly requests an emergency deploy for the current turn.

Before any `terraform-admin` emergency deploy, state and verify:

- Why GitHub Actions cannot be used.
- Exact source commit and branch.
- Target environment and services.
- Current live Cloud Run revisions/images.
- The command that will be run.

After an emergency deploy:

- Verify live worker/web revisions and user-visible surface.
- Merge or record the deployed source back into the normal branch flow.
- Report that the deploy used the emergency path.

## Source rules

- Required stg source: `origin/develop`.
- Required prod source: `release/prod-*` created from `origin/develop`.
- Any fix in another worktree, jj commit, sibling branch, or deploy copy must be merged into the deploy source first.
- A clean worktree is not proof of deploy readiness.

## References

- Full CI/CD setup: `docs/ci_cd_deploy_guardrails_20260521.md`
- Git/JJ release discipline: `AGENTS.md`

