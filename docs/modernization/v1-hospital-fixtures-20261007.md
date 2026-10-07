# Hospital Original-Input Fixtures: Component Technical Preparation

## Actual SDK Duration Continuation (Current)

Base/generating HEAD `3b0cecdda1f95a35f49b31f13504d05b6aedbca8`, dirty source.
Earlier sections are historical. Both ingest and workflow dispatch inspectors
now use official `tasks_v2.Task.pb(task).dispatch_deadline` integer seconds/nanos.
They compare both exact raw fields against the saved contract (omitted nanos is
protobuf zero), without timedelta float conversion, rounding, or tolerance.
Ingest's real typed SDK mutation now edits underlying protobuf nanos; workflow
adds exact positive and one-nanosecond negative neighbors. Headers/provider extras
and other existing exact checks remain intact.

Four permanent-successor refusal failures arose while fixtures injected parent
metrics/status corruption after terminal ACK using ORM flush. The before_flush
owner fence correctly rejected this unowned write before refusal was exercised.
Those two injection branches now use explicit physical Core UPDATE; production
ownership checks are unchanged. Real original intent/claim/terminal producer is
retained; configured snapshot adapter remains a labelled component isolation,
not actual original-capture/operator/provider proof. All original refusal, no
redispatch, parent receipt/order immutability and duplicate assertions remain.
No actual Session retention root defect was established.

Actual isolated SDK 2.26.0 target read only:
`/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-main/tmp/v1-hospital-task-sdk-20261007`.
No install or shared venv write in this continuation. Focused two files:
**105 passed, 11 warnings, 5.84s, exit 0** (103 prior + two new workflow cases).
No lifecycle configuration cases were changed or broadly rerun.

Exact command from dedicated WT/backend, P denotes its absolute WT path:

```sh
env -i PATH=/usr/bin:/bin HOME=$P/tmp/sdk-duration-proof/home TMPDIR=$P/tmp/sdk-duration-proof/temp XDG_CACHE_HOME=$P/tmp/sdk-duration-proof/cache PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-main/tmp/v1-hospital-task-sdk-20261007:. DB_URI=sqlite:///$P/tmp/sdk-duration-proof/bootstrap.sqlite3 NO_GCE_CHECK=true GOOGLE_CLOUD_DISABLE_GRPC=true /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python -m pytest tests/unit/test_ingest_delivery_contract.py tests/unit/test_workflow_ocr_delivery_runner_contract.py -q -p no:cacheprovider --basetemp=../tmp/sdk-duration-proof/cases --junitxml=../tmp/sdk-duration-proof/results.xml > ../tmp/sdk-duration-proof/pytest.log 2>&1
```

SHA256 source, relative to backend:
- services/ingest_delivery_dispatch_service.py: `9920e9d289b51c9659fe92ff1eb5909e1f8b8b807cfdcd6d077bb6662b9228f4`
- services/workflow_ocr_delivery_dispatch_service.py: `2cbf8775a9933f4ca6aa9e15def716e0ac841df64eafe682d20f77ca4f7c7310`
- tests/unit/test_ingest_delivery_contract.py: `9a71cbba567e16d76badba18ffba6c94c252019f668403cd5500c878a1885cc1`
- tests/unit/test_workflow_ocr_delivery_runner_contract.py: `3b0853aa294c610b9ace39c703dc0c62fad2e815541ea130f1b38f127e4009ab`

Owned raw artifacts under `$P/tmp/sdk-duration-proof`:
pytest.log SHA256 `66a57ca6302b42b545c70fe1b975c137dfd7541b415a291ee74218d04f9b7585`;
results.xml SHA256 `30fbc663b73f8d0901abcf7ae647bbc9de05e3a7f0bc8316d6fb5b268fd0dbb0`.
Parent 96-pass/7-failure XML remains untouched in main/tmp/v1-parent-hospital-sdk-20261007.
Stage freeze only, no commit/merge/push/deploy, no running needed session or PG.
Parent monitor/commit/integrated actual test remain required; not whole acceptance.

## Projection Metadata Continuation (Current)

