# Monthly source date boundary

Base `00f693c951c52f48d7792dcf10fa8a24af956910`, branch
`codex/modernization-v1-hospital-date-boundary-20261008`.

Failure class: index-only monthly-source identity resolution. Shared resolver
`output_builder._resolve_monthly_entry_by_source_row` previously checked index,
diet and uniqueness without checking line date. The selected canonical row has
an explicit date; an indexed line must have a parsed valid date equal to it.
Added three production lines: absent/invalid/mismatched date raises existing
`monthly_entry_source_row_unresolved`. Existing parsing, diet selection, name,
quantity, no-index name lookup and accepted-receipt early return unchanged.
No date inference from week/daypart, effectiveDate policy or 13caller changes.

Only original `test_monthly_entry_override_blocks_unresolved_source_row` was
reconnected via explicit producer_candidate. Both original error assertions,
date values, quantities, indexes and diets remain unchanged. Its existing source
read doubles make this an explicit malformed producer component, not an actual
accepted receipt/customer OCR flow. No ownership guard is disabled.

New cases: aligned string/date identities, absent/invalid/wrong date, out-of-range,
negative and noninteger indexes, wrong diet, no-index boundary. Actual owned
registration/evidence/save/bagging produces a valid old receipt; a labeled
source-read fault then changes source date after valid producer input. Shared
enrichment stops with `bagging_output_enrichment_failed`, before any new receipt,
and retained payload/digest and accepted output remain identical. Recognition
is doubled against synthetic matching facility/date sources, not live OCR proof.

Distinct earlier guard: `order_serialization_service.candidate_menu_amount_resolution`
already rejects date=None before normal bagging enrichment; `run_bagging` returns
`menu_amount_unresolved`. This source inspection does NOT claim a missing-date
corrupted receipt was observed. The new owned fault case instead has valid line
date and a mismatched captured physical row, reaching the shared resolver.

## Historical Date-Only Proof

From worktree root: `mkdir -p tmp/date-proof/home tmp/date-proof/temp`.
From its `backend`:

```sh
env -i PATH=/usr/bin:/bin HOME="$PWD/../tmp/date-proof/home" TMPDIR="$PWD/../tmp/date-proof/temp" PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-main/tmp/v1-hospital-task-sdk-20261007:. DB_URI="sqlite:///$PWD/../tmp/date-proof/bootstrap.sqlite3" NO_GCE_CHECK=true GOOGLE_CLOUD_DISABLE_GRPC=true /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python -m pytest tests/unit/test_h2_monthly_source_date_boundary.py tests/unit/test_output_builder_diet_overrides.py::test_monthly_entry_override_blocks_unresolved_source_row tests/unit/test_h2_derived_accessory_identity.py tests/unit/test_h2_output_source_capture.py tests/unit/test_h2_output_acceptance_receipt.py -q -p no:cacheprovider --basetemp=../tmp/date-proof/cases --junitxml=../tmp/date-proof/results.xml > ../tmp/date-proof/pytest.log 2>&1
```

**119 passed, 149 warnings, exit 0**, 17.08 seconds. Own formal migrated SQLite,
HOME/temp/output; donor Python/SDK read-only. Other held/legacy tests unchanged,
not claimed green. Four-file source packet, no other production changes.

Paths relative to absolute worktree root
`/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-v1-date-boundary`:
- XML `tmp/date-proof/results.xml`: SHA256 `5eb7b0812a0a167afa575b3b5da4bd9c0a64189f6a217154c82c3a7ba9ce47e8`
- Log `tmp/date-proof/pytest.log`: SHA256 `b0222f9ec2f103bf18bd8464f5b8f4130ce4e488e362507ef537d73ae07899a1`
- Production `backend/src/services/output_builder.py`: SHA256 `ecf920854595255104b3242c424a8e655e0c96f26adbcc586c6c5e59f7551b2f`
- New test `backend/tests/unit/test_h2_monthly_source_date_boundary.py`: SHA256 `2bb6f10311cc7cd664480dabccfebeded2174dac6ac96fbf44e5e05f6955aa54`
- Reconnected test `backend/tests/unit/test_output_builder_diet_overrides.py`: SHA256 `6d59e1ad7b124df443425621834d5880238308d8d630e62eaa3de26223cbf18e`

Proof lineage is base plus frozen uncommitted diff, not parent-integrated SHA.
Parent review/integration/fresh-SHA regressions pending. No commit/main writes,
deploy, business policy redefinition or whole-fallback/modernization claim.

## Final Date And Daypart Proof

The physical source inventory requires normalized daypart in 朝/昼/夕.
Ordinary indexed resolution previously did not compare it; the derived branch
already did. The shared resolver now requires a supported normalized line
daypart equal to the selected row, using the same existing error. Existing
朝食/昼食/夕食 normalization is retained; no meal inference is introduced.
The earlier candidate amount guard requires a nonempty daypart but does not
prove this physical-row equality. No-index and frozen accepted-receipt paths
remain unchanged. Both original error assertions and inputs remain intact.

Aligned 昼/昼食 aliases, missing/empty/invalid/wrong dayparts and a labeled
source-read daypart fault after valid owned producer input are covered. The
fault stops before a new receipt; old payload/digest/output remain unchanged.

From root: `mkdir -p tmp/date-meal-final/home tmp/date-meal-final/temp`.
From `backend`, final command:

```sh
env -i PATH=/usr/bin:/bin HOME="$PWD/../tmp/date-meal-final/home" TMPDIR="$PWD/../tmp/date-meal-final/temp" PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-main/tmp/v1-hospital-task-sdk-20261007:. DB_URI="sqlite:///$PWD/../tmp/date-meal-final/bootstrap.sqlite3" NO_GCE_CHECK=true GOOGLE_CLOUD_DISABLE_GRPC=true /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python -m pytest tests/unit/test_h2_monthly_source_date_boundary.py tests/unit/test_output_builder_diet_overrides.py::test_monthly_entry_override_blocks_unresolved_source_row tests/unit/test_h2_derived_accessory_identity.py tests/unit/test_h2_output_source_capture.py tests/unit/test_h2_output_acceptance_receipt.py -q -p no:cacheprovider --basetemp=../tmp/date-meal-final/cases --junitxml=../tmp/date-meal-final/results.xml > ../tmp/date-meal-final/pytest.log 2>&1
```

**127 passed, 155 warnings, exit 0**, 17.88 seconds. Prior 119 proof and source
hashes above are historical, not verification of the final source.
Final paths use the same absolute worktree root above:
- XML `tmp/date-meal-final/results.xml`: SHA256 `a20c6df807e2f9f4de780cf0a323bb9d0b2018d673427a6f0dfd6e6a653e060f`
- Log `tmp/date-meal-final/pytest.log`: SHA256 `001afaa2051b126de5062db34ae0b32e62a145e7a663b389afc2593f7b61636a`
- Production `backend/src/services/output_builder.py`: SHA256 `29606d64cbecd38b949c02a38e5bc01ba29b4e9e10300c2640c44bf9dea2499a`
- New test `backend/tests/unit/test_h2_monthly_source_date_boundary.py`: SHA256 `d7bc683d82592be4d8446fd93fe3a5221f7336b7debe339390c7ef28150503b9`

Final proof is base plus this four-file uncommitted packet. Parent integrated
verification remains pending; synthetic provider and source-read fault doubles
are component evidence, not customer/live OCR proof. All business date-policy
holds and unrelated legacy tests remain untouched.
