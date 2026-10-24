# Diagnostic failure-class remediation

Source of the failed diagnostic: `f50d8118fcccec8a674428beb167191f4b3a78da`.
Evidence: `/private/tmp/sawa-hospital-full-f50d8118-20261024/failed-run.json`.
The backend was interrupted after shared preparation failures were established:
69 failed, 1606 passed, 254 skipped, 53 errors. Unexecuted cases are not passed.
Other independent groups completed; none produced a signed success receipt.

## Implemented, not yet revalidated

| Class | Shared correction | Required preserved behavior |
| --- | --- | --- |
| Imported fixtures lose dependent fixtures | Register hospital_operator and owned_outputs in shared conftest | Real grants, per-case owned schema and source receipts |
| Canonical-source refusal becomes HTTP500 | Catch CanonicalStoreError at the workflow command boundary after session exception rollback | No fallback source; original conflict remains a conflict |
| Candidate preview performs writes | Use nonpersisting workflow projection without recursive candidate preview | Current draft, current-state tables and original artifacts unchanged |
| Saving/selecting erases saved-sheet history | Retain draft rows while clearing active selection and invalidating output materializations | Historical OCR cannot become the new selected source; stale CAS still rejects |
| Removing an archived OCR violates retained draft FK | Return explicit saved-history conflict for unselected referenced evidence | No implicit cascade, no removal of saved values or current selection |
| Structural mapping is blocked but falsely needs no correction | Keep explicit unresolved column-mapping correction requirement even with one candidate | No position fallback adopted; quantity-content policy unchanged |
| Monthly recipe fixture is unresolved | Declare regular diet on the authored recipe and a complete synthetic diagnostics recipe | No application-side diet, amount or physical-row fallback |
| Fixture duplicates existing physical menu rows | Explicit prepared_menu_sources input for already-authored positive cases | Real OCR queue/capture/selection still validates source rows |
| Special-diet label policy is missing | Positive fixtures explicitly declare comparable diets | Missing-policy negative cases remain refused |
| Accessory has no authored amount | Explicit parent dish and separate sauce recipe in the component | Original 3+4 servings remain seven, with independent source refs |
| Workflow fixture expects retired double revision increments | Bootstrap1/context2 and one CAS increment per repeated unconsumed command | Exact selected projection, original JSON and stale-revision refusals |
| OCR rerun assertions contradict saved-sheet lifecycle | Assert saved values and exact unchanged pre-rerun materializations | A save alone does not publish new OrderLines; failed rerun retains existing lines |
| List test guesses a week from PDF filename | Refuse missing owned selection; isolate upstream receipt in lightweight component test | No filename-derived facility/week adoption |
| Diagnostic decision fixture violates document NOT NULL | Explicit component-only document identities | No fabricated PDF, OCR or output proof |
| Archived hosted browser checks no longer exist | Local source/run identity entrypoints and current manual-policy contract tests | Staging target, real auth, immutable image SHA, GET allowlist, owned cleanup |
| Compound commands mask later checks | 28 independent serial command records | Every result/log recorded; failed or changed source cannot sign |
| PG paths assumed Linux | Explicit PG_BIN override; REFRESH_TEST_PG_BIN prerequisite documented | Owned database/PID/role/socket checks unchanged |
| Frontend image contract expects Node20 | Match the actual Node24 Dockerfile | Real image, source/vendor bytes and TLS proof retained |
| SQLite schema migration repeated for each case | Reuse the existing formal empty-template helper, copy to a new case-owned DB | Per-case formal-head/FK checks, immutable template digest; native PG migrations unchanged |

MCP/native stop repair is separately committed as `a9376047`; its document
lists the scheduling, generation, process ownership and unknown-stop cases.
Its tests have not been executed on the changed source.

## Business-specification reconciliation

The forbidden-diet exclusion is not changed in application code. Read-only
production inspection established worker-prod-se39911618242n1 at 100% traffic,
image `sha256:cce530e11a2095e74d7fec90af8db215b027821110f76982ca571aa7e1fc03a3`.
The verified OCI configuration identifies source
`9c16aa0711f1864271f83050c2e889b00fddd12e`; its exclusion set matches current
source: forbidden_other, no_fish, no_fried, no_meat, sesame_allergy, placeholder,
unknown. Commit `5a89ce6e` explicitly introduced the forbidden exclusions and
regressions. Old tests expecting those values in aggregation are migrated while
checking that the unchanged original values remain in saved sheets. This is a
source comparison, not a claim of live same-input OCR/output parity.

## Source integration follow-up

The daily output, native stop and saved-history corrections were integrated in
f5a08888's ancestry. The additional actual-runtime dependency inventory and
native source synchronization are recorded in runtime_dependency_inventory_20261024.md.
These source integrations are not successful full-batch or live acceptance.

## Still required

- Complete staged review of all additional dependency/native corrections;
  keep unrelated AGENTS edits out of commits.
- Verify all patched actual image environments without hiding advisories or
  equating a same-contact recognition comparison with complete OCR parity.
- Run the complete integrated-source batch serially, including all backend cases,
  PostgreSQL, actual images, native workflows, frontend types and browser tests.
- Only successful same-source evidence permits minimal manual staging deployment.
- Verify the reported live order, save/reload, OCR job separation and test-DB return.

No additional Actions, deployment, budget, IAM or production writes were made
for this remediation. Docker was stopped after confirming zero running containers.