Generating base: `29323324ba28007c1ad11d134096e66ef042f579`, same named
fixture branch, dirty source. Earlier preparation hashes/results below are
historical, not this continuation's source proof. No commit/integration performed.

Root: physical projection represents version_id/valid_from/valid_to/facility,
not every raw authored-version metadata key. `verify_projection` now compares
those four matching authored keys using strict snapshots_equal. It does not
strip or rewrite the full authored receipt: full digest and exact receipt checks
still include unknown metadata. Missing represented keys still stop. Existing
unknown_version=None positives remain unchanged and pass.

Added eight cases: physical bool/int/float/null mutation rejection, unknown
version metadata alter/remove/add rejection against full receipt and digest,
and preserved raw bool/int/float/null metadata with semantic key-order equality.
Only canonical_facility_lineage.py and its unit definitions changed as source.

Focused unit: **43 passed, 6 warnings, exit 0**, no formal DB operation; existing
fake Session models read/owned materialization statements without guard mocks.
Owned explicit SQLite URI was supplied; no provider/install/network/broad rerun.
Exact command, from backend (P is the absolute dedicated WT path above):

```sh
env -i PATH=/usr/bin:/bin HOME=$P/tmp/projection-metadata-proof/home TMPDIR=$P/tmp/projection-metadata-proof/temp XDG_CACHE_HOME=$P/tmp/projection-metadata-proof/cache PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. DB_URI=sqlite:///$P/tmp/projection-metadata-proof/unit.sqlite3 NO_GCE_CHECK=true GOOGLE_CLOUD_DISABLE_GRPC=true /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python -m pytest tests/unit/test_canonical_facility_lineage.py -q -p no:cacheprovider --junitxml=../tmp/projection-metadata-proof/results.xml > ../tmp/projection-metadata-proof/pytest.log 2>&1
```

Source SHA256: canonical lineage
`6c7f9cc5ef62d012db3eabcc137390b6f54ff0a6c15bd284bb12c497f47a34a5`;
unit `044bf4750a2929aafe6952ca2b0d1f9b6066b17f8ba8daf069dd59d725ac3b0c`.
Log `tmp/projection-metadata-proof/pytest.log` SHA256
`245ca99d3fadc4f71cc1a5a16a41ac113cc6518302d6e75456f0180cf401edb8`;
unit XML SHA256 `5013743ed4f46a375b9f10731d34dbc0cc4a318055687344a4c8749f59a73ba3`.

Actual parent XML read, not rerun:
`/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-main/tmp/v1-parent-hospital-jobs-20261007/results.xml`,
SHA256 `df18da91a09acf0f8482a2d413b84985bd0ea17024bb996a7cd7c91083e2a6b5`.
87 recorded failures grouped by actual exception/assertion: lineage projection
25; missing google.cloud.tasks_v2 23; receipt_owner_lost 4; delivery configuration
required 25; original missing 2; workflow revision contract 8 (missing mock
secondary_actions_json 1, missing checks 1, stale source-string assertions 2,
reparse persisted result missing 3, terminal persist refused 1). Those other
classes remain unmodified, not established production bugs merely from XML.
Full per-case traces/grouping are preserved in owned tmp/record.json. Parent
reported 128 passed and KeyboardInterrupt exit 2; not a complete neighbor run.
Whole fixture/date/fresh producer/integrated acceptance remain parent gates.
No owned PG server or needed running session remains. Stage freeze only.

## Status

Not complete. The original selected-lineage root and literal guards now have
passing component evidence. The dated February legacy case and fresh-upload
selection producer gap remain open. No whole-193, actual provider, fresh-upload,
operator-flow, integrated-SHA, live, deployment, or all-code/DB acceptance is claimed.
The historical freeze below is superseded, not overwritten as a successful run.

Worktree: `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-v1-fixtures`

Branch: `codex/modernization-v1-hospital-fixtures-20261007`

Base and generating HEAD: `5d895c5b3cba6424d7e086ecf7d30e1560d41286`.
All results below were generated with dirty source, not a new commit.

## Current Authorized Root And Source Boundaries

