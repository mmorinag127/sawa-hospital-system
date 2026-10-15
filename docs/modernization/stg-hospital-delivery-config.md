# Staging hospital delivery deployment contract

Scope: `mmorinag127/sawa-hospital-system`, `develop`, existing staging WIF in
`deploy-stg.yml`, project `sawahospitalsystem`, region `asia-northeast2`.
No production changes, alternate credentials, local bootstrap/deploy, or DB writes.
The failure is missing runtime transport deployment configuration, not missing OCR
input. The failing upload saved a PDF before raising before the UploadedPdf transaction.

## Resources

| Flow | Queue | Task SA ID | Drain SA ID | Scheduler job |
| --- | --- | --- | --- | --- |
| ingest | hospital-ingest-stg | hosp-ingest-task-stg | hosp-ingest-drain-stg | hospital-ingest-drain-stg |
| workflow OCR | hospital-workflow-ocr-stg | hosp-workflow-ocr-task-stg | hosp-workflow-ocr-drain-stg | hospital-workflow-ocr-drain-stg |

All ordinary SA emails end in `@sawahospitalsystem.iam.gserviceaccount.com`.
Worker runtime remains `worker-exec-stg`; neither school queues nor school identities
are reused. Exact worker URL/audience:
`https://worker-stg-avlnzjjrca-dt.a.run.app`.
Ordinary SA descriptors supply enabled status, project, email and actual uniqueId;
subjects are never constants. Google-managed agent emails derive from the actual
projectNumber and must occur in unconditional project IAM service-agent bindings.
Do not describe Google-managed agents as ordinary project SAs or grant access to them.
Read-only observation: projectNumber `167795504375`; direct managed-agent descriptor
reads returned `iam.serviceAccounts.get` denied. No subject is needed for these agents.

## IAM And Prerequisites

- Runtime: `roles/cloudtasks.enqueuer` and `roles/cloudtasks.viewer` on each hospital
  queue only (create, get, fullView; viewer also permits list). No taskRunner/custom role.
- Runtime: `roles/iam.serviceAccountUser` on each task SA only.
- Existing deploy SA `sawa-github-deploy-stg`: `roles/iam.serviceAccountUser` on each
  drain SA only, required to attach that identity to the Scheduler job.
- Tasks/Scheduler agents: `roles/iam.serviceAccountOpenIdTokenCreator` on the
  corresponding task/drain SA only. No broad tokenCreator grant.
- Four dedicated identities: `roles/run.invoker` on worker-stg only.
- Existing project bindings for Tasks and Scheduler service agents must already be
  present. The bootstrap never modifies project IAM or grants deployer SchedulerAdmin.
- APIs/service agents must already exist. Existing deploy credentials must permit
  Scheduler list/get/create and the resource-specific IAM/bootstrap operations.
  Scheduler authority is not present in the reported direct deploy-SA bindings;
  denial is an explicit prerequisite failure, not authorization to escalate.

The CLI wrapper suppresses raw cloud stderr. Denial, malformed descriptors, missing
Google service-agent bindings, or unexpected existing resource configuration stop
before revision replacement. Absence is determined only by successful list results;
errors are never interpreted as absence. Existing mismatched resources are not replaced.

## Timing And Runtime Configuration

Both queues use concurrency 2 and one dispatch/second. Task dispatch deadlines are
1800 seconds, within the application's 15..1800 range. Existing worker timeout and
upload lease remain 1800 seconds. Workflow OCR's required process lease is explicitly
2100 seconds, longer than the worker request/task deadline. No application defaults change.
Each flow has its own Scheduler job, every minute UTC, POST to its own
`/worker/{ingest,workflow-ocr}-deliveries/drain?limit=10`, deadline 180 seconds.
Schedulers use their respective drain identities, never task identities.

For each of `HOSPITAL_INGEST_DELIVERY_` and `HOSPITAL_WORKFLOW_OCR_DELIVERY_`, set:
`PROJECT_ID`, `LOCATION`, `QUEUE`, `TARGET_URL`, `SERVICE_ACCOUNT_EMAIL`, `AUDIENCE`,
`DISPATCH_DEADLINE_SECONDS`; and each `WORKER_`/`DRAIN_` trust tuple of `ISSUER`,
`AUDIENCE`, `EMAIL`, `SUBJECT`. Also set
`HOSPITAL_WORKFLOW_OCR_DELIVERY_PROCESS_LEASE_SECONDS`. Total: 31 variables.
Ingest's task identity also covers its existing auto-LLM successor endpoint.

## Gate And Evidence

Actions runs bootstrap after existing WIF/setup-gcloud and before the worker deploy.
Only verified descriptors produce `HOSPITAL_DELIVERY_ENV`. The deploy step requires
that value to equal `hospital-delivery-artifacts/verified-env.txt`, then appends it
to the existing `WORKER_EXTRA_ENV_VARS` passed to the unchanged deployment script.
Other environment variables remain intact. No separate revision/env update is used.
The artifact contains nonsecret env, projected resource descriptors and success/blocked
status, and is uploaded even when bootstrap fails. New schedulers may run before
the replacement revision; old missing-config handlers remain fail-closed.

Tests mock subprocess, not cloud state in a live project. They cover both flow contracts,
CLI argument support, exact context, descriptor drift, authority denial, idempotency,
Google agent binding verification, and verified-env handoff. Local test success is not
an Actions bootstrap, deployment, upload/OCR, or live verification success.
Parent must review real IAM, run authorized Actions, and verify the actual operator flow.

CLI references: [queue IAM](https://docs.cloud.google.com/sdk/gcloud/reference/tasks/queues/add-iam-policy-binding),
[SA IAM](https://cloud.google.com/sdk/gcloud/reference/iam/service-accounts/add-iam-policy-binding).
The queue command does not accept `--condition`; SA/Cloud Run IAM commands do.
