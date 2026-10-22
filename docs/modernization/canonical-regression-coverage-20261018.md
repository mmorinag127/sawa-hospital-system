# Canonical saved/projection regression coverage

Base: `1bc65e70f5dbe75a3c95f2e00a78f4892cd87539`, branch
`codex/canonical-regression-coverage-20261018`.
This is offline component coverage, not real recognition accuracy, production
parity, staging acceptance, or the later full-code review. The frozen quantity31,
projection24 and broad29 failures remain unwaived and were not rerun here.

## Original assertions to NEW tests

Original saved tests are in `backend/tests/contract/test_saved_sheet_quantity_schema.py`.
NEW tests are in `backend/tests/unit/test_canonical_saved_projection_regression.py`.
The NEW wrappers invoke the original assertion functions directly, unchanged,
with a NEW `CanonicalHistoryCase` provider. No original source/helper is edited.

| Original function / case | NEW function | Assertions retained |
| --- | --- | --- |
| `test_saved_schema_failure_is_readable_and_blocks_every_materialization_path[missing]` (111) | `test_saved_schema_materialization_paths[missing-quantity_template_field_missing]` | Exact saved rows/types/note8, context/read/API/gates, direct candidate, apply/confirm/bagging refusal, no source/DB adoption |
| Same `[unknown]` | Same `[unknown-quantity_template_field_unknown]` | Same, exact unknown-domain code |
| Same `[duplicate]` | Same `[duplicate-quantity_template_alias_collision]` | Same, exact collision-domain code |
| `test_complete_schema_preserves_typed_operator_values_and_real_candidate['8']` (183) | `test_saved_typed_values_and_candidate[8]` | Exact saved types/note, regular2F8/regular3F2/soft2F2, real captured candidate |
| Same `[0]` | Same `[0]` | Integer zero retained in sheet, quantity0 present in candidate |
| Same `[None]` | Same `[None]` | None retained, only regular3F2/soft2F2 materialize |
| Same `['']` | Same `['']` | Empty string retained, same two other quantities |
| Same `['  ']` | Same `['  ']` | Two spaces retained, same two other quantities |
| `test_unknown_programming_value_error_is_not_converted_to_domain_blocker` (210) | `test_programming_error_is_not_domain_blocker` | Actual ValueError type propagates from injected programming error |
| `test_invalid_schema_revision_cannot_reuse_real_accepted_outputs_or_aggregate` (225) | `test_saved_schema_invalidates_previously_accepted_output` | Actual bagging12, accepted regular10/soft2, invalid revision removes acceptance/aggregate; no old-output fallback |
| `test_invalid_schema_candidate_cannot_be_adopted_even_by_direct_materializer` (277) | `test_invalid_schema_direct_materializer` | Actual candidate/direct materializer rejects exact unknown code |
| `test_context_revalidates_real_source_after_schema_validation` (290) | `test_saved_schema_source_race` | Actual competing SQL revision detected as current_sheet_input_changed, no clobber |

The original programming-error and source-race injections are preserved by calling
their unchanged functions. They are not used to bypass ownership or validation.

Original axis/period tests are in `test_h2_operator_repair_revision.py`:

| Original | NEW | Coverage and explicit difference |
| --- | --- | --- |
| `test_axis_actual_save_and_consumption_original_source[row]` (310) | `test_row_save_consumes_original_source` | Real SQL save/normalize and source/revision/digest invariants, changed PDF SHA and missing geometry proof rejection. Original three coordinates `[0,50,100]` cannot encode seven body days plus two canonical header bands. NEW valid coordinates `[0,10,20,30,40,50,60,70,80,100]` retain those three values and are preserved exactly; this does NOT claim literal equivalence of the original coordinate array. |
| `test_axis_get_put_consume_get_keeps_original_proof[row]` (391) | `test_row_get_save_get_keeps_proof` | Real GET/save/GET source ownership, original receipt preserved, new receipt revision advances. Same explicit coordinate-contract difference. |
| `test_actual_header_row_and_normal_collector_share_partial_period_plan` (943) | `test_partial_period_same_plan_all_consumers` | Calls unchanged original function: `[4,5,6]` day mapping, real header/row review images, identical target count, both collected pages1, malformed day plan rejected. Uses actual alignment with explicit synthetic observed coordinates/calendar; no fabricated successful-detector flag. Source fields are declared before the call and monthly inputs are actual SQL rows. |

## Boundaries

- SQLite FK is enabled and read back1 using a module-scoped test listener for
  each NEW case. All selected DB/HOME/temp/cache/artifact paths belong to this WT.
