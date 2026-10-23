# Owned Status Read Preparation

No tests have run in this implementation batch. These six preparations now use
the actual authenticated registered/context-confirmed order fixture rather than
unauthenticated requests or guessed positive job identities.

| Previous preparation | Current preparation |
| --- | --- |
| Detail missing output | Real queued/claimed failed progress; GET cannot recover output, publish evidence or mutate the job |
| List workflow refresh | Authenticated read-only list; actual workflow function is observed, not replaced by None |
| Completed first-pass file | Real owned running job plus unrelated diagnostic done file cannot complete it |
| Done message first-pass without evidence | Unowned message job/file remains diagnostic; registered order status is unchanged |
| Done order-bound first-pass without evidence | Explicit order binding alone cannot manufacture workflow ownership or selected evidence |
| Running list status | Real owned queued/claimed running progress, authenticated list, no read mutations |

Existing separate real produced/saved-five evidence case remains intact. Negative
diagnostic jobs deliberately have no workflow attempt or terminal receipt; no
selectable evidence or execution receipts are fabricated. File bytes and SQL
are protected by the shared read-only fixture.
