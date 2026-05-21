# CI/CD deploy guardrails

Last updated: 2026-05-21

## Goal

Sawa deploy source must be constrained by the platform, not by memory or manual operation.

- stg deploys only from `origin/develop`.
- prod deploys only from a named `release/prod-*` branch that contains `origin/develop`.
- prod requires GitHub Environment approval before Cloud Run is changed.
- deploy credentials live in GitHub Actions via Google Workload Identity Federation, not in a local key file.

## Repository changes

The repository includes these workflows:

- `.github/workflows/ci.yml`
  - Runs backend/frontend checks on pushes to `develop`, `main`, `master`, and `release/prod-*`.
  - Still runs on pull requests, but PRs are not required for a single-operator flow.
- `.github/workflows/deploy-stg.yml`
  - Runs on pushes to `develop` when deploy-relevant paths changed.
  - Can also be started manually with `workflow_dispatch`.
  - Fails if the checked-out source is not exactly `origin/develop`.
  - Builds only the changed surface when possible.
- `.github/workflows/deploy-prod.yml`
  - Runs only manually.
  - Fails unless the selected branch is `release/prod-*`.
  - Fails unless `origin/develop` is an ancestor of the selected release branch.
  - Requires the GitHub `production` Environment before deploying.

## Required GitHub settings

### Environments

Create this GitHub Environment:

- Name: `production`
- Recommended protection: required reviewer = your GitHub user

With one operator, this still avoids accidental prod deploys because a production run must be explicitly approved in GitHub Actions.

### Actions secrets

Create these repository secrets under:

`Settings` -> `Secrets and variables` -> `Actions` -> `Repository secrets`

- `GCP_WORKLOAD_IDENTITY_PROVIDER_STG`
- `GCP_DEPLOY_SERVICE_ACCOUNT_STG`
- `GCP_WORKLOAD_IDENTITY_PROVIDER_PROD`
- `GCP_DEPLOY_SERVICE_ACCOUNT_PROD`
- `STG_OPERATOR_PASSWORD`
- `PROD_OPERATOR_PASSWORD`

Set the operator password secrets to the existing stg/prod deploy-check operator password values.

Example service account secret values:

```text
GCP_DEPLOY_SERVICE_ACCOUNT_STG=sawa-github-deploy-stg@sawahospitalsystem.iam.gserviceaccount.com
GCP_DEPLOY_SERVICE_ACCOUNT_PROD=sawa-github-deploy-prod@sawahospitalsystem.iam.gserviceaccount.com
```

Example provider secret value format:

```text
projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/github-actions/providers/github-stg
projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/github-actions/providers/github-prod
```

Replace `PROJECT_NUMBER` with the numeric GCP project number.

## Required GCP setup

Run these from a terminal authenticated as a project/IAM admin.

```bash
PROJECT_ID=sawahospitalsystem
REGION=asia-northeast2
REPO=mmorinag127/sawa-hospital-system
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"

gcloud services enable \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  sts.googleapis.com \
  cloudbuild.googleapis.com \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  --project "$PROJECT_ID"

gcloud iam service-accounts create sawa-github-deploy-stg \
  --project "$PROJECT_ID" \
  --display-name "Sawa GitHub deploy stg"

gcloud iam service-accounts create sawa-github-deploy-prod \
  --project "$PROJECT_ID" \
  --display-name "Sawa GitHub deploy prod"
```

Grant deploy permissions:

```bash
for SA in sawa-github-deploy-stg sawa-github-deploy-prod; do
  DEPLOY_SA="${SA}@${PROJECT_ID}.iam.gserviceaccount.com"
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${DEPLOY_SA}" \
    --role="roles/run.admin"
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${DEPLOY_SA}" \
    --role="roles/cloudbuild.builds.editor"
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${DEPLOY_SA}" \
    --role="roles/artifactregistry.writer"
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${DEPLOY_SA}" \
    --role="roles/storage.objectAdmin"
done
```

Allow each deploy service account to deploy only with its runtime service accounts:

```bash
gcloud iam service-accounts add-iam-policy-binding \
  "web-exec-stg@${PROJECT_ID}.iam.gserviceaccount.com" \
  --project "$PROJECT_ID" \
  --member="serviceAccount:sawa-github-deploy-stg@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/iam.serviceAccountUser"

gcloud iam service-accounts add-iam-policy-binding \
  "worker-exec-stg@${PROJECT_ID}.iam.gserviceaccount.com" \
  --project "$PROJECT_ID" \
  --member="serviceAccount:sawa-github-deploy-stg@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/iam.serviceAccountUser"

gcloud iam service-accounts add-iam-policy-binding \
  "web-exec-prod@${PROJECT_ID}.iam.gserviceaccount.com" \
  --project "$PROJECT_ID" \
  --member="serviceAccount:sawa-github-deploy-prod@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/iam.serviceAccountUser"

gcloud iam service-accounts add-iam-policy-binding \
  "worker-exec-prod@${PROJECT_ID}.iam.gserviceaccount.com" \
  --project "$PROJECT_ID" \
  --member="serviceAccount:sawa-github-deploy-prod@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/iam.serviceAccountUser"
```

