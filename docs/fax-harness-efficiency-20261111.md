# FAX Harness Efficiency

Requirements: deterministic intake/waits/selection/source collection; AI only
compares supplied originals and sheets and proposes operator-reviewed edits.
Preserve source/history/manual edits. No production, IAM or budget increase.
Complete only after final serial local validation, staging six-copy save/readback
and measured ordinary AI cost. No invented cost or unverified completion.

| Change | Status |
| --- | --- |
| Fixed intake, native idle wait, unique evidence selection | implementing |
| Verified sheets/PDF pages supplied together before model dispatch | implementing |
| Remove polling/native operations from model tools and enforce server ownership | implementing |
| Preserve corrections, approval, versioned save and native GET readback | implementing |
| Regression tests and source-bound final serial batch | not started |
| Staging six-copy save/reload and ordinary AI cost | not started |

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
The final aggregate and corrected staging six-copy run remain required.