Failure classes: unresolved worker service binding; legacy active-status evidence
eligibility conflicting with archived immutable JSON projection; Python literal
equality accepting bool/int/float substitutions; selected historical source
compared against an unrelated unversioned current root.

Shared decisions: `_order_execution_input`, selected configured-source capture
and validation, `persist_ocr_evidence_run` -> `persist_evidence_run`, and the
existing synchronous/workflow configured-source fences. Explicit Order selected
ID is read in the same Session with Core projection reads/locks, real
`verify_projection`, and creation source proof validation. Its projection,
lineage, source proof, full raw source envelope, selected authored facility, and
unchanged policy are frozen at first capture. Existing captures are checked, not
enriched. Current root fields never replace historical selected fields.

Production changes are limited to the worker binding and the directly needed
source capture/validation/ID propagation in `canonical_facility_lineage.py`,
`config_service.py`, `workflow_attempt_guard_service.py`, `ocr_evidence_service.py`,
`order_service.py`, and the one configured-source comparison in the existing
`order_workflow_v2_service.py` shared mutation verifier. The latter path was
reported before editing. No recognizer, quantity mapping, dates, selection policy,
schema, API/UI, status design, or original PDF/menu semantics were changed.
No existing capture is repaired from the current DB. Legacy captures stop.

Strict `snapshots_equal` / canonical JSON comparisons reject boolean/integer/
float/null/unknown-field substitutions and duplicate selected versions. Semantic
key ordering is allowed. Physical projection tampering tests use Core UPDATE:
ORM assignment alone suppressed bool/int/float-equivalent updates, so those two
earlier failures were invalid physical tamper fixtures, not relaxed assertions.

## Current Focused Evidence

Source definitions before: worker 19, recovery 7 (26 original collected cases).
After: worker 24, recovery 6. Parameterization preserves three legacy recovery
IDs and both direct-inline IDs; current collection is 65 cases, not 193.

Final coherent-source focused run: `materialize-literal-root`, exit 0,
54 passed, 11 deselected, 335 warnings in 21.68s. This includes actual pre-registered
delivery -> owned runner -> quantity 2 / Menu A / 2F / 2026-04-06 and persisted
evidence, failed/no-autoqueue business cases, immutable original budget/receipt,
23 selected projection/literal cases, empty/nonpersistable handling, duplicate
delivery idempotence, explicit operator restart, and fresh missing-version stop.
Five additional cases invoke real `advance_nonworkflow_order_input` inside the
actual owned runner: normal and changed-current-root preserve the frozen original
template/config/start receipt; physical bool/int/float substitutions stop. The
injected test corruption is rolled back, and the runner retains quantity 2 and
the exact saved original input. This is pre-registered component evidence only.
Six real `materialize` cases reject selected/raw bool/int/float/null/unknown-field
mutations before any projection insertion and preserve valid key-order semantics.
The shared selected facility and effective-date comparisons now use strict
`snapshots_equal`. Adjacent creation proof checks require canonical source and a
nonempty string revision; `canonical_date` rejects numeric/boolean dates. Original
creation proof remains unchanged. This audit concerns this exact sibling path,
not a global assertion that no other literal equality remains.
No worker or source guard is mocked. External OCR responses are authored doubles.
The PDF is an explicit blank component byte fixture, NOT facility/week PDF proof.
Acceptance + original intent are real; external Cloud Tasks dispatch is not proof.

Final log SHA256:
`43d8daa07fada4542777613371e5a3c99f1f2101a6b8d82b7a4b9f739c67d563`.
Absolute artifact root:
`/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-v1-fixtures/tmp/fixtures-proof`.
Every run's `report.json` records exact child command, closed env, owned SQLite
and basetemp paths, base HEAD, dirty source SHA256, inventory, exit and log SHA256.
Outer runner: `env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python tmp/fixtures-proof/run_focused.py LABEL [recorded -k selector]`.
No runtime install, credentials, network, provider, PG server, commit or deploy.

