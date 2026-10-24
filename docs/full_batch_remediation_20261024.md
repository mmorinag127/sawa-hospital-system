# Fixed-source full batch remediation

## Follow-up At 57e9dffe

The clean fixed-source run completed all30 local commands at
`57e9dffe35c96b23f91039ae2769d0c3a9d4b40d`, with23 zero exits and7 nonzero.
Evidence: `/private/tmp/sawa-hospital-full-57e9dffe35c9-20261024-3/failed-run.json`.
Backend:9837 passed,131 failed,22 setup errors,184 skipped in3852.72seconds.
Its153 failure/error sections are indexed beside the original log.
The five frontend commands lacked the clean worktree's ignored node_modules;
the browser flow did not run and is not a functional failure or success proof.
The C1 guard group had202 passes and one obsolete push-trigger expectation:
the approved workflow is manual-only. Actual Linux agent tests and owned
container cleanup succeeded. These are local results, not deploy evidence.

Follow-up implementation is in progress, without a new test run:

- Queue capture now copies the document ID from the actual selected PDF receipt,
  rejecting differing explicit/metadata IDs. Pending handoff no longer relies on
  a caller redundantly supplying that same identity. Guards remain strict.
- Selected evidence with unavailable artifacts retains identity-only recovery
  metadata. Its unusable payload is never returned or selected by this change.
- The original target plan includes post-menu auxiliary cells, but its evidence
  validator rejected their authored role. Only explicit aux.col_N roles are now
  accepted; raw text is preserved, not summed, reanchored or used as a quantity.
- Authored fixture sources explicitly declare their label-comparison policy.
  Terminal-history setup records diagnostics during owned completion instead of
  modifying already-completed jobs outside their delivery owner.
- Receipt transition assertions cover both the actual pipeline delivery receipt
  and its output reference atomically, retaining whole-database comparisons.
- Assignment merge diagnostics count body cells only, not header merges.
- A blocked-source placeholder without a persisted draft ID no longer hides the
  selected evidence identity and presents it again as a new candidate.
- Raw monthly category 副菜 is preserved independently of output slot labels.
- The isolated worker's two OCR integration fixture files were reviewed and
  copied into the parent. Corrected-PDF tests use real registered source bytes,
  derived hashes and parent receipts; missing receipts/artifacts never fall back.
  Diagnostic draft persistence proves exactly one added row, unchanged current
  selection, raw operator cells, PDF and canonical facility revision.
- Assignment public-command boundaries now return the existing typed refusal
  for inconsistent repaired representations and invalid assignment sources,
  instead of leaking a validation exception. Cache publication is deferred until
  baseline and final assignment validation have both succeeded. Two failure-
  injection cases require no publication and whole-database/source immutability;
  these new cases are not yet run.
- The local batch now refuses missing frontend dependencies before starting
  backend tests. Dependency installation remains explicit; no Actions or network
  installation is introduced by the runner. New refusal coverage is unrun.
- The daily-category fixture now declares a real scoped side-dish recipe while
  retaining the different master category and all quantity assertions.

Unresolved source semantics: partial-month order periods versus full seven-day
FAX periods must not be silently conflated. The former Sunday expansion and
the explicit-period boundary currently disagree. No narrowing was implemented.
Totals-only forbidden-diet fixtures also conflict with the explicit exclusion
introduced in 5a89ce6e; the production exclusion has not been removed to pass
those tests. Frontend dependencies must be prepared before the next clean batch.

All follow-up changes remain unverified and unintegrated. The current parent
AGENTS.md modification is unrelated and remains unstaged.

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
| Canonical JSON validation imports the database through template validation | Resolve the database engine only on database access; preserve the existing engine hook and explicit database requirement | Implemented, unverified |
| Failed recognition checkpoint recovery requires a nonempty quantity review despite editable raw quantities | Allow explicit full-manual recovery for failed jobs with an empty valid review; retain all owner/source/terminal checks, done-job refusal and empty-subset refusal | Implemented, unverified |
| Full-manual resume lacks the original terminal-owner recheck used by subset recovery | Revalidate the original job, checkpoint, selected PDF, terminal owner and evidence identity before consuming manual values; do not consume old recognition records | Implemented, unverified |
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

