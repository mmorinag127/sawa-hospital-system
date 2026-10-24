# Fixed-source full batch remediation

Fixed requirements: complete known implementation repairs first, then update
fixtures together, then run serial local verification on one fixed source.
Preserve raw OCR/operator edits, canonical input/geometry/receipt validation,
CAS, authentication and process/database ownership. No additional billing,
automatic CI/deploy, local deploy, production/IAM changes or fabricated receipts.
Completion still requires integration, regular stg delivery and actual operator
save/reload/OCR isolation/test-database return checks; local results are not that.

## Preserved diagnostic evidence

- Source7be11d7b67945b7873484307ee162acefcc2853b, initially clean.
- /private/tmp/sawa-hospital-full-7be11d7b-20261024/failed-run.json.
- Backend:775 failed,9271 passed,608 skipped,365 setup errors,2345.30seconds.
- All1140 failure/error sections are indexed beside the original4.6MB log in
  00-backend-tests.failure-index.json. Unparsed exception labels are not passes.
- Next dev generated next-env.d.ts changes and AGENTS.md. Source guard correctly
  stopped the batch after command5. Both generated files were preserved in the
  failed-run directory, and only these known generated changes were restored.
- Remaining24 unchanged commands ran as diagnostics, without signed receipts:
  /private/tmp/sawa-hospital-tail-7be11d7b-20261024/diagnostic-results.json.
- Actual Linux six-environment dependency audits and actual-image TLS passed.
  MCP430 passed/3 Mac process-inspection/termination permission failures; safety
  refusals were not suppressed. PG adoption51 passed/2 metadata comparison failed;
  readonly PG passed. Three other PG suites refused their old Linux-only profile.

## Work Split And Failure Classes

Parent owns production/runner changes in assistant-business-workflows-20261023.
Existing Terra/low worker owns backend test-fixture migration in its separate
agent-batch-remediation-20261024 worktree, also based on7be. It initially performs
read-only classification, and edits only after IMPLEMENTATION_READY. No tests,
commits, push or deploy are delegated. Nash reviews scope and staged differences.

| Shared cause | Repair | State |
| --- | --- | --- |
| Known stale commands acquire SQLite write lock before CAS refusal | Read-only precheck; retain original under-lock CAS and internal write-session semantics | Implemented, unverified |
| Typed original-PDF receipt refusal becomes a generic error dictionary | Preserve SelectedPdfReceiptError through materialization; translate only at public command boundary | Implemented, unverified |
| Context-only template identity is mistaken for downstream artifacts | Exclude template identity alone from downstream reset predicate; retain actual evidence/draft/output checks | Implemented, unverified |
| Python scalar equality conflates JSON boolean/integer/float configuration identity | Compare canonical typed JSON digests, retaining valid-to-only closure semantics | Implemented, unverified |
| Rendered main-delivery label cannot round-trip through sorting | Recognize both full-width and ASCII A labels as the existing main category | Implemented, unverified |
| Optional Cloud SQL CLI driver imported during pure receipt validation | Import the connector only when the CLI actually opens a connection | Implemented, unverified |
| Stored blank regular-entry encoding leaks into unresolved master-item/request selection | Separate master-item identity/filter and require explicit entry-lookup diet; retain stored-entry encoding | Implemented, unverified |
| Rejected OCR template mismatch is missing from returned workflow blockers | Use the existing canonical blocker transition, preserving archived evidence/sheets | Implemented, unverified |
| Explicit reselect preserves a template-mismatched current draft | Preserve valid same-evidence drafts only; detach invalid current references without deleting saved rows | Implemented, unverified |
| Combined OCR candidates borrow later menu-amount diagnostics from the full workflow | Serialize the checked base once and copy only for full-workflow enrichment; candidate response keeps base semantics | Implemented, unverified |
| Adoption test compares current0036 against a hardcoded0035 shape | Compare every current-head table/constraint; preserve NULLs and original rows | Fixture update pending; catalog SQL unchanged |
| Next dev changes tracked source; E2E URL not set | Isolated source/dependency copy, explicit owned loopback URL, existing no-reuse policy | Implemented, unverified |
| Archived test steps retain GitHub-only expressions/identity names | Strictly translate known local path/SHA/run expressions; never create hosted identity | Implemented, unverified |
| Dedicated PG modules rerun outside their owned context | Assign them only to their existing dedicated commands; preserve all nodeids | Runner implemented, fixture/coverage checks pending |
| Fixed historical PG paths and Linux-only guard reject local PG16 | Per-run physical owned root/socket/PID/server identity with PG16 validation | Runner implemented, fixture profiles pending |
| Native workflow tests encounter Mac process inspection denial | Verify in isolated local Linux runtime; do not weaken ownership checks | Runner implemented, unverified |
| Old quantity-only plan assumes56 but authored note fields produce112 | Verify actual authored role/geometry and update common producer fixture without fabricated OCR | Worker fixture migration |
| Missing canonical source/geometry fields, stale helper signatures/imports and typed date inputs | Common authored fixtures and unambiguous imports; retain negative assertions | Worker fixture migration |