| New run label | Actual result | Exit |
| --- | --- | --- |
| original-lineage | 5 failed, 1 passed, 22 deselected | 1 |
| selected-original-capture | 16 passed, 24 deselected | 0 |
| normal-selected-source | 1 failed, 17 passed, 22 deselected | 1 |
| migrated-owned-callers | 2 failed, 10 passed, 28 deselected | 1 |
| strict-literals-and-owned | 3 failed, 34 passed, 11 deselected | 1 |
| fresh-and-empty-diagnostic | 2 failed, 1 passed, 46 deselected | 1 |
| assigned-two-files-after-migration | 3 failed, 50 passed | 1 |
| physical-literal-selected-proof | 32 passed, 21 deselected | 0 |
| strengthened-normal-and-literal | 32 passed, 21 deselected | 0 |
| final-approved-component-proof | 43 passed, 11 deselected | 0 |
| actual-advance-immutable-proof | 30 passed, 29 deselected | 0 |
| final-component-with-advance | 48 passed, 11 deselected | 0 |
| materialize-literal-root | 54 passed, 11 deselected | 0 |

Earlier reports retain invocation-time hashes. Source was edited while the
assigned-two-files and physical-literal runs were pending; their logs are
diagnostic history, not immutable final-source acceptance. Final-source hashes
are checked against the final report before staging. All prior failures,
including the requested 16-failure `freeze-full` baseline, are retained.

## Old Versus New Fixture Contracts

- Worker positive is accurately named `pre_registered_original_component`.
  `register_authored_original` manually assigns a real materialized canonical ID
  BEFORE runner capture. It proves a stored-original component, not fresh creation.
- Old worker force-restart mock becomes real explicit operator restart + fresh
  transport intent + actual owned run; original payload/digest is unchanged.
- Old unowned inline provider/evidence/finalization mocks become explicit
  `ingest_execution_lease_required` before provider/write, with counts unchanged.
- Old OCR jobs containing only status/output URI/legacy metrics cannot be
  auto-adopted/resubmitted: real recovery returns 0, exact job snapshots unchanged.
  Terminal-failed filtering remains a component assertion, not recovery flow proof.
- Archived same-message records are not implicitly linked. Upload linkage remains
  absent; archived Order/document references and processed document are preserved.
- Owned first-pass completion preserves upload/order/document/evidence assertions.
  The retired automatic latest-evidence workflow adoption expectation is replaced
  by explicit no selected/active evidence and blocked apply. No operator decision
  is fabricated. Duplicate delivery keeps the exact saved evidence/receipt.
- Empty/nonpersistable actual output fails terminally without lines or autoqueue.
  Error code/message are persisted. Default notification threshold is 2; first
  attempt has no notification timestamp. The original stale business failure
  cases still assert notification at attempt 2. The earlier unconditional new
  empty-case notification expectation was a test-author error, retained in logs.

## Remaining Actual Gaps

1. `test_owned_delivery_marks_completed_and_links_entities`: original quantity 2
   persists, but date expectation 2026-02-20 fails with actual 2027-02-20 for
   authored `02/20` under unchanged default MM/DD / nearest-received-year policy.
   Original `received_at=datetime.utcnow()` is retained. No convenient timestamp,
   date-format policy, year guess, skipped case, or weakened date assertion is used.
   It remains a dated-policy dependent case, not silently counted passed.
2. Real fresh upload without pre-registration reaches
   `original_template_projection_missing` before provider, persists failed/no queue,
   and creates no projection, evidence or lines. `uploaded_pdf_service.py:422`
   builds no explicit operator-selected version/effective-date input;
   `IngestEmailPayload`/parser likewise have no such fields; fresh Order creation
   in `order_service.py` leaves `template_version_id` absent. Propagating a genuine
   operator selection requires upstream input work, not choosing week start, FAX
   date, received date, latest/current JSON, or fixture manual assignment. This
   missing selected-version producer is distinct from the held 13 dated callers.
3. Broader contract/ingest/evidence/history/output-builder migration is the next
   assigned unit, not part of this two-file count. Legacy STG missing timestamps
   and human/date-policy questions remain separate. Integrated-SHA rerun and
   whole acceptance are still parent gates.

Normal stored-original + literal checks passed before preparation staging.
This is not completion of the remaining date or fresh-source producer work.
No needed execution session remains running; no owned PG server was started.