The full receipt entry removes inherited PYTEST_ADDOPTS/PYTEST_PLUGINS only from
its child environment and forces the existing impact selector into full mode.
The invoking user's environment is not changed. Direct general/owned-PG runners
also discard inherited pytest selectors; explicit full manifest arguments remain
unchanged. The regression contract checks both full child scope and parent
environment retention. These new checks are not reported as executed yet.

Native selected sheets remain viewable with sheet_unavailable and source blockers
when the authored source is unavailable. This does not authorize adoption or
borrow historical authority; negative tests must preserve both the blocked state
and the operator's exact saved input. Initial saved imports retain their distinct
validated immutable source receipt.

Do not treat old tests as the cause without reading each failure. Genuine source
defects are reported to the parent. Do not replace unknown expected values,
remove assertions, skip cases or reintroduce fallback to make this batch green.

## Integrated-source batch and recorded execution reads

The first integrated-source batch is running from clean develop
7dd6677ce19597146e76855cf53bcadbc1c00532 in the independent verifier worktree.
Its evidence directory is
/private/tmp/sawa-hospital-full-7dd6677ce195-20261024.
The general backend partition collected10075 cases. Failures have been observed;
this source is not verified and has not been deployed by this batch.
The verifier source remains unchanged while production repairs are made in the
separate assistant-business-workflows-20261023 worktree.

Source inspection found fabricated OCR-{order_id} reads and latest-job selection
in list/detail/summary, pipeline status, sheet/pages and current history status.
The shared binding reader now resolves only workflow_v2.ocr_job_id, checking
order/attempt, captured current document, the original upload/document/hash,
explicit selected version and the independent workflow delivery input.
Missing or inconsistent bindings produce explicit diagnostics without selecting
another job or changing persisted data. List reads stay bulk rather than adding
per-order job/source queries. Audit history retains its legacy search targets and
also includes actual job IDs belonging to the order.
Saved-sheet origin is a separate identity: it comes from its base evidence's
recorded execution, not from a newer candidate job.
These repairs remain unverified until all known implementation work is complete.

Required batch regressions for this failure class:
- UUID job remains current with newer unrelated/stale jobs present, across list,
  detail, summary, pages, workflow and current history status.
- Missing job, foreign order, old attempt, changed current document and malformed
  receipt identities are refused without a latest/name/cache substitution.
- Explicit historical-version selection works when its original document,
  upload digest and version identity agree.
- Changing captured current_document_id to match a newer document cannot bypass
  the independent delivery's original processing input comparison.
- Receipt document/version/digest alterations are refused; operator quantities,
  saved sheets, audit rows and original evidence remain unchanged.
- Progress reads use the same binding. Permanent binding errors stop only their
  owned poller; late errors cannot clear another document/job/attempt/revision.
  The existing workflow banner distinguishes binding failures from execution
  failures and shows the concrete cause. A fresh explicit null clears an older
  detail error without changing operator input.

## Completed failed baseline and additional root causes

The clean integrated 7dd6677ce195 source completed all 30 local commands:
25 commands succeeded and 5 failed. Backend: 475 failed, 9408 passed,
184 skipped, 8 errors in 3370.56 seconds. Browser: 136 failed, 2 passed;
133 failures reported a redirect loop. These are not a successful receipt.
Evidence: /private/tmp/sawa-hospital-full-7dd6677ce195-20261024/failed-run.json.

- Daily delivery requires template_uri in the accepted source resolver but
  rejects it in the workbook embedding path. The requirement appeared in
  05fde4ce (2026-10-21), while the rejection dates to 24c392aa (2026-05-16).
  This is a real integration inconsistency, not merely an obsolete assertion.
  Repair must preserve the authored template package rather than remove its URI.
