# Removed OCR argument test contract

Base: `23b33fb419dd7d9efab3b3cda53fabf6bf166440`.
Branch: `codex/modernization-hospital-removed-ocr-flags-20261008`.

Only `test_legacy_ocr_output_fallback_options_are_disabled` is changed in the
integration module. Product source was inspected before editing: `get_ocr_output`
at line 21012 accepts `order_id`, keyword-only `persist_cache=False`,
`include_legacy_edits=True`, and `allow_job_reconcile=False`. It has no
`allow_legacy_fallback` parameter or variadic keyword parameter. Its first branch
explicitly raises `LegacyOcrEvidenceFallbackDisabledError` for reconciliation.

The first original oracle expected `LegacyOcrEvidenceFallbackDisabledError` for
`allow_legacy_fallback=True`. It now expects `TypeError` with the anchored exact
message `get_ocr_output() got an unexpected keyword argument 'allow_legacy_fallback'`.
Added standard-library signature assertions require that argument to be absent
and forbid `VAR_KEYWORD`, preventing silent keyword acceptance. This is a changed
oracle, not a claim that all original assertions remain identical.

The second original call `allow_job_reconcile=True` and its exact existing
`LegacyOcrEvidenceFallbackDisabledError` assertion are unchanged. Both call
expressions, registration fixture, cache quantity `6`, evidence-absence assertion,
and all other cases remain unchanged. Neither compatibility keywords nor
product fallback behavior are introduced.

Parent-reported original repeat batch remains 152 cases: 142 passed, 10 failed.
Five of the six migrated negative cases passed; this case stopped at the removed
keyword TypeError. That failed evidence is retained, not replaced by this source
edit. Two preceding parent plans were discarded without edits: one used the wrong
second argument name, and one incorrectly assumed reconciliation was removed.

Only this document and the one-case integration definition are written. No tests,
builds, runtime imports, DB, network, browser, stage, commit, push, or deployment
were performed. Product/helper/date/quantity sources are untouched. Parent review
and integration with the independent unused-entry packet precede one consolidated
six-module and source-parity verification. No completed-test, human OCR, global
fallback-absence, or whole-modernization claim is made. Frozen file and tracked
diff hashes are reported externally; the untracked document is not in Git diff.
