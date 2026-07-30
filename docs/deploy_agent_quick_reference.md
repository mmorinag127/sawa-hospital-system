# Deploy agent quick reference

Last updated: 2026-05-21

This is the short deploy rule page for agents working in this repo.

## Normal deploy path

- stg deploy: GitHub Actions `Deploy Staging` from `origin/develop` only.
- prod deploy: GitHub Actions `Deploy Production` from `release/prod-*` only.
- prod backend deploy is blocked unless `prod-db-bootstrap-gate` succeeds after the GitHub `production` environment approval.
- Prod release branch must contain `origin/develop`.
- Do not deploy stg/prod directly from a local worktree during normal work.
- Do not deploy from detached HEAD.

## Forbidden local prod deploy path

Local prod deploy is forbidden for agents and humans.

- Do not run `gcloud run deploy` for `web-prod`, `worker-prod`, or `ocr-pipeline-prod` from a local worktree.
- Do not use `terraform-admin` or runtime service accounts as an app deploy identity.
- Do not create or use service account keys for deploy.
- If GitHub Actions is unavailable, stop and report the outage. Do not invent a local bypass.

The only normal prod deploy path is GitHub Actions `Deploy Production` with the `production` environment approval.

## Source rules

- Required stg source: `origin/develop`.
- Required prod source: `release/prod-*` created from `origin/develop`.
- Any fix in another worktree, jj commit, sibling branch, or deploy copy must be merged into the deploy source first.
- A clean worktree is not proof of deploy readiness.

## References

- Full CI/CD setup: `docs/ci_cd_deploy_guardrails_20260521.md`
- Git/JJ release discipline: `AGENTS.md`