- `HistoryCase.queue`, canonical facility/version context, actual PDF bytes and
  receipt, workflow delivery claim/capture, rerun persistence, evidence selection,
  real save, schema validation, materialization and source fences are unchanged.
- Only the external manifest/provider boundary and model wrapper are synthetic.
  The model explicitly returns `2`; native serialization, contact generation,
  raw/confidence/image checks, checkpoint persistence and full canonical artifact
  validation are real. Overlay/PDF external-provider files are authored, not
  actual recognition output. They are not cited as live rendering proof.
- Axis image generation is an authored observation. SQL/source validators,
  calendar contracts and setter/normalizer are real, not patched.
- Row setup records the actual current document through the existing
  `_record_current_order_version` registration operation before source capture.
  The frozen repair fixture alone omitted that required explicit-document proof.
- The partial-period fixture's external raster/quad registration remains
  synthetic. Both consumers use actual `_align_axes` with an explicit manual
  header/row contract. The existing template/raw-source validators remain active.
- No raw expansion cases are duplicated from
  `test_native_quantity_projection_contract.py`; no setting invalidation cases
  are duplicated from `test_saved_sheet_setting_invalidation.py`.
- Quantity semantics, source precedence, recognizer behavior and all frozen
  tests/helpers remain unchanged. Row-axis literal-coordinate incompatibility
  remains a separately stated gap, not a waived assertion.

## Execution evidence

Owned evidence root: `tmp/canonical-regression-coverage/`.
Exact command: `run1/command.sh`; stdout/stderr: `run1/tests.log`;
JUnit: `run1/results.xml`. Initial execution and any setup-only follow-up results
are retained under separate run directories. Final counts and source hashes are
recorded below after execution.

### Results and retained failed attempts

| Run | Scope | Result / exit |
| --- | --- | --- |
| run1 | All15 NEW cases | 12 passed,3 failed,0 skipped;78.76s;exit1 |
| run2 | Only row2/partial1 setup repairs | 2 passed,1 failed,12 deselected;3.02s;exit1 |
| run3 | Only row2/partial1 final setup | 3 passed,0 failed,12 deselected;3.18s;exit0 |

Run1's12 successes executed every original saved-schema assertion. These bodies,
the provider and saved-schema fixture are unchanged by the subsequent row/period
setup corrections. There is **not** a single final-source 15-pass batch here;
the parent can independently execute the complete final file.

Run1 row2 stopped at `document_not_found`: the original authored relational setup
had a document but no OrderVersion. NEW setup now invokes the real version
registration operation. Run1 partial stopped at `.convert` because its corrected
external alignment stub returned NumPy instead of the PIL image contract.
Run2 then reached the original `status == ready` assertion and failed because the
old stub supplied no `header_intersection_x_match.used`. Rather than force that
flag, final setup removed the alignment mock for both consumers and uses actual
manual-axis validation. All failed logs/JUnit are retained.

All test DB files are below each run's `pytest/` absolute owned basetemp; the
bootstrap database is `runN/parent.sqlite3`. FK1 is enabled/read back at each
SQLite connect by the NEW autouse fixture; integrity failures are not suppressed.
No source/ownership validation is monkeypatched or disabled. Original tests and
helpers have no diff. No existing failures are waived by these NEW successes.

## Remaining gaps

- Literal original row vectors `[0,50,100]` plus success cannot satisfy the
  current required seven-day/header contract. The two NEW cases prove the same
  ownership/coordinate-preservation properties with valid explicit boundaries,
  not acceptance of invalid original coordinates. No production relaxation or
  business-policy decision was made.
- No new live model/FAX/stg/output-rendering proof; no blanket migration of
  unused old OCR. No expanded-copy mode semantics were invented.
- Frozen31/24/29 failures, overall visual acceptance and the full later review
  remain open. Parent review/integration and independent final-source run remain.

Final patch/hashes and machine-readable run/case counts are under
`tmp/canonical-regression-coverage/{new-test.patch,source-sha256.txt,results-summary.json}`.

## Parent Independent Verification

After the child froze the final test source, the parent ran the complete NEW
module without a name filter:15passed,0failed,0skipped,77.26s,exit0. Log/JUnit:
`tmp/canonical-regression-coverage/parent-all/{tests.log,results.xml}`. The
parent reused the recorded command with only its output/basetemp paths changed
and the child diagnostic name filter removed. All original assertion bodies
remain unchanged. This establishes final-source local component coverage, not
formal CI, stg, real recognition accuracy or waiver of original failed fixtures.

Parent added this NEW module to the existing CI OCR regression list by one line;
no existing test or CI gate was removed. Review/commit/integration remain pending.