Create Workload Identity Federation:

```bash
gcloud iam workload-identity-pools create github-actions \
  --project "$PROJECT_ID" \
  --location global \
  --display-name "GitHub Actions"

gcloud iam workload-identity-pools providers create-oidc github-stg \
  --project "$PROJECT_ID" \
  --location global \
  --workload-identity-pool github-actions \
  --display-name "GitHub Actions stg" \
  --issuer-uri "https://token.actions.githubusercontent.com" \
  --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
  --attribute-condition "assertion.repository == '${REPO}' && assertion.ref == 'refs/heads/develop'"

gcloud iam workload-identity-pools providers create-oidc github-prod \
  --project "$PROJECT_ID" \
  --location global \
  --workload-identity-pool github-actions \
  --display-name "GitHub Actions prod" \
  --issuer-uri "https://token.actions.githubusercontent.com" \
  --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
  --attribute-condition "assertion.repository == '${REPO}' && assertion.ref.startsWith('refs/heads/release/prod-')"
```

Allow GitHub identities to impersonate the deploy service accounts:

```bash
gcloud iam service-accounts add-iam-policy-binding \
  "sawa-github-deploy-stg@${PROJECT_ID}.iam.gserviceaccount.com" \
  --project "$PROJECT_ID" \
  --role "roles/iam.workloadIdentityUser" \
  --member "principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github-actions/attribute.repository/${REPO}"

gcloud iam service-accounts add-iam-policy-binding \
  "sawa-github-deploy-prod@${PROJECT_ID}.iam.gserviceaccount.com" \
  --project "$PROJECT_ID" \
  --role "roles/iam.workloadIdentityUser" \
  --member "principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github-actions/attribute.repository/${REPO}"
```

Set the GitHub provider secrets to:

```text
GCP_WORKLOAD_IDENTITY_PROVIDER_STG=projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/github-actions/providers/github-stg
GCP_WORKLOAD_IDENTITY_PROVIDER_PROD=projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/github-actions/providers/github-prod
```

## Optional hard enforcement: remove local deploy paths

After the GitHub deploy path has succeeded once for stg and prod, audit existing direct deploy permissions:

```bash
gcloud projects get-iam-policy sawahospitalsystem \
  --flatten="bindings[].members" \
  --filter="bindings.role:(roles/run.admin roles/iam.serviceAccountUser roles/artifactregistry.writer roles/cloudbuild.builds.editor)" \
  --format="table(bindings.role,bindings.members)"
```

Then remove human/local principals that should not deploy directly. Do this after confirming which principals are still needed for Terraform or emergency maintenance.

The important enforcement point is:

- day-to-day deploy permission should belong to `sawa-github-deploy-stg` and `sawa-github-deploy-prod`
- local user credentials should not have enough permission to run `gcloud run deploy`
- any remaining `terraform-admin` path should be treated as infra/emergency only, not normal app deploy

## Normal operation

### stg

```bash
git checkout develop
git pull --ff-only origin develop
git push origin develop
```

GitHub Actions runs `Deploy Staging` automatically when deploy-relevant paths changed.

Manual stg rerun:

1. GitHub -> Actions -> `Deploy Staging`
2. `Run workflow`
3. Branch = `develop`

The workflow fails if any other branch is selected.

### prod

```bash
DATE="$(date +%Y%m%d)"
git fetch origin
git checkout -B "release/prod-${DATE}" origin/develop
git push -u origin "release/prod-${DATE}"
```

Then:

1. GitHub -> Actions -> `Deploy Production`
2. `Run workflow`
3. Branch = `release/prod-YYYYMMDD`
4. Fill `Production smoke order id` when needed
5. Approve the `production` Environment

The workflow fails if:

- the branch is not `release/prod-*`
- the selected release branch does not contain `origin/develop`
- GitHub OIDC is not coming from the allowed repo/ref

## Time and cost expectations

This uses GitHub-hosted runners plus existing Cloud Build/Cloud Run flow.

- stg backend-only change: roughly current backend build/deploy/check time
- stg frontend-only change: roughly current frontend build/deploy/check time
- stg full-stack change: backend and frontend image builds run in parallel, then deploy/check runs in order
- prod: full backend/frontend build plus one Environment approval wait

GitHub Actions cost is usually small for this repo shape because only deploy-relevant pushes trigger stg deploy, and prod is manual. GCP cost is mainly Cloud Build minutes and Artifact Registry storage for the built images. Exact monthly cost depends on deploy frequency and build duration.
