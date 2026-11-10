# OCR selection reset and hospital agent trial

## Scope

Return the six production orders for 2026-11-15 through 2026-11-21 to review by retiring current OCR, draft and output references. Keep original documents, every OCR evidence row, saved sheet, operator edit, confirmed snapshot and historical output unchanged. Then exercise the hospital order agent through its actual task surface.

Targets: ORD108ac1e3, ORDf021b65f, ORD4a12b06a, ORD8b3b2ede, ORD8af32e27, ORD752f4dcb.

## Implementation

| Item | Status | Behavior |
| --- | --- | --- |
| Reset command/API | Implemented, unverified | Order lock, revision CAS, active OCR refusal, authenticated atomic audit; no history deletion |
| STEP1 reset action | Implemented, unverified | Explicit preservation confirmation; current selections retired; history remains accessible |
| Agent initialization | Implemented, unverified | Exact Cloud SQL instance / portal authentication origin pairs for stg and prod; no cross-environment binding |
| Online agent delivery | Implemented, unverified | Build and select the exact-source agent sidecar instead of retaining an older native backend |
| Related regression coverage | In preparation | Saved/unsaved orders, unchanged sibling/history, revision conflict, active job, idempotency, UI cancellation, database pairing, sidecar identity |
| Local batch | Not run | Serial execution after implementation is complete; real disposable local PostgreSQL for ledger integration |
| Staging | Not deployed | Official Actions only, develop source, reset/history/readiness checks |
| Production reset | Not performed | Preserve the user staging-review/production approval boundary; compare protected history hashes before/after |
| Hospital agent trial | Not performed | Source selection remains unresolved: actual-six private copies or existing packaged verification FAX |

## Findings

Production assistant task/config requests returned HTTP 503. Its configured Cloud SQL target is orders-prod, whereas `cloud_store` accepted only orders-stg. The deployed online release also retained the older agent image while replacing the main backend/frontend. Both paths require correction before a meaningful trial.

An initial-saved import whose original template has not been replaced by a canonical selection has additional receipt ownership constraints. Reset now retires that binding with the original source digest in the atomic reset audit, without changing historical rows. A missing or mismatched retirement audit remains rejected. This sibling path is implemented but not yet tested; no live reset has been attempted.

## Completion evidence

None yet. No tests, deployments, live state resets or agent runs are claimed successful by this document. No budget, IAM, business quantities or source PDFs have been changed.
# Real Six-Order Copy And Reset Confirmation

2026-11-11: The operator selected private copies of all six 11/15-week
production orders, not the packaged synthetic FAX. Production originals,
saved sheets and manual history must remain unchanged by the agent trial.

Implementation now includes an owner-scoped, content-addressed source bundle,
frozen source identity in target lookup and task creation, normal pending-FAX
registration, original orientation propagation and private captured workbook
resolution. Source DB extraction is a repeatable-read, read-only transaction;
only supporting facility/menu tables enter the private SQLite database.
Production saved-order import bindings are excluded from the private master;
authored facility versions remain unchanged. No real source files enter Git.

The private six-order bundle was prepared locally as
`tmp/hospital-real-six-20261111.zip`, SHA-256
`ff5a61281e4daab52a762f0b3cbc224064f410912dbb5fa959f8ea897d21bd02`.
This is data preparation, not successful agent/OCR execution.

Incident: stg smoke order ORD37344b72 was reset during native confirmation
verification. Audit AUDd87f48731479454692f4d907f2256326 records the prior
references; retained evidence, saved sheet and confirmed snapshot still exist.
The precise native-dialog cancellation timing is not established. Production
six-order resets have not been performed. Restore permission was requested;
no live restoration is claimed here.

Native confirmation is replaced with an in-page dialog. Opening or cancelling
must not submit a reset. A revision-bound restore command can restore retained
references only before an intervening mutation or active OCR, with a new audit
and monotonic workflow revision. Legacy confirmed imports require their actual
retained confirmed snapshot and reopen a newly captured import receipt.

Status: implementation and test additions are local; the new source-copy,
modal and restore changes have not yet passed the combined local batch or been
deployed. Existing stg 75524611 is not evidence for these new changes.