## Current Dirty Source Hashes And Record

All hashes below are the final focused run's actual source hashes at base HEAD
`5d895c5b3cba6424d7e086ecf7d30e1560d41286` plus dirty working source, NOT a new SHA.
The final record at
`/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-v1-fixtures/tmp/fixtures-proof/current-preparation-materialize/manifest.json`
includes every retained run's exact command/env/result/log hash, every raw artifact
path/hash, the staged binary patch/hash, generating base, branch, and final doc hash.
The earlier `current-preparation-record/manifest.json` is retained with SHA256
`3c3a38e99a56327b6935aae44a05cc181e7fdddd709a8055d9484a45ab8b50e2`.
The superseded `current-preparation-final/manifest.json` remains unchanged with
SHA256 `24e48af6a685cfc4b099965f66b1f7ccae9191dd42ba36e1a8dc8afc154380db`.
The isolated advance log SHA256 is
`c1865496089d1f8234ec44f1f5facbafbb3b7106ef8ba92f08b5d3fb9915ef86`.

| Source file under backend | SHA256 |
| --- | --- |
| tests/integration/test_uploaded_pdf_worker.py | `f9ac32e6b1f9d400048d5c85af975d0d66966a121f17a60e94b5266b15b5c33c` |
| tests/integration/test_uploaded_pdf_recovery_flow.py | `f1e8345a6d7279373960ed76a7d162e1b9e2c902abebc3f1638a8a38af4ce54c` |
| tests/helpers/ingest_original.py | `568beec42801329fc63300fd9c5c8d47688467bb57cc37ff6dc7b3aa1cb9e65c` |
| src/workers/ingest_worker.py | `98302b4d7cedece163b7be9cbc613a3c326169e3eda45eeee0153da786048a21` |
| src/services/workflow_attempt_guard_service.py | `cec35ae337dada51dccd4cd74017d4353706825c2c4bc58dcd44249339e3c340` |
| src/services/ocr_evidence_service.py | `9757d9a979c38a96a0bbe7a95f1cd7be1179bb3544cf53801f01ae4f9b924c58` |
| src/services/config_service.py | `904350ed5954ea1b1519a0ca5f8b4aa7028de85dd336f4efa46531c20f59d339` |
| src/services/order_service.py | `1fc8a0992bd465166c6478f9bc5e602cf6924442f5b40be8f1cc4833e893f4d5` |
| src/services/canonical_facility_lineage.py | `baa66a36af060550c2212bd00ed7d94be5675f55568b0e34d1baf5aa0c70c08b` |
| src/services/order_workflow_v2_service.py | `a2b753e088f667114f8e3e77a2db21c826f2b15296b60fffe34b7f6020c229db` |

Exact final test invocation from the dedicated WT:

```sh
env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python tmp/fixtures-proof/run_focused.py materialize-literal-root -k 'materialize_selected_raw_literals or actual_owned_advance or original_projection_capture or pre_registered or owned_nonpersistable or fresh_upload or business_failure or interrupted_transport or owned_first_pass or operator_restart or unowned_inline or legacy_recovery or ocr_job_recovery_ignores or canonical_order_ocr or uploaded_pdf_linking or aliases'
```

`git diff --cached --check` returned 0 after explicit staging of these 11 files.
Raw artifacts/scripts remain ignored in WT/tmp, outside the production commit.
Preparation is frozen for parent review, not accepted/integrated/committed.

## Superseded Historical Freeze

Everything below describes the earlier binding-only freeze. Its scope, hashes,
remaining-case counts and guard blockers are historical, not current conclusions.

## Scope And Invariants

- Test changes are restricted to the two assigned integration files and
  `backend/tests/helpers/ingest_original.py`.
- The subsequently authorized production change is one real module import in
  `backend/src/workers/ingest_worker.py`. No other production file changed.
- No conftest, schema, auth, migration, skip, xfail, global autouse bypass,
  business guard mock, commit, merge, push, network, deploy, or integration-WT write.
