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
- Gate implementation complete; gate regression, release dispatch and post-deploy verification pending.

## Evidence

Local worktree `tmp/numeric-quantity-prod-before-20261117.json` records original revision UIDs and resolved images. `tmp/numeric-quantity-prod-data-before-20261117.json` and the preflight comparison preserve exact reported-order state. No production release has been dispatched at the time of this initial record.
