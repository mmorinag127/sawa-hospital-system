# Numeric Quantity Production Release

User approval: staging-verified runtime may be deployed to production and checked. Document-only signed proof extension separately approved on 2026-11-17.

## Fixed Scope

- Runtime must equal staged `fb24db83fef9c8cdc42b388145982abf6ebc4a69`, successful staging run `43914538923`.
- Only the two named release documents, isolated receipt implementation/tests and its exact four-line dispatcher insertion may differ.
- No application, dependency, migration, deployment workflow, authentication, production approval or rollback changes are permitted by this proof.
- No production OCR rerun, evidence adoption, sheet save, quantity correction or confirmation. No IAM or budget increase.
- Finish gate implementation before one serial local gate regression, then use owner-dispatched `release/prod-*` Actions with explicit production confirmation and rolling release checks.

## Status

- Production baseline: source `fc76bc51162fcd9bf932f451a7617f7c37aeb1c0`, run `42938340536`; immutable worker/web/agent/producer manifests are available.
- Reported orders `ORD76d867d8` and `ORDacd23151` captured in read-only transactions; original business state unchanged during preflight.
- Gate implementation and serial local gate regression complete: 80 passed, no failures/skips (3.50 seconds). Existing staged backend 960 / agent 482 / frontend 319 and type/build proofs were verified, not rerun or relabeled as a new full regression.
- Production source: `dc10e71ae44453bd98713e9c991413eefd64d25d`, branch `release/prod-20261117-numeric-quantity`.
- Owner-dispatched run [43989856294](https://github.com/mmorinag127/sawa-hospital-system/actions/runs/43989856294) succeeded on attempt 1. The registered production-environment approval was approved as owner under the user's explicit deployment instruction; approval configuration and IAM were not modified.
- Rolling release completed without displaying maintenance. Cloud SQL backup `1794877061335` was recorded, schema remained `0039`, catalog verified, and candidate tags were cleaned up without failures.
- Browser verification and read-only database comparisons completed. No OCR rerun, evidence adoption, sheet save, quantity change, bagging or confirmation was performed in production.

## Deployed Images

| Service | Serving Revision | Digest |
| --- | --- | --- |
| Worker | `worker-prod-r43989856294-1-candidate` | `b4fbbae64c738f4e8b5b7929003bfe9c96727811a6f18c3c43cb004f7ff9c9a2` |
| Web | `web-prod-r43989856294-1-candidate` | `eab9bf2df4a79b01cad841a3a26a0288efc57e60ddb155008b5793bb94e0e3b1` |
| Agent sidecar | Attached to the web revision above | `7f8ea7457be4685ce69363b59caddd2445e64fc7c79b791952ad274d59c5e4c2` |
| OCR producer | Existing `ocr-pipeline-prod-00050-k8b`, producer code unchanged | `a8e1747cb65de43d0a4e447bb102cb67cd560ecdb4b7d69c48e31fd4d6e258df` |

Worker and web labels match the exact release SHA. Serving traffic is 100% on the intended revisions; immutable manifests were resolved and verified. The original `fc76bc51` worker/web revisions and digests, including the original agent sidecar, remain recorded for rollback.

## Production Verification

- Existing saved order `ORD613fc70e`, facility `FAC00004`, week `2026-11@2026-11-22~2026-11-28`: all 616 browser cells matched the expected read-only projection. All 99 numeric cells were unchanged. The 31 non-numeric originals are displayed separately as pending, not converted to guessed digits or zero.
- Active saved draft `ODS84cb30bac7844bee`, original sheet SHA256 `121e7342bc0cd768936636be1e6b0a21c023455a7ffa74db2aaa4d88f7b054d2`: stored JSON, order and workflow were exactly unchanged before/after deployment and browser reading.
- Reported orders `ORD76d867d8` / `ORDacd23151`: all captured rows in orders, workflow states, draft history, selected/candidate evidence and OCR jobs matched the pre-deployment snapshot; changed tables `[]`.
- Historical failed OCR states were preserved, not relabeled as successful and not automatically reprocessed. This is deployment/read-projection acceptance, not a claim that every OCR recognition or pending quantity is correct.
- Budget, IAM, facility configuration, original PDFs, stored quantities and manual edits were not modified by this task. The separate numeric recognition error `909` was not automatically replaced.

## Evidence

Local worktree evidence:

- `tmp/numeric-quantity-production-proof-20261117/receipt.json`, `gate.xml`, `gate.log`: signed exact-source document/gate delta and actual gate regression.
- `tmp/numeric-quantity-prod-before-20261117.json`, `tmp/numeric-quantity-prod-dispatch-20261117.json`, `tmp/numeric-quantity-prod-after-20261117.json`: immutable original/current revision UIDs, images and source labels.
- `tmp/numeric-quantity-production-actions-20261117/online-release.json`: completed Actions release record, original services, backup, candidates, promoted revisions, schema and tag cleanup.
- `tmp/numeric-quantity-prod-data-{before,preflight,after}-20261117.json` and corresponding summaries: exact reported-order snapshots and hash comparison.
- `tmp/numeric-quantity-prod-saved-sample-{before,after}-20261117.json`: active saved-sheet preservation.
- `tmp/numeric-quantity-prod-ui-{before,after}-20261117.json`: actual browser cell values.
- `tmp/numeric-quantity-prod-verified-20261117.jpg`: production screenshot with separate originals/pending quantities.
