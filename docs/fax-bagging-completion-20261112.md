# FAX Bagging And Confirmation

Scope: finish native bagging and confirmation for the six staging copies, using
canonical original images, while retaining histories/manual edits. No production,
budget/IAM increase, quantity guessing, blanket mark deletion or weakened native
bagging guard. Complete implementation before the final serial local validation
and one staging rollout. Use worktrees/prod-preparation-20261105.

## Cause

The earlier run's saved sheets contain nonnumeric quantity strings. The native
materialization guard rejects these, independently of the pre-save review receipt.
Review-confirm records that a sheet was inspected; it does not convert symbols
to numbers and cannot authorize guessed quantities. The previous harness prompt
retained all suspicious marks but did not resolve or explicitly ask about every
cell needed for bagging. Numeric anomaly review did not provide this validation.

| Original | Remaining nonnumeric cells in saved snapshot |
| --- | ---: |
| ORD8b3b2ede | 17 |
| ORDf021b65f | 37 |
| ORD108ac1e3 | 17 |
| ORD8af32e27 | 10 |
| ORD752f4dcb | 10 |
| ORD4a12b06a | 8 |

Values include '-', '--', '一', '・', and the mixed strings '· 99' and '12_'.
No blanket conversion of these values is authorized.

## Work List

| Item | Status |
| --- | --- |
| Native read-only pre-save report uses bagging's canonical quantity validator/template | implemented, not verified |
| Supply exact invalid cells before AI comparison; image-based proposals or specific questions | implemented, not verified |
| Review surface names unresolved cells; save remains available | implemented, not verified |
| Final completion rechecks current native report before bagging/confirmation | implemented, not verified |
| Required regression/fixture updates | implemented: native validator parity/read-only, version/cell/schema fences |
| Final serial local verification and signed source receipt | pending |
| Regular staging rollout, six bagging/confirmation proofs | pending |

The old trial was explicitly stopped after save/readback and has no reservation.
Do not silently rewrite its terminal status or old execution binding. Old native
and AI receipts remain historical evidence, not proof of bagging success.