- Existing completed/order/document/week assertions remain on the migrated
  positive cases. The original Menu A / 2026-02-20 / regular / 2F / quantity 2
  case now also checks the persisted physical OrderLine.
- Authored byte/PDF, JSON, menu-row and external pipeline fixtures are component
  inputs, not a verified facility/week operator PDF or real OCR-provider proof.

## Root Fix And Fixture Changes

Failure class: unresolved service-module dependencies in the shared first-pass
and recovery paths. Shared decision point: owned evidence classification and
persistence. Invariant: normal and negative branches resolve the same actual
`src.services.order_service`, without fixture-injected modules.

The import fixes references at worker lines 341, 419, 426, 451 and 1061.
All module-global service aliases were inspected; a symbol-table regression
checks referenced global service names, and asserts module identity. There are
no other undefined service aliases detected in this module. Other production
files were read only; no additional production edits were made.

The rejected `retry_uploaded_original` direct retry primitive was removed.
Business failures now use `delivery.process` -> actual `process_ingest_job` ->
actual registration/capture/failure settlement, with only the external pipeline
provider failure injected. Assertions cover provider arrival, persisted failed
terminal, warning fields, no next retry, exclusion from ready queue, original
generation/digest/budget preservation, and duplicate delivery not recomputing.
Transport interruption has its own case: `claim_delivery` / `renew_missing`
retain the original business lease and one budget attempt.

Positive cases use actual ingest receipt/intent producers, selected PDF digest,
physical menu rows, JSON configuration, canonical selection/materialization,
and actual pipeline-delivery receipt producer. Provider output fields copy the
real receipt; recognition and mapping provenance are produced by runtime code.
No projection status is patched and no current config is substituted for an
original selection.

Retired expectations changed explicitly:

- Filename week derivation in `build_ingest_payload`: `week_hint_required`.
- Filename week derivation in order creation: stop with order/document counts unchanged.
- Rewriting `ORD-UPL-NULL-WEEK` from a filename: stop; preserve null week,
  `DOC-UPL-NULL-WEEK`, document status, and empty superseded history.
- Filename-derived placeholder creation: stop before upload/ingest/order/document writes.
- Automatic recovery legacy backfill: forbidden by a failing call sentinel;
  assert the actual owned OCR recovery ordering instead.

The archived-order case was given its existing explicitly authored full week,
not an inferred filename week. Its original linkage assertion is unchanged and
still fails; it is not counted as repaired.

## Actual Remaining Production Blocker

Before the import fix, `freeze-full/pytest.log` proves `NameError: order_service`
at worker line 1061 on both migrated positive paths. That was not fixture lack.

After fixing the binding, the two positive paths reached real evidence storage.
The first run stopped at `legacy_current_config_lineage_stamp_disabled`.
The fixture was corrected to select and materialize its explicitly authored
canonical JSON version before execution, using real producers.

The resulting valid projection is `archived` by
`canonical_facility_lineage.py:83`. `ocr_evidence_service.py:629` requires
`active`, returns None, and the real caller raises
`first_pass_own_evidence_persist_failed`. Both normal quantity-2 and empty-row
nonpersistable paths reach this guard. The empty-row positive completion
assertions have not been rewritten as success or silently weakened.

`ocr_evidence_service.py` is outside the additional dependency-binding-only
production authorization. A correction to that separate shared guard requires
explicit scope authorization and proof preserving canonical selection and
ownership, not setting fixture projection status to active or disabling checks.
Normal and nonpersistable completion proof remains incomplete.

## Inventory And Runs

Source-definition inventory before edits: worker 19, recovery 7, total 26.
Current: worker 21, recovery 7, total 28 (one transport case and one module-alias
case added). Full before/after names are in each `report.json`; 193 is historical,
not a current proven inventory.

Owned artifact root:
`/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-v1-fixtures/tmp/fixtures-proof`.

Every run uses a newly created directory and bootstrap SQLite URI there,
existing `isolated_formal_schema` per-case Alembic databases, explicit env-i,
owned HOME/TMP/cache, and the read-only trusted Python. No prior DB/artifact is
overwritten. The runner and all raw artifacts remain ignored tmp files.

Outer command template, run from the worktree:

