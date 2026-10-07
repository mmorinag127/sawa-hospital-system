# H2 terminal generation and output acceptance source audit

Base: d3d39bf3198f3ba631c82c7c8bd396323dcd2484.
Source only; no runtime, tests, network or DB execution.

## Terminal ownership

DONE and FAILED use `_complete_terminal` -> `_persist_terminal_intent`.
Redelivery uses `_replay_terminal` -> the same persistence helper.
Output reads use an exact observed generation; output writes use that generation
(or zero only when absent). Final output verification is generation conditional.
Final Firestore acknowledgement compares the entire original validated
`execution_claim`, in addition to status and terminal receipt. Concurrent output
replacement cannot be overwritten or acknowledged. Claim replacement before
the final reread cannot be acknowledged.

Source cases exercise actual helpers with transport failure injection for both
terminal statuses, replacement before upload, replay denial and claim switch
before acknowledgement. They have not been executed.

## Accepted outputs

`output_builder._workflow_v2_materialization_candidate` is called from its
saved-sheet output projection. It now consumes the immutable bagging candidate,
checks saved-sheet identity, template identity, candidate digest and full
configuration/policy presence. It does not reconstruct from current JSON.
Missing receipt explicitly stops. Zero values and unknown configuration keys
are preserved. Source behavior cases invoke this actual consumer.

Production draft insertion sites: `draft_sheet_service.persist_sheet_draft`
and workflow `save_sheet`. Patch adoption (`ocr_patch_candidate_service`) and
order-service draft producers delegate to the common draft persistence path.
Bagging produces the candidate/configuration/digest artifact; final confirmation
consumes that same candidate under the original synchronous input proof.

## Exact unresolved receipt semantics

### Producer/consumer correction

Initial bagging explicitly passes its original materialization candidate into
the common output producer. No persisted artifact is consulted during that
calculation. The candidate records its calculated `output_lines` before its
artifact digest is generated. Existing-output consumers require that immutable
candidate/digest and consume those output lines without repeating menu,
expanded-copy or quantity-multiplier interpretation against current sources.
Missing artifact template identity now rejects even when the other identity
exists. Bag allocation consumes the same captured facility and quantity policy;
output preparation and rebuild bind lines/config/policy to one accepted read.
Totals use accepted quantity policy rather than the mutable global policy.
Bag-summary consumer propagates explicit receipt failure rather than serving
an old payload after a failed reconstruction.

Full production call-site inventory (`rg build_order_lines_for_outputs`):

- workflow `_build_bag_rows_for_candidate`: explicit producer candidate.
- output `build_bag_rows_for_outputs`: accepted receipt consumer.
- output `_prepare_output_context`: one accepted candidate/config/policy.
- output `rebuild_bags`: one accepted candidate/config/policy.
- totals `build_totals`: accepted lines and quantity policy.
- order `_bag_summary_expected_totals`: accepted consumer.
- order `list_daily_output_override_editor_rows`: accepted consumer.
- order daily bag summary builder (call near original 8131): accepted consumer.
- order bag summary initial lines (original 8871): accepted consumer.
- order bag summary transient reconstruction (original 8886): accepted consumer.

There are ten production invocations plus the common function definition,
not eleven distinct production invocations. All reach the shared receipt gate.
No frontend or terminal-race source changes in this correction.

New source case calls actual `_build_bagging_result_payload` and actual common
enrichment/bag allocation without mocking `_build_bag_rows_for_candidate`, then
consumes its artifact. Neighbors cover changed master, zero values, missing
template, missing policy and wrong digest. All are definitions, not executed
proof. Existing tests with the prior mocked bag helper remain unchanged.
Historical artifacts lacking `output_lines` explicitly stop; no automatic
current-master backfill. Acceptance timing remains bagging-time only. Sheet-save
receipt timing is still unresolved and is not silently changed here.

The saved-sheet model and both insertion sites do not persist a sheet-save-time
configuration/policy acceptance receipt. The existing immutable configuration
receipt is captured at `run_bagging`, not at `save_sheet`. Choosing sheet-save
acceptance versus bagging acceptance changes which configuration is authoritative
when JSON changes between these commands. No new acceptance timing was invented.
Historical saved sheets without an accepted bagging receipt stop rather than
adopt current config. Defining and implementing a sheet-save acceptance receipt
remains unresolved pending this specific semantic decision; this is not a test
execution residual. No full H2 completion is claimed. The 193 legacy test caller
migrations remain deferred.
