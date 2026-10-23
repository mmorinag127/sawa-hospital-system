# Authored Native Selection

## Failure Class

Registration or an owned generic pipeline receipt is not a current Hakodate
result. Current sheet projection additionally validates original physical
targets, artifact ownership and the selected evidence's source receipt. A
fixture that only registers or returns generic `table_rows` cannot prove a
positive current sheet read/save, even if its job metadata is owned.

`tests/helpers/native_selection.py::select_authored_native_result` consumes an
already registered, context-confirmed authored original. It uses actual queue,
delivery claim, attempt context, `rerun_ocr_evidence_only`, terminal persistence
and explicit result selection. The production producer creates source/target/
artifact provenance. No test inserts a selectable evidence record or weakens a
runtime guard. The shared caller's source table is input to an external
recognizer double only, not a prepared production result.

## Input Integrity

- Dates must resolve uniquely inside the declared period; no received-date or
  default-year choice is allowed.
- Every supplied quantity identity must exist in the unchanged original target
  plan. Duplicate identities and conflicting values inside a merged physical
  region are refused rather than collapsed.
- Unlisted physical cells use a literal explicitly supplied by the caller;
  current components declare it as an empty string. This is authored test data,
  not a production default or inferred recognized quantity.
- Source and physical dayparts use the same existing canonical identity function
  (`朝`/`朝食`, `昼`/`昼食`). Unknown tokens and duplicate identities after that
  canonicalization are refused. Quantity values and the physical plan are not
  altered by this comparison.
- When stored line quantities are also the explicitly authored recognition input,
  every diet/area pair must have a declared physical quantity column. Unmapped
  source quantities are refused before queueing, not silently converted to blank.
  Explicit `ocr_rows` cases separately declare recognition input and stored
  business-line input; tests of unmapped business lines do not adopt those lines
  as recognition truth.
- The captured original PDF is loaded and verified by the production receipt
  API. Target semantics and expected cells are not shortened or regenerated.
- Artifacts are confined to the caller's temporary directory. External-provider
  patches last only for that execution, avoiding cross-order source capture.

This helper is not a live FAX scenario, recognition-accuracy proof or a substitute
for same-input production comparison. The current history fixture uses it;
dependent output fixtures import it but are not yet integrated or verified.

## Remaining Migration

The earlier registration-fixture changes did not finish current source
preparation. The parent subsequently mapped the old cache/line-derived adoption,
recovery, page, diagnostic-draft and edit/export responsibilities below to actual
native selection, formal operator saves or explicit unowned-source refusal.
This is static migration, not a passing test result. The independent worker's
fixture batch must be frozen and integrated before consolidated verification.
No new runtime test result or deployment is claimed here.

The selected-history fixture also supports an explicitly declared
`blank_source=True`, mutually exclusive with authored rows/line quantities.
This runs the actual native producer with blank external-recognizer cells for
the complete original physical plan. It is not automatic missing-input rescue.
The two-day explicit December range and the 28-day March range use this declared
blank input. The February 8–10 case retains explicit 5/6/7 source quantities;
December 26–27 retains 7 and a blank across the declared year-boundary period.
The original daypart ordering and exact date-set/row-count assertions remain.

The base history registration now confirms the explicit context through the
actual workflow command, but does not select evidence. Nine missing-menu read
cases retain their original historical rows, noisy dayparts, zero/58 inputs and
cache write attempts, and check the current exact `selected_ocr_required`
refusal across read, repeated recovery and context. Full SQL, original PDF and
canonical revision remain unchanged. An empty `review_blocked` sheet is not
reported as a successful current sheet. Saved-current data disappearing after
selection is separately covered by
`contract/test_saved_identity_publication.py::test_missing_source_keeps_selected_saved_display_but_refuses_output`.
Unowned diagnostic-draft persistence is explicitly refused rather than treated
as saved operator work. The original four-menu parent-loss case now checks the
formal FK preventing parent deletion and retaining every physical menu row;
it no longer creates an orphan by relying on disabled SQLite constraints.

`test_native_selection_fixture_scope.py` covers blank-mode exclusivity before
registration, including numeric zero and explicitly empty supplied rows, plus
duplicate quantity identities and out-of-period input refusing before queue or
source mutation. These tests have been added but not executed yet.

| Former history responsibility | Current case |
| --- | --- |
| Read 2/1/3 using the declared six quantity columns | `test_get_ocr_sheet_from_selected_owned_evidence`: actual selected result, exact fields and evidence identity, no order-line fallback |
| Full current label JSON export | `test_export_ocr_sheet_label_writes_current_sheet_json`: selected native source and actual temporary JSON output |
| Persisted 6/5 versus unrelated noisy payload 7 | `test_selected_identity_ignores_unowned_payload_menu_noise`: real selected 6/5, original noise/menu/note input, unchanged authoritative cache |
| Numeric 7, blank and loose note 23/unstructured 16 | `test_selected_numeric_cells_ignore_unowned_loose_note_and_unstructured_values`: actual raw 7/blank/note, no promotion of unrelated 16 |
| Zero from formerly order-line/payload paths | `test_get_ocr_sheet_preserves_zero_from_selected_evidence`, `test_selected_zero_is_not_replaced_by_unowned_payload`: actual selected zero, cache preservation |
| Failed projected quantity 30/copy diagnostics 120 versus 7 | `test_selected_source_survives_unowned_failed_projection_diagnostics[cache/job]`: actual selected 7, original line 30 retained, diagnostic failure literals retained, complete SQL/cache non-mutation |