All1140 baseline sections have an individual source/log classification in
/private/tmp/sawa-backend-failure-map-7be11d7b-20261024.json. IMPLEMENTATION_READY
was sent only after the known production/runner repairs above. Worker fixture
updates remain isolated; parent owns PG profiles, runner contracts and CAS cases.
Tests have not been rerun against these repairs.

Parent fixture updates now cover current-head adoption, owned PG profiles on
Linux/macOS, wrong URI/role/OWNER/PID/marker/server rejection, cleanup failure
evidence, explicit stale-CAS no-write behavior, valid/invalid saved-template
reselection with derived rows present, isolated E2E source/private-environment
exclusion, dedicated-module mapping and strict local expression translation.
The frontend whole-file hash changes only to the already-present approved
tooltip source; all surrounding semantic/lineage assertions are retained.
Nash caught missing derived cleanup on same-evidence reselect and independent
container cleanup failure handling. Both implementation paths are corrected;
their regression cases are included, not yet executed.

The previous owned synthetic agent PostgreSQL container
fe1f66ebbd0369b1380f0eead061ecc7de21f66b79f3dd09338c06245c4d05a2
was dumped to the preserved tail-diagnostic directory, then gracefully stopped.
Docker inspection reported Running=false, ExitCode=0. No other container changed.

## Unreached PostgreSQL sibling path

After5f03d613 was saved, source review of the previously aborted output group
found another fixed-target path: TCP5432, Linux-only PG binaries, historical
formal-cluster paths/socket aliases and the old runner role. The output and
formal modules now use the same per-run owned PG16 implementation as the other
dedicated suites. Output runs the real migration observer first, then every
transaction case; formal uses an independent cluster and the whole original
module. Failure of one group does not omit the independent other group.
The observer validates physical owner/source/PID/server/role and a new evidence
path before production imports. Current formal fixtures retain their actual
catalog/server assertions and reject old/TCP/foreign targets.
The two obsolete archived PG jobs are no longer executable via the generic
legacy-step entry, preventing the same fixed-target path from re-entering.
Additional contract cases cover migration failure, both suite executions,
independent failure collection and foreign/existing/aliased evidence refusal.
No test was executed while these known implementation repairs were pending.

Local read-only OCR artifact verification now leaves gh on its existing local
login instead of trying to resolve an unavailable Actions github.token. An
already supplied GH_TOKEN is preserved; no Actions identity/token is generated.

Native selected sheets remain viewable with sheet_unavailable and source blockers
when the authored source is unavailable. This does not authorize adoption or
borrow historical authority; negative tests must preserve both the blocked state
and the operator's exact saved input. Initial saved imports retain their distinct
validated immutable source receipt.

Do not treat old tests as the cause without reading each failure. Genuine source
defects are reported to the parent. Do not replace unknown expected values,
remove assertions, skip cases or reintroduce fallback to make this batch green.
