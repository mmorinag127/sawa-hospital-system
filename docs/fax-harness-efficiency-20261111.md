# FAX Harness Efficiency

Requirements: deterministic intake/waits/selection/source collection; AI only
compares supplied originals and sheets and proposes operator-reviewed edits.
Preserve source/history/manual edits. No production, IAM or budget increase.
Complete only after final serial local validation, staging six-copy save/readback
and measured ordinary AI cost. No invented cost or unverified completion.

| Change | Status |
| --- | --- |
| Fixed intake, native idle wait, unique evidence selection | implemented, deployed |
| Verified sheets/PDF pages supplied together before model dispatch | implemented, deployed |
| Remove polling/native operations from model tools and enforce server ownership | implemented, deployed |
| Preserve corrections, approval, versioned save and native GET readback | verified on six staging copies |
| Regression tests and source-bound final serial batch | passed: backend 301, agent 428, frontend 239; types/build passed |
| Staging six-copy save/reload and ordinary AI cost | six saves/readbacks passed; 4 AI calls, estimated $0.11 |

Use existing dedicated worktree `worktrees/prod-preparation-20261105` on
`codex/agent-memory-recovery-20261111`, starting at `b1f2ca82`.
Old paid trials and their uncertainty reservations remain unchanged. The new
execution contract applies only to new pending-FAX runs; old histories are not
silently rebound to a changed tool contract.

First staging run 43027847507 deployed ff61374b after backend 301, agent 428,
frontend 239 tests and types/build passed. Trial cae9276d44e6423692b7572ff8280bc1
completed all six native OCR waits and six evidence selections without any model
dispatch. It was stopped before AI dispatch after discovering that full native
sheet metadata (1.6--3.4 MB per FAX) was included in the proposed AI packet.
Spend $0, reserve $0, no unsettled dispatch or pending command; all histories
remain retained. No save/reload success is claimed.

The AI packet now contains only header/fields/rows; full lineage, confidence,
provenance and cell geometry stay in the native sheet used for signatures and
save validation. The regression includes megabytes of native provenance and
asserts it is absent only from the model packet, not deleted from native data.
The final serial aggregate passed on cad794ab08f5. Its source-bound receipt is
`tmp/fax-harness-proof-20261111-compact/receipt.json`; aggregate log is
`tmp/fax-harness-batch-20261111-compact.log`.
Corrected staging Actions run 43039514229 succeeded: web-stg-00513-b76,
image sha256:9d921050c64d1d61adf54a0841141dcead8ad807425fd97991e6e0d321d4c5a4.
New trial 9761165743b0448b883f27671d86eaa5 uses the same pinned six-original
bundle, within a $0.25 run cap and the unchanged $10 monthly cap. A transient
authorization-service communication failure was recorded during a native wait;
the same run subsequently continued to the next intake. No token-expiration
diagnosis or save/readback completion is claimed. Normal cost remains unmeasured
until actual inference and six save/readback results are available (now recorded below).

## Final Staging Evidence (2026-11-12)

Trial `9761165743b0448b883f27671d86eaa5` completed six OCR executions,
six evidence selections, operator-reviewed proposals and six draft saves.
Each successful save command includes native GET readback and signature/revision
checks (`FaxWorkflow.after_write`). An independent read-only comparison of the
downloaded native SQLite snapshot found every persisted full sheet exactly equal
to its approved sheet, including native metadata; no reduced-field comparison
was substituted for full equality.

| Original | Dedicated staging copy | Saved draft | Full equality |
| --- | --- | --- | --- |
| ORD4a12b06a | ORDd6c6e640 | ODS94d43bc751e34578 | pass |
| ORD752f4dcb | ORDb64750a4 | ODSc0d74eac4f424a1f | pass |
| ORD8af32e27 | ORD44d4263b | ODSb45d87abd0ae4383 | pass |
| ORD108ac1e3 | ORDefbb3cb2 | ODS1b8357b089d14c6b | pass |
| ORDf021b65f | ORDa56ddf57 | ODSc900d7fcdad54b98 | pass |
| ORD8b3b2ede | ORD268d03b1 | ODS0cf7fb8325144955 | pass |

All six workflow revisions were 10 in the verified snapshot. Proof:
`tmp/fax-harness-six-save-proof-20261112.json`; read-only verifier:
`tmp/verify_fax_harness_saves.py`. Full sheets and original PDFs are not embedded
in this document. The production originals and their saved/manual histories were
not modified. The trial was stopped normally after save/readback verification;
the UI confirmed stop completion. Screenshot:
`tmp/fax-harness-six-saved-stopped-20261112.jpg`.

## Measured Agent Cost

| Measure | Earlier exploratory run | Corrected harness trial |
| --- | --- | --- |
| AI requests | 73 | 4 |
| Additional agent AI cost | $2.54 | $0.11 |
| Six saves/readbacks | not completed | completed |

The four requests were one initial six-image comparison plus three human-requested
corrections of cancellation cells, not polling or native-operation selection.
Initial comparison alone was estimated at $0.04. The whole measured trial was
$0.11 for six FAXes (about $0.018 per FAX), using configured rates of $0.75 per
million input tokens and $3.75 per million output/thought tokens. Totals: 96,020
input, 4,328 output and 5,398 thought tokens. The ledger rounds total estimated
cost upward to cents. This is one observed case, not a fixed price or final
invoice; existing native OCR/Cloud Run/storage charges are excluded. The old run
and this run did not reach the same endpoint, so this is not a controlled timing
benchmark. No processing-time reduction is claimed.

## Acceptance Boundary

The requested six saves/readbacks passed. This does not certify every OCR number
as correct or eliminate human review. Three cancellation cases required explicit
image-based human correction; uncertain other marks were preserved, not guessed.
The subsequent bagging operation was refused with `sheet_ocr_review_required`;
confirmation/output completion is not claimed and that guard was not bypassed.
No production operation, IAM/budget increase, Docker start or extra deployment was
used to turn this refusal into success. The existing uncertain reservation from
the earlier OOM run was not released.