Old source strings such as `weekly_menu+ocr_payload`, automatic failed-job
suppression and legacy trace label `ocr_payload` are not asserted as current
authority. Their quantity/identity/preservation responsibilities are exercised
through the real current source instead, not replaced with generic refusals.

## Cache Publication Isolation

The cache writer previously merged page images, engine/facility/template identity,
debug fragments and partial target/record arrays from the previous cache. Those
fragments did not include the complete owning source receipt. The writer now
requires a complete owned Hakodate result for both synchronous and asynchronous
writes, validates it against the captured source, and never creates a legacy
evidence mirror. Only the existing operator-edit archive survives independently;
provider-supplied edit archives are not adopted.

`test_ocr_cache_result_isolation.py` adds partial synchronous rejection with full
SQL/PDF/operator-work preservation, asynchronous incomplete-output refusal before
the write fence or evidence publication, and an actual successor producer retaining
operator work without inheriting old image/identity/failure diagnostics.
The two historical partial-cache preservation cases now verify rejection against
actual selected artifacts rather than accepting a fabricated fragment bundle.

Row-index ties and conflicting raw 99/88 rows are checked against actual selected
5/6 cells. The April 29 component explicitly authors its positive physical source
as 70/70/58/59/60. Old April 20 or identityless first-pass rows are adversarial
inputs, not a date-realignment success. Unknown 91/92 inputs cannot populate an
explicitly blank selected source. These new cases remain unexecuted until the
known implementation and fixture migration batch is complete.

The page-preview dispatcher required legacy `pages` before its already-existing
Hakodate-only page materialization could run. It now accepts the active owned
Hakodate artifact bundle at that shared decision point. This does not discover
another job's images or treat incomplete output as selectable. The current
native-only preview case checks real source preparation and absence of old
message-job overlay images; runtime verification remains pending.

Preview preparation no longer creates an unowned cache and then mocks facility
configuration to make it readable. Declared four-column grid interpolation is a
direct geometry component test with detection forbidden. Actual selected native
preview reads separately cover deferred expensive detection, no markdown loads,
no legacy overlay exposure and no cache/evidence/workflow writes. The original
structured table containing April 26 / 大豆のトマト煮 / 11 is retained as an exact
table-compaction component, not promoted into an unrelated January order.

The former archived `ORDc935f9e2` fixture remains an explicitly unbound negative:
its raw 23 and legacy DB override cannot resolve a missing week. It is not a
current OCR success. Source-equivalent separate orders remain equal when both
have explicit periods; deleting one period is now checked as `week_unresolved`,
not inferred from quantities or received time. Actual FAC00005 specimen column
indexes are used for the selected 0/0 and 58/2 positives; noisy legacy columns
cannot rewrite that schema or its selected cells.

Raw review candidate tests now feed the exact registered original bytes to only
the external extraction double. Their initial baseline ID is the actual selected
draft, not an invented null ID. The candidate 5-to-7 and invalid-overwrite
responsibilities remain. A corrected URI without a parent receipt is no longer
a success: the former case checks refusal, original/source preservation and the
selected 2. Genuine declared corrected-parent receipt positives remain in
`test_h2_captured_proposal_inputs.py::test_declared_corrected_input_retains_parent_receipt`
and `test_selected_pdf_start_receipt.py`; these are separate source-receipt
components, not proof of that unowned URI's validity.

The old workflow-gate mock and positional-fallback success switches are removed
from the current-sheet authority case. It now compares the real selected sheet's
gate and source before/after an unselected diagnostic draft with a quantity
blocker. That draft cannot block or replace the operator's selected current
surface. Reparse-candidate 9 likewise remains separate from actual selected
4/5 with explicitly authored physical menu rows; no missing menu is silently
reconstructed just to make the positive setup run.

### Selected Operator Drafts Versus Internal Archives

`get_latest_sheet_draft` returns the workflow-selected draft, not the newest
arbitrary diagnostic row. Four former refresh/rebase cases retain their original
17/23/18/blank/ghost/wrong-menu inputs as unselected diagnostic rows and check
exact selected-draft and SQL stability. They do not claim automatic pruning or
adoption. The distinct real operator positive declares March 22 and March 24
physical rows, saves 17 and manual-extra 4 through `workflow.save_sheet`, and
checks repeat reads plus unchanged original OCR evidence.

Internal `save_ocr_sheet_exact` archives are tested as archives; they do not
automatically become workflow selection. Current UI/export positives explicitly
call the formal save command. The cache-isolation fixture deliberately performs
both an internal edit archive and a formal operator save so it can verify both
retention responsibilities. Raw-output reads retain actual recognition records
and do not expose the old `edited_table` surface. Manual 12/11/13, label 12,
header stability and repeated 11-to-3 edits are verified on their intended
archive or selected surface, not a fabricated cache projection.

Invalid-daypart diagnostic cases now prepare the explicit five-row physical menu
before producing the real native 4/5/6/7/8 source. An intentionally deleted menu
is never silently recreated by the native-selection helper.

The former overlay case's numeric 1 at confidence 0.09 is retained through a
real owned successor producer, explicit selection and actual native-page read.
The external recognizer score is an explicit component input; no confidence
threshold or production recognition code is changed to make that case pass.
