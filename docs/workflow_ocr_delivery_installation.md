# Hospital Workflow OCR Delivery Installation

The source requires an explicit Cloud Tasks deployment configuration. No inline or FastAPI background fallback is available.

Set these deployment-owned values before enabling delivery:

- `HOSPITAL_WORKFLOW_OCR_DELIVERY_PROJECT_ID`
- `HOSPITAL_WORKFLOW_OCR_DELIVERY_LOCATION`
- `HOSPITAL_WORKFLOW_OCR_DELIVERY_QUEUE`
- `HOSPITAL_WORKFLOW_OCR_DELIVERY_TARGET_URL`
- `HOSPITAL_WORKFLOW_OCR_DELIVERY_SERVICE_ACCOUNT_EMAIL`
- `HOSPITAL_WORKFLOW_OCR_DELIVERY_AUDIENCE`
- `HOSPITAL_WORKFLOW_OCR_DELIVERY_WORKER_EMAIL`
- `HOSPITAL_WORKFLOW_OCR_DELIVERY_WORKER_SUBJECT`
- `HOSPITAL_WORKFLOW_OCR_DELIVERY_WORKER_AUDIENCE`
- `HOSPITAL_WORKFLOW_OCR_DELIVERY_DRAIN_EMAIL`
- `HOSPITAL_WORKFLOW_OCR_DELIVERY_DRAIN_SUBJECT`
- `HOSPITAL_WORKFLOW_OCR_DELIVERY_DRAIN_AUDIENCE`
- `HOSPITAL_WORKFLOW_OCR_DELIVERY_PROCESS_LEASE_SECONDS`
- `HOSPITAL_WORKFLOW_OCR_DELIVERY_DISPATCH_DEADLINE_SECONDS`

Deployment owners must explicitly choose the queue, transport identity, worker/drain identities,
audiences, process lease and task deadline. Transport audience/email must match worker
audience/email. No live values or new production permission grants are supplied by this source change.
The independent entry point is `POST /worker/workflow-ocr-deliveries/drain?limit=<explicit-limit>`.

The exact deterministic task name, URL, identity, audience and deadline are committed with the
intent before any transport request. Recovery preserves that captured contract and changes only
delivery generation, without making a new business attempt. Exact NotFound permits recovery;
permission/network failures and mismatched task contracts remain explicit errors.

PostgreSQL execution uses a dedicated connection with a session advisory lock across the runner.
SQLite execution uses a process-shared file lock keyed by database path and delivery ID.
Database transitions additionally use PostgreSQL FOR UPDATE or SQLite BEGIN IMMEDIATE.
Lease expiry blocks old-token adoption; it does not permit a concurrent runner while the
original process still holds execution exclusion. Pool capacity for the extra PostgreSQL
connection and deployment lease/deadline behavior require technical verification.

Original async pending receipts are preserved. The workflow-only before_flush hook binds the
generation/token and selected PDF into the same transaction as pending_receipt persistence.
The same hook binds terminal result receipts, with a before_commit check that rejects an
incomplete terminal transaction. Workflow-command intermediate flushes do not become
standalone synthetic terminal successes.
Launcher ACK can represent verified durable handoff, not business completion.
Existing ingest recovery reads that original receipt; the shared workflow guard accepts only
its exact saved handoff owner after launcher ACK. No ingest-worker, ingest-job-service or
order-service source was modified. Unpersisted/refused handoff and terminal results cannot ACK.

Read-only implementation references:
`worktrees/modernization-20261004/school-lunch-main/backend/src/services/implementation_price_ocr_delivery_service.py`,
`implementation_price_ocr_dispatch_service.py`, and `implementation_price_ocr_delivery_auth.py`.
Their legacy backfill, local transport and mode defaults were not incorporated.

Real caller map: ordinary/evidence requests enter `orders.run_order_workflow_v2_ocr` ->
the two enqueue functions -> `mark_ocr_run_queued` -> durable delivery -> authenticated worker.
Quad/header/row PUTs save geometry and do not dispatch OCR; their subsequent OCR-runs request
uses the same boundary. There is one existing runner per request kind, called only by the worker.

Specific installation residual: parent must integrate migration 0032, apply 0033 on verification
databases, observe PostgreSQL and SQLite revision catalogs, and generate the genuine
0033 entries in `backend/alembic/catalog_contracts.json`. The registry now requires 0033
and still invokes `require_revision_catalog`; absent observed fingerprints block startup.
No observed catalog entry was invented, and no schema check was bypassed.

Source cases are defined in `docs/workflow_ocr_delivery_source_cases.md` and
`backend/tests/unit/test_workflow_ocr_delivery_runner_contract.py`. They have not been executed.
All execution, migration/catalog observation, endpoint/concurrency verification and parent
review remain technical verification work. Long-run operational measurement and J1 cutover
are deferred; source freeze does not establish runtime readiness.

External verification must prove: an intent is committed with its workflow queue transition; Cloud Tasks reaches the worker with the configured OIDC identity; duplicate task acknowledgement does not invoke OCR; an expired claim cannot acknowledge after a newer claim; and OCR terminal state remains independent from transport acknowledgement.