```sh
env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python tmp/fixtures-proof/run_focused.py LABEL [PYTEST_FILTER]
```

Actual labels/filters/results (each directory contains exact inner command,
environment, base, dirty hashes, inventory, exit and log hash in `report.json`):

| Label | Filter | Exit | Actual Result |
| --- | --- | --- | --- |
| initial | none | 1 | 17 failed, 9 passed; rejected direct-retry variant, not valid retry proof |
| owned-runner | none | 1 | 19 failed, 7 passed |
| failure-diagnostic | `-k owned_delivery` | 1 | 2 failed, 24 deselected |
| capture-diagnostic | `-k owned_delivery` | 1 | 2 failed, 24 deselected |
| capture-reason | `-k owned_delivery` | 1 | 2 failed, 24 deselected; incomplete authored columns |
| complete-config | `-k owned_delivery` | 0 | 2 passed, 24 deselected |
| terminal-budget | `-k 'owned_delivery or stops_missing_week'` | 0 | 4 passed, 22 deselected |
| positive-original | `-k 'owned_delivery or interrupted_transport or stops_missing_week'` | 1 | 1 failed, 5 passed, 21 deselected; actual NameError |
| freeze-full | none | 1 | 16 failed, 11 passed; retained requested baseline |
| no-filename-backfill | `-k 'missing_week or archived_orders or owned_delivery or interrupted_transport or run_uploaded_pdf_recovery_once'` | 1 | 3 failed, 9 passed, 15 deselected |
| dependency-binding | same filter as preceding row | 1 | 3 failed, 9 passed, 15 deselected |
| authored-version | `-k 'owned_delivery or interrupted_transport or missing_week or run_uploaded_pdf_recovery_once'` | 1 | 2 failed, 9 passed, 16 deselected; parent projection missing |
| authored-parent | same filter as preceding row | 1 | 2 failed, 9 passed, 16 deselected; real archived/active guard conflict |
| binding-and-guard | `-k 'service_aliases or owned_delivery or interrupted_transport or missing_week or run_uploaded_pdf_recovery_once'` | 1 | 2 failed, 10 passed, 16 deselected |

There were no broad unmodified-test reruns after the parent dependency-fix
authorization. Focused failed runs are preserved, not hidden by deselection.

## Frozen Hashes

Final tested source SHA256:

| File | SHA256 |
| --- | --- |
| worker integration test | `3dd880803d278bb5e9857c92c71172f9139bda0b27868f2043401faae022a62c` |
| recovery integration test | `43157ac2bc16be2d47b8c754c2f0eb3406c71e90a67a8a05f8c99d03b2601537` |
| helper | `568beec42801329fc63300fd9c5c8d47688467bb57cc37ff6dc7b3aa1cb9e65c` |
| production worker | `98302b4d7cedece163b7be9cbc613a3c326169e3eda45eeee0153da786048a21` |

Retained `freeze-full/pytest.log` SHA256:
`8524b82299661cdb103d169701a50a6370f1a4cc16de83c6f06f6cd63369dc43`.
Final `binding-and-guard/pytest.log` SHA256:
`de1ebcee8ef67dca4e32e5de994817f0e2a07ab3175d7439014825759c4e60f0`.
Each intermediate log/source hash is retained in that run's report. Final
staged patch/doc/runner/report artifact hashes are in `freeze-manifest.json`.

## Residual Cases And Handoff

The final 16 deselected cases were not rerun or claimed repaired. Baseline roots:
force-restart duplicate IngestJob insertion; archived implicit linking expectation;
obsolete output-bucket monkeypatches; reconciliation missing registration/original
mapping/ownership receipts; old recovery jobs rejected rather than auto-resubmitted;
direct inline call missing the ingest execution lease. These are fixture/caller
migration residuals, separate from the two demonstrated real production failures.
The broader ingest/evidence/workflow/history/output-builder inventory is deferred
to the next assigned unit, not counted as done.

No subprocess required for these runs remains active. No PG server was started.
No commit/integration occurred; parent review, canonical guard decision,
integrated-SHA rerun and whole acceptance remain required.
