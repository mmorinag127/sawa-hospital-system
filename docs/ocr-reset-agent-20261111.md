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
