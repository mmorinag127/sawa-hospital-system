# Daily Bundle Empty Accounting

- Worktree: `hospital-output-empty-accounting`
- Branch: `codex/modernization-output-empty-accounting-20261006`
- Base: `9497db8f17345b71bfe68bc8eafb0929929dd356`
- Failure class: `VALID_EMPTY_OUTPUT_MISCLASSIFIED_AS_ERROR`

## Invariant

A successfully resolved order with no rows for the requested output date is `empty`, not `error`. A canonical saved-sheet/materialization failure remains `error`. The daily-bundle manifest and HTTP headers expose `success_orders`, `empty_orders`, and `error_orders` from the same item statuses.

## Preserved Behavior

- Output rows, quantities, files, formats, and `total_orders` aggregation are unchanged.
- An all-empty requested bundle still raises `ValueError("対象日の出力対象がありません")` and the API continues to return HTTP 400 for that error.
- No legacy reference delivery route, fallback, or locked workbook behavior is enabled or changed.

## Evidence

The isolated Python was `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0/backend/.venv/bin/python`. Each run used unique worktree-local `HOME`, `TMPDIR`, `XDG_CACHE_HOME`, and pytest output directories.

- Focused command: `python -m pytest backend/tests/integration/test_daily_output_bundle.py::test_build_daily_output_bundle_empty_orders_are_not_errors backend/tests/integration/test_daily_output_bundle.py::test_daily_output_bundle_keeps_ok_empty_and_canonical_errors_separate backend/tests/integration/test_daily_output_bundle.py::test_daily_output_bundle_all_empty_keeps_existing_no_output_block backend/tests/contract/test_daily_bundle_empty_accounting_api.py`.
  Result: 13 passed. XML: `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-output-empty-accounting/tmp/empty-accounting.bfRWdl/results.xml`; SHA256 `d7de8153b795e4ac56d917df15451ba79196fbfafcaf2ca4d04d3eae65f021cb`.
- Whole related command: `python -m pytest backend/tests/integration/test_daily_output_bundle.py backend/tests/contract/test_daily_bundle_empty_accounting_api.py`.
  Result: 78 collected, 71 passed, 7 failed. XML: `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-output-empty-accounting/tmp/empty-accounting-whole.sCS9AF/results.xml`; SHA256 `0af2074a0a3fd420fd8524b21e0e6c54765187efa056d952b35bff8618d72c50`.
- The seven retained failures are `test_write_delivery_note_blocks_when_template_uri_missing`, `test_build_outputs_download_path_does_not_write_canonical_rows`, `test_weekly_weight_collect_rows_counts_diabetes_as_regular_and_excludes_forbidden`, and four inactive reference-daily-delivery workbook nodes. The former empty-accounting node passed; no test was removed.

## Integrated And Staging Evidence

- Source `290caac4247b85f6ef62678c9fc30075a78fea8a` integrated the empty-accounting correction. Parent result: 87 tests, 80 passed, 7 existing failures. XML: `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-main/tmp/c0-runtime-schema/parent-empty-integrated-290caac/results.xml`; SHA256 `2bbcf7950d06378480cd27719f133cfe5e93ede063fd6016a3d634918661986c`.
- STG run `37338269442` succeeded for that source. Browser primary/bags/totals completed, six permitted API GETs returned HTTP 200, and parent reviewed full, bags, and totals PNGs. The saved-sheet target eight-menu total `206` equals daily bags `206` and totals `order_refs` `206`; all manifest hashes passed. Artifact: `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-output-source/tmp/stg-290caac-output-37338269442`; `result.json` SHA256 `f5347fd536c9b257a74703eaa2df869724000b33baa084e31d33cdab76dc04c8`.
- The verifier does not call the daily-bundle endpoint. Consequently, the live `empty_orders` response header is not proven by this STG run; only the local 13-pass regression set covers empty accounting across four formats and headers. The STG evidence proves root source/day-table quantities only.

This worker candidate is integrated and STG-verified as stated above; this is not a claim of production deployment, human approval, or whole-program completion.