- Contact OCR did not emit raw_text. The producer now supplies its own rendered
  word text alongside ocr_text; no test-input raw value is injected.
- The local image proof reused source/tmp/image-ci. Its local executor now maps
  that exact output root to its newly allocated evidence directory, preserving
  old proofs and keeping deploy inputs/source identity unchanged.
- Progress error side effects now reject an obsolete request revision before
  changing the banner or stopping the current poller.
- The PostgreSQL C1 guard batch had 200 passes and 3 failures: two fail the
  run-scoped ledger-name check; one expects the old workflow commands without
  the CA update. PostgreSQL corruption has not been established by these failures.

These new repairs are uncommitted and unverified. The remaining failure-index
records were read against their tracebacks; fixture updates are not yet complete.
The repeated-apply synchronous-input conflict still requires diagnosis from its
exact guard rejection and must not be classified as an obsolete expectation.

## Current implementation and fixture batch

- Daily delivery now retains each authored workbook package and packages complete
  facility workbooks in a ZIP. Original support worksheets, drawings and print
  definitions are retained; multiple delivery dates clone worksheet package parts.
  Package-retention regression cases have been added but not executed.
  Review found that table-bearing worksheet clones would share table identities;
  these now fail explicitly before publishing instead of producing a corrupt ZIP.
  Single-date tables remain supported. General multi-date Excel-table cloning is
  not implemented and is not claimed as complete.
- New recognition bases no longer seed quantities from stored OrderLines. The
  authored weekly physical structure is copied without borrowing saved values.
  Operator-saved sheets are not rewritten by this change.
- Current candidate template resolution consumes the validated current-sheet
  configured-input receipt instead of an unrelated legacy candidate template.
- Formal user/audit timestamps retain the nullable shape of migration0035;
  no live schema or production records were changed.
- The isolated fixture writer has frozen four unit-test files. Parent reviewed
  and copied their changes into the repair worktree through apply_patch. No tests
  executed, no stage/commit/push. The first producer's no-recapture assertions
  were restored around the actual producer, not around fixture preparation.
- Parent fixture updates retain raw artifact hashes, real authentication grants,
  terminal-owner protections, read-only snapshots and explicit source geometry.
  Test-only stale-diagnostic injection deliberately uses out-of-band SQL while
  keeping native terminal receipt and execution ownership fields unchanged.

No new local test, type check, build, Actions run or deployment has been executed
against this repair batch. The earlier failed clean-source run remains preserved.

Additional review/fixture work in progress:
- Actual admin-user save errors are now displayed by EditForm once rather than
  both EditForm and the page; query invalidation and authority refresh remain.
- Copied-sheet formulas and local defined names share token-based self-sheet
  reference translation. String literals and foreign sheet references are not
  text-replaced. Formula and package regression cases remain unexecuted.
- Manifest template consumption now checks the captured facility ID against the
  requested facility before consuming its authored template.
- HistoryCase now uses an independent UUID by default, avoiding collisions with
  deliberately unowned OCR-order/message-ID diagnostic rows.
- Unowned diagnostic corruption is installed only by an explicit test SQL helper
  that refuses recorded current jobs and any execution/terminal owner metadata.
  Authorized terminal update guards remain unchanged.
- Six ingest lifecycle cases now resolve their original job through the actual
  generation's IngestDelivery receipt, with independent successor assertions.
- The operator receipt-wire test now uses the real native failed-job review,
  full-manual save and newly claimed workflow resume scenario. Receipt-less
  hand-built overrides are negative cases, not successful resumes.
- The legacy local child runner now removes inherited pytest/impact selectors
  and forces full selection while leaving the invoking environment and TLS trust
  settings unchanged. A fresh-image-evidence/full-child-scope test was added.

Known remaining fixture groups include the original-target geometry specimens,
captured evidence/projection components, and the worker's pending first-pass
boundary cases. The repeated-apply conflict remains unresolved. No clean-source
success, develop integration or stg verification is claimed for this batch.

