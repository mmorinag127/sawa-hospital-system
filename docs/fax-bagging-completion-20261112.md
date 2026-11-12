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
| Native read-only pre-save report uses bagging's canonical quantity validator/template | implemented, local parity/read-only tests passed |
| Supply exact invalid cells before AI comparison; image-based proposals or specific questions | implemented, local tests passed; six staging proposals registered |
| Review surface names unresolved cells; save remains available | implemented, local tests passed |
| Final completion rechecks current native report before bagging/confirmation | implemented, local tests passed; live completion pending |
| Required regression/fixture updates | implemented: native validator parity/read-only, version/cell/schema fences |
| Final serial local verification and signed source receipt | passed at 9123e9d6: backend 315, agent 437, frontend 239; types/build passed |
| Regular staging rollout, six bagging/confirmation proofs | rollout 43137457062 succeeded; six live completion proofs pending |

Signed receipt: `tmp/fax-bagging-proof-20261112-recovery-fixed/receipt.json`.
Staging web revision `web-stg-00515-m4l`, image
`sha256:f1854d2b8c85a293d3f3e2cef2ba1a45904da6cd7ace5d86da7e4a8105c390e4`.
Unchanged backend was reused, not rebuilt. Local PostgreSQL was stopped; Docker
was not started. One earlier batch failed on five synthetic recovery fixture
setups; those fixtures were fixed before the successful complete batch above.

The same staging trial now holds six approval proposals. Its first incomplete
inference and one bounded low-thinking restart cost $0.11 combined so far.
The original $0.15 trial cap and $10 monthly cap remain unchanged. Native
quantity readiness is not evidence of accurate quantities: visual comparison
found two Kasuga 7-to-1 errors and Ikoi cancellation/change-column errors. A
single operator correction request addresses both; approvals and native
bagging/confirmation remain pending. The Ikoi fish exclusion 1 belongs to
11/17 evening, not 11/20; the latter remains blank, as the original shows.

The old trial was explicitly stopped after save/readback and has no reservation.
Do not silently rewrite its terminal status or old execution binding. Old native
and AI receipts remain historical evidence, not proof of bagging success.

## Incomplete Inference Found In Staging

Run 43125720030 deployed bd9ad8c2 successfully. Trial
2e4d51b976264951bd1278a341d2be79 retained all six completed native OCRs. A
read-only GET of its existing Gemini interaction proved status `incomplete`,
7,860 thought tokens and 328 output tokens under an 8,192 combined limit, with
no function-call steps. Estimated cost was $0.06. No reviews or writes were
executed from that incomplete response.

The provider documents that max_output_tokens includes thinking and output,
and recommends reducing thinking_level rather than using a small cutoff:
https://ai.google.dev/gemini-api/docs/thought-signatures

New fixed-FAX runs pin thinking_level=low. Recovery of the already-prepared
trial is bounded to one restart only when the provider proves an incomplete
response with no function calls, no local model execution checkpoint, no human
review/decision, settled usage and unchanged owner/source/budget fences. The old
interaction and its charges remain in history; a lower-thinking attempt uses the
same working copy, cap and completed OCRs. The response-token ceiling doubles
(8192 to 16384 here) but remains clamped by the existing unspent run allowance;
the monetary cap and monthly budget do not increase. Low-thinking incomplete responses,
unknown provider results and any potentially executed tool call are not retried.

## Budget Boundary Found During Operator Correction

The image-based operator correction response brought cumulative estimated spend
to the unchanged $0.15 trial cap. The business controller's global cap check
then changed the run to budget_reached while a paid approval-proposal command
was still executing. This also prevented subsequent human approvals and native
completion, although those actions do not send an inference request. Separately,
the first corrected proposal could pause the remaining corrected proposals from
the same received response.

The fixed FAX controller no longer gates local approval/native completion on
remaining model allowance. A budget-stopped run requires explicit receipt
recovery; provider history must prove a completed/requires-action response
containing only approval-proposal calls. Pending local proposal IDs and complete
arguments must match that history before their idempotent, read-only-native
review preparation can resume. Unknown native mutations remain blocked. Source
upgrade keeps its owner/generation/binding gate. Remaining proposal calls from
the same paid batch drain before pausing for human approval. The pre-dispatch
model cap checks remain unchanged; no additional inference is authorized.

This additional root fix requires a new consolidated local regression receipt
and staging rollout before live recovery. Six native confirmations are still
pending; no success is claimed from proposal registration alone.
