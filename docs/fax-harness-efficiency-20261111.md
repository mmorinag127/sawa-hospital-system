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