Further source/fixture reconciliation (not executed):
- Current Hakodate reconciliation dispatches to captured native recovery before
  the retired structured first-pass branch. The old success fixture must not
  drive a relaxation of owned H2 artifact validation. The isolated writer has
  supplied a native recovery replacement; parent review is still in progress.
- Removed latest-evidence helper expectations now reject uncaptured reads. A
  positive selected native projection reuse case checks exact rows and database
  immutability, without borrowing unselected latest/cache artifacts.
- Shared-row merge specimens now preserve explicit saved blanks, zero and 17.
  Matching display labels with different row IDs remain a rejected join.
- Evidence persistence specimens now exercise separately owned native 3/3/9
  runs, preserve each prior payload, and reject ownership-free source labels.
  An actual native artifact packet supplies the positive classification case.
- A suspected missing template-version argument in repeated save was disproved:
  the local persistence wrapper already passes the captured evidence version.
  The attempted extra argument was immediately removed. The repeated-apply
  cause remains unconfirmed; no fix or successful verification is claimed.

- Subsequent read-only review found a concrete repeated-save failure mechanism:
  `_append_edited_ocr_revision` shallow-copied the ORM JSON payload and mutated
  its shared `_edited_ocr` dictionary. This can hide the JSON change from ORM
  dirty checking while advancing the timestamp, so the subsequent refreshed CAS
  reads an older body. The write now deep-copies the payload before mutation.
  The existing 11 -> manual lines20 -> edit3 regression additionally inspects
  the persisted cache in a separate session. Runtime confirmation is pending.
- The history fixture now explicitly saves its declared operator lines after
  native evidence selection, which intentionally invalidates old derived rows.
  Recognition evidence and the independently declared operator values are not
  substituted for one another.
- Daily reference specimens explicitly declare facility-aggregate columns for
  inputs without an area. The complete delivery/label bundle check reads the
  two separate workbooks from the ZIP and retains label quantity assertions.
- Monthly-menu component inputs explicitly declare regular diet identity so
  the test does not depend on guessing a blank identity from another row.
- Bagging source identity used the aggregation normalizer that collapses
  daycare/staff into regular. The retained weekly-weight database contains all
  three distinct declared rows at the same physical slot; the source-row
  duplicate gate and item ranking therefore rejected legitimate input.
  Monthly item/entry identity now uses the existing identity-preserving delivery
  diet normalizer. Aggregation normalization itself remains unchanged. Added
  regular/daycare/staff/diabetes component cases verify distinct source choice,
  distinct authored amounts, explicit source-diet retention and unchanged
  aggregation aliases. Full bagging/weekly output verification is pending.
- The projection writer's final single-file delta is now copied into the parent.
  Canonical record and augmentation positives use actual registration, queued
  execution, capture and producer code; doubles remain at external rendering
  and recognizer leaves. Explicit logical targets replace inferred shared-row
  copies. Raw full-width/nonnumeric/blank values stay editable and unchanged.
- Parent `git diff --check` passed after integration. All writers are frozen for
  staged review. This is not a test, build, commit or deployment success. The
  next full local batch must use a new clean named verification worktree and
  fresh evidence directories, preserving the earlier failed 7dd source/run.

## Fixed-source 9d batch and aggregate repairs

Source `9d046f3be08ed895ca5cc791a3576812a2de9412` was checked in the
clean `hospital-batch-verification-20261024` worktree. Evidence is retained at
`/private/tmp/sawa-hospital-full-9d046f3be08e-20261024-2`.
All 30 commands finished: 26 returned zero and four did not. Backend collected
10,116 cases: 9,578 passed, 310 failed, 184 skipped and 44 setup errors, in
3,491.03 seconds. The browser command was deliberately interrupted after
repeated obsolete-selector failures: nine passed, 54 failed, one interrupted
and 74 not run. It is not a full browser success. Config had three failures;
the C1 guard command had one obsolete bootstrap-list assertion. Actual Linux
agent checks passed 433 cases; four image proofs and TLS checks passed. These
results do not establish success for the new dirty repair source.

