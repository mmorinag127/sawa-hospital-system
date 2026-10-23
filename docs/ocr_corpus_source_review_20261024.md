# Archived OCR Corpus Source Review

## Verified Mismatch

`ORDbabf3c73.pdf` was rendered and inspected. Its single physical page names
グループホームそよかぜ (repository FAC00009) and contains February 15–21,
2026. The fax header says P.001/002, but `pdfinfo` confirms the stored PDF has
only one page. Its existing manual-label/base-sheet JSON contains 64 rows for
February 1–8 with different menus. The old LLM output lists February 15–21.

`source_alignment_review.json` records exact original-file hashes and the
unresolved mismatch. The original PDF and label JSON files are unchanged.
The manifest no longer labels this pair trusted. This is not a repaired dataset,
a passed recognition case or a successful production-parity comparison.
A verified matching original/label is still required for such proof.

## Retained Current Responsibilities

`test_archived_pdf_and_labels_remain_unchanged_and_the_period_mismatch_is_explicit`
checks the actual hashes, label dates, body-period review and unchanged baseline.
`test_manual_label_discovery_never_substitutes_a_different_pdf` retains exact-name
discovery and explicitly records that the only discovered pair is mismatched.

The separate `test_authored_operator_sheet_preserves_all_64_rows_quantities_and_export_identity`
uses the unchanged JSON as explicit operator input, not recognition expectations.
It declares an AUTHORED-OPERATOR-LABEL facility and February 1–8 period, generates
a synthetic component PDF, acquires a real queue/claim and terminal result,
selects that result, and formally reviews/saves all 64 rows. The external
recognizer double returns zero, not copied label quantities. Every original
field/header/row/row-ID is compared exactly after save, reload and actual JSON
export. Exports are confined to the test temporary directory, not the repository
fixture directory. Original selected OCR output must remain unchanged.

This storage test is not a substitute for the invalid real PDF/label pair or
for a normal operator upload/recognition/live-order scenario.

## Retired Algorithm Nodes

The user limited migration to current production Hakodate on 2026-10-13.
The following retired Yomitoku/LLM row-index-copy compatibility assertions are
not implemented, skipped as success, or counted as current recognition coverage:

| Former node | Disposition |
| --- | --- |
| `test_ocr_sheet_regression_corpus_yomitoku` | Old cached Yomitoku-to-weekly-menu projection; excluded provider compatibility. Original archive files retained. |
| `test_ocr_sheet_regression_corpus_yomitoku_trusted_smoke` | Same old projection, not current Hakodate. Its archived PDF was inspected, but no new recognition result is claimed. |
| `test_ocr_sheet_regression_corpus_llm_row_index_copy` | Forbidden old row-position copying; excluded provider compatibility, original rows retained. |
| `test_ocr_sheet_regression_corpus_llm_row_index_copy_trusted_smoke` | Same old copying plus the verified mismatched PDF/label pair; never promoted to a current positive. |
| `test_ocr_sheet_regression_corpus_manual_labels` / `...manual_labels_trusted_smoke` | Actual saved/export quantity/identity responsibility moved to the explicit operator-input component above; real recognition proof remains unresolved. |
| `test_corpus_manifest_defaults_to_trusted_cases_only` | Replaced by `test_corpus_manifest_does_not_trust_the_known_mismatched_pair`; default trusted policy remains but inaccurate pair status is corrected. |
| `test_manual_label_case_discovery_is_safe_when_fixture_files_are_missing` | Exact original-name discovery retained without substitution, now explicitly identifies the mismatched pair. |

Current Hakodate producer/source/quantity guards remain in the owned-workflow,
H2 repair and editable-quantity suites. They do not certify this archive's labels.
All new test implementations are pending the consolidated local verification.