The following aggregate changes have not been rerun or deployed:
- Preserve explicit monthly source category (including main-A variants) before
  meal-slot category resolution, rather than collapsing it to an aggregate.
- Return the flushed terminal receipt including its real delivery owner.
- Read workflow candidate identity from a matching successful terminal receipt,
  not mutable stale diagnostic pointers. Missing/foreign/failed receipts are
  negative cases; selected values and source records remain unchanged.
- Distinguish an unavailable selected artifact from an output never produced.
- Allow workflow diagnostic reads to expose unresolved menu amounts without
  bypassing quantity calculation or output gates.
- Derive row-review calendar and boundary count from the explicitly selected
  period, not a guessed Sunday-to-Saturday range, through producer, validator
  and UI. Include cross-month and partial-period specimens.
- Remove the reparse scope heuristic that reselected dates using OCR clusters
  or old lines. The explicit facility/period and canonical physical menu rows
  alone determine the scope; missing period/facility/menu is refused.
- Retain a current refresh's own error across its generation increment while
  fencing off errors from superseded routes, revisions and refreshes.
- Bound the sticky date column in the monthly menu table so it does not cover
  actionable menu cells. Pointer interception is fixed in layout, not bypassed
  with forced test clicks.
- Context reconfirmation deleted all OCR evidence while patch/history foreign
  keys still referenced it. It now invalidates current selection and derived
  materialized rows without deleting evidence, saved drafts, patch candidates,
  confirmed snapshots or output-history artifacts. Regression assertions retain
  every historical row and forbid automatically borrowing an old saved sheet.
- Update recorded-job mocks, actual ownership fixtures, row calendar inputs,
  full-source controls and current browser entry labels as one batch. Static
  header fixtures explicitly identify a static master; generated body fixtures
  use the real workbook builder rather than manufacturing a schema flag.

The legacy real-PDF geometry cases included facility-14 files assigned to other
facilities. Their unowned structure path recaptured current JSON and generated a
workbook without the selected period; that fallback now explicitly refuses a
missing captured structure. The five old cases are negative contracts that do
not open those PDFs or invoke recognition, not replacement live-flow proof.
The manifest's explicitly supplied structure workbook path is retained, as are
current owned native artifact and numeric-FAX regressions. These changes have
not been executed. Remaining fixture and implementation groups are still being
repaired. No new full verification, commit, integration,
Actions run, billing change or stg deployment is claimed for these edits.

### Auxiliary total transcription follow-up (not yet verified)

The shared display path erased conflicting totals, reanchored values across
rows, guessed dates by sequence, and derived missing totals from quantities.
Both display callers now use the existing canonical row identity mapping without
the raw-table reoverlay or preemptive blanking. Conflict diagnostics no longer
erase or redistribute cells. The dictionary caller copies nested values before
writing, so refusal cannot mutate the caller's source. The unused 450-line
sequence/derived-total helper chain was removed rather than left callable.

The new auxiliary-cell regression covers both callers, unbound raw dates and
missing dayparts, zero/blank/dash/symbol/full-width values, conflicting totals,
and identity rejection with input preservation. Existing sequence datasets now
check preservation rather than inferred totals. No execution success is claimed;
the known remaining implementation and fixture groups must be finished before
the serial aggregate verification starts.

An awaiting pipeline resend also stored its new delivery receipt before changing
the job's old output reference. The ingest flush fence correctly rejected that
mixed pair. The common receipt writer now updates both in the same transaction,
without changing original input, ownership, PDF/configured-input checks or
generation checks. The existing real-ingest resend regression additionally checks
the persisted handoff pair and rejection of the superseded output.

The blank-rerun regression now checks the actual recognition boundary with an
explicit operator quantity two in OrderLines. It requires the new evidence to
remain blank and the old selected evidence, saved draft and every line column to
remain unchanged. Queue/history fixtures use real queue/claim/completion instead
of invented current-job pointers; native recovery uses an explicit isolated
artifact directory. These additions are also unexecuted.
