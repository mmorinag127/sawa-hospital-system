# Diet fixture reconnection checkpoint

Latest checkpoint: authorized derived-accessory root packet, 132 focused tests
passed, exit 0; three original business-contract holds remain unchanged. The
following earlier incomplete checkpoint and failures are historical, not current
root-test failures. Parent review/integration and fresh-SHA proof are pending.

Base: `6f172fb82b8c901f141f7ec1ca7c459177cf4a25`.
Branch: `codex/modernization-v1-hospital-diet-fixtures-20261008`.
The initial fixture-only checkpoint below had no production changes. The current
packet includes the subsequently authorized `output_builder.py` root changes;
it remains pending parent review/integration, not whole acceptance.
All original assertions in the seven assigned tests remain verbatim. The six
unassigned legacy cases remain untouched; their prior failures are not resolved.

## Source changes

- Dated garnish/category component supplies explicit mixer/soft entry diets and
  no longer calls the removed `_apply_builtin_menu_defaults`.
- Two output-diet cases author canonical FAX column configuration and use actual
  registration, delivery, evidence selection, save and bagging receipts.
- Bag quantity case uses two independently migrated owned SQLite databases for
  the original June 12/June 10 dates, initial 100g receipts, actual monthly item
  updates to 2/count and new bagging receipts. Original 10/8 and 2 assertions stay.
- Narrow helper accepts explicit quantity/unit and configured output-diet values.
  PDF/JSON inputs are synthetic selected-date components; external recognition is
  doubled. This is not customer OCR, operator, integration or live proof.

## Results And History

Paths below are relative to this worktree's absolute root:
`/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-v1-diet-fixtures`.

- Initial selected three: 2 passed, 1 failed (removed helper invocation), exit 1.
  `tmp/diet-proof/results.xml` SHA256
  `d4f2d06abe78cd71d2b670017a78c18064167fd901644cdf5481f12cad32e3f2`.
- Selected seven plus complete source-capture, receipt and physical-neighbor
  files: 117 passed, 4 failed, 34 deselected, exit 1. This includes three passing
  assigned cases and 114 passing neighbors; NOT seven passing fixtures.
  `tmp/diet-final/results.xml` SHA256
  `44fab60fc436d9e7dac36fa29ef69e2eb9e3d30ddebae1e4dccefe0e78d850d7`.
  Bag failure at this checkpoint was duplicate monthly seed creation.
- Narrow bag retry after separate database authoring: 1 failed, exit 1 because
  explicit migration target environment still referenced the fixture database.
  `tmp/diet-bag-repair/results.xml` SHA256
  `d9e05e9e9aaaefa290d1e4cfe8bf36efafc6fa48d32e457adb227ecb30aa8364`.
- Final bag-only retry with matching owned migration target: 1 failed, exit 1.
  Actual tofu label amount is **20個**, original assertion remains **10個**.
  `tmp/diet-bag-final/results.xml` SHA256
  `f1f23ecf0a6b1e39d4237e14f86cc879816cd3b9456286e3e2d00860be45ddbd`.
  Log SHA256 `81bae9d3cb24c1fa2e2abff6801a5c671044a27f71645a15797d4e9ab12512b7`.
  This actual-pipeline discrepancy needs investigation before claiming a pure
  fixture migration; the test has not been weakened or given a quantity shim.

## Remaining Contract Conflicts

- `keeps_regular_and_soft_mixer`: same dated physical slot has different normal
  and soft/mixer menu names. Existing physical common-name consumers explicitly
  refuse this (`test_h2_geometry_physical_identity.py:72,74`). Inventing separate
  diet physical slots would violate this task. Original test remains unchanged.
- `blocks_unresolved_source_row`: the second original assertion requires a
  source-row error for date=None and index=0. `_resolve_monthly_entry_by_source_row`
  selects by index/diet without checking line date; `_apply_menu_entry_overrides`
  does not add that check. Reconnecting to a producer cannot honestly guarantee
  that specific error without deciding the missing-date boundary. Unchanged.
- `regular_row_applies_to_soft_mixer_only`: the old fixture authors only regular
  entries and no dated soft item, whereas actual serialization requires exact
  dated diet identity (`order_serialization_service.py:101-108`). Adding a soft
  variant would change the regular-only premise; bypassing it is forbidden.
  Unchanged pending reconciliation, not proof of a new production regression.

## Commands

Run from `backend` with read-only Python
`/Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python` and SDK
`/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-main/tmp/v1-hospital-task-sdk-20261007`.
Each invocation uses `env -i PATH=/usr/bin:/bin`, owned HOME/TMPDIR/DB_URI,
`PYTHONDONTWRITEBYTECODE=1`, `PYTHONPATH=<SDK>:.`, `NO_GCE_CHECK=true`,
`GOOGLE_CLOUD_DISABLE_GRPC=true`, `-p no:cacheprovider`, fresh `--basetemp`,
`--junitxml` and redirected log under the matching `tmp/diet-*` directory.

```text
python -m pytest tests/unit/test_output_builder_diet_overrides.py -k 'output_diet_type_override_maps or output_diet_type_is_unchanged or monthly_menu_item_qty_survives' -q
python -m pytest tests/unit/test_output_builder_diet_overrides.py tests/unit/test_h2_output_source_capture.py tests/unit/test_h2_output_acceptance_receipt.py tests/unit/test_h2_geometry_physical_identity.py -k 'monthly_menu_item_qty_survives_entry_category_mismatch_and_garnish_alias or bag_level_monthly_menu_item_qty_overrides_stale_materialized_amounts or output_diet_type_override_maps_diabetes_to_regular or output_diet_type_is_unchanged_without_facility_override or monthly_entry_override_keeps_regular_and_soft_mixer_menu_items or monthly_entry_override_blocks_unresolved_source_row or monthly_entry_override_regular_row_applies_to_soft_mixer_only or test_h2_output_source_capture or test_h2_output_acceptance_receipt or test_h2_geometry_physical_identity' -q
python -m pytest tests/unit/test_output_builder_diet_overrides.py::test_bag_level_monthly_menu_item_qty_overrides_stale_materialized_amounts -q
```

Final test source SHA256:
`3d1a99c558e2d895ec2b02cb94aa83facbef8fa2fbbd5580efe51c6dea532b7a`.
Helper SHA256:
`93a64c3b3d7d61a29ebf8e7a5af6238e64eb6f57b1521e27556675a0a26e6f83`.
Results are tied to base plus uncommitted source, not a parent-integrated SHA.
No main/develop/release writes, commit, push or deploy. Existing business holds
and remaining modernization acceptance are unchanged.

## Authorized Derived Accessory Root Continuation

Failure class: repeated physical monthly enrichment reclassified a derived
accessory as its parent, producing parent servings 5+5 before multiplication.
Shared boundary: snapshot/monthly-entry/monthly-item enrichment and final own
accessory amount validation in `output_builder.py`.

Derived kind and parent relationship identify children independently of mutable
category; inconsistent identity stops. Monthly processing still validates the
physical parent date/daypart/diet/source row and garnish name, but never copies
parent name/category/amount to the child. Saved order snapshot does not supply
derived component values. Actual own monthly/master/rule sources resolve before
final validation. Missing amount/unit stops before durable bagging receipt;
implicit 30g/unit substitution is removed. MenuMaster normalized-name uniqueness
is the existing database contract. No bag deduplication or quantity division.

Synthetic fixture explicitly authors sauce 12g, not a customer food fact. The
changed premise (old plain parent versus new parent with embedded garnish) is
retained. Original 10/8/2 assertions are unchanged and pass; sauce is separately
60g/12g. Actual receipt component tests cover plain parent, garnish and explicit
condiment; each component bag has one source reference. Missing-source and
invalid identity/quantity/unit tests stop. Snapshot sibling test stores old own
child 99/count, updates actual master from 12g to 14g and proves new child 70g,
parent 10個, and byte/value-identical retained old receipt/digest with child 12g.
No export recapture or mutation of old accepted receipts.

History retained: `tmp/accessory-proof` 124 passed/3 failed (fixture master names
were not normalized by actual producer); `tmp/accessory-final` 126 passed/1 failed
(new sauce assertion expected numeric 60 instead of existing formatter 60g);
`tmp/accessory-frozen` and `tmp/accessory-freeze-final` each 127 passed. These are
not substituted for the final source proof.

Final command: same trusted `env -i` command documented above, owned
HOME/TMPDIR/DB_URI under `tmp/accessory-final-packet`, five files including
`tests/unit/test_h2_derived_accessory_identity.py`, and the previous four passing
assigned case names plus all source/receipt/physical/new-root cases in `-k`.
Exact selector excludes the three unchanged held tests and other legacy tests;
it is not a whole-suite success claim. Final result: **128 passed, 37 deselected,
155 warnings, exit 0**, 19.20s. Full command is the same previous packet command
with the new test file and `or test_h2_derived_accessory_identity` selector, using
`--basetemp=../tmp/accessory-final-packet/cases`,
`--junitxml=../tmp/accessory-final-packet/results.xml`, log redirection to
`../tmp/accessory-final-packet/pytest.log`.

Historical 128-test checkpoint SHA256 (superseded by ranking review below):
- XML: `7d5ab70f8b48b68fcf5700873ddca160922510e1c7eb2edf2c335f7d7859e586`
- Log: `9925e36880ec83b4daacd49c9df22d7acc407fb093ca6362d372c562656f8d3e`
- Production source: `0228d933f3347b0744eb0ce3ea32b59266b980eeeaa8b8f1874c15993cca8c00`
- New root tests: `b509808595201826ccf4cac3b3067e7400264f44c66270696db5dbe881ab240f`
- Assigned tests: `82f21faeda8fa91777ef24adb741b7686e6d1628d78e7f09faf0838008c13a70`
- Helper: `3478d1929b8d6d1d94676ec147ce410c62be97bf0b2613cb3fa8f71d2ebee724`

Manual production change: one file, 40 added/12 removed lines relative to base.
Five source/doc files are frozen for review. Original failed DBs/receipts remain
under `tmp/diet-bag-final`; no commit/main/deploy writes, no whole-modernization
or actual OCR/operator acceptance claim.

## Final Equal-Rank Source Review

Monitor NOT OK: monthly selection previously ranked `(score, specificity, ID)`.
The shared matcher now ranks only the unchanged score and specificity. Distinct
best-ranked payloads raise `monthly_menu_item_source_ambiguous`; identical copies
of one payload are not distinct sources. Differing ranks keep existing priority.
No ID tie breaker, quantity guess or accepted-receipt mutation.

Added tests cover both input orders of distinct equal-ranked quantities, unique
source, lower-ranked candidate and repeated identical payload. A clearly labeled
ambiguous-source read double drives the actual owned bagging boundary: no new
receipt persists and old payload/digest remain identical. This fault injection
is not actual duplicate-master authoring or sole positive adoption proof; existing
real registration/receipt/current-master-update components still run unchanged.

Final command, from this worktree's `backend` (directories created with
`mkdir -p tmp/accessory-rank-final/home tmp/accessory-rank-final/temp` at root):

```sh
env -i PATH=/usr/bin:/bin HOME="$PWD/../tmp/accessory-rank-final/home" TMPDIR="$PWD/../tmp/accessory-rank-final/temp" PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-main/tmp/v1-hospital-task-sdk-20261007:. DB_URI="sqlite:///$PWD/../tmp/accessory-rank-final/bootstrap.sqlite3" NO_GCE_CHECK=true GOOGLE_CLOUD_DISABLE_GRPC=true /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python -m pytest tests/unit/test_output_builder_diet_overrides.py tests/unit/test_h2_derived_accessory_identity.py tests/unit/test_h2_output_source_capture.py tests/unit/test_h2_output_acceptance_receipt.py tests/unit/test_h2_geometry_physical_identity.py -k 'monthly_menu_item_qty_survives_entry_category_mismatch_and_garnish_alias or bag_level_monthly_menu_item_qty_overrides_stale_materialized_amounts or output_diet_type_override_maps_diabetes_to_regular or output_diet_type_is_unchanged_without_facility_override or test_h2_derived_accessory_identity or test_h2_output_source_capture or test_h2_output_acceptance_receipt or test_h2_geometry_physical_identity' -q -p no:cacheprovider --basetemp=../tmp/accessory-rank-final/cases --junitxml=../tmp/accessory-rank-final/results.xml > ../tmp/accessory-rank-final/pytest.log 2>&1
```

Result: **132 passed, 37 deselected, 161 warnings, exit 0**, 19.55s.
The three held business-contract tests remain unchanged and excluded, not green.
All earlier failed artifacts and the 128-test proof remain historical evidence.

Current final SHA256:
- XML: `d526192952a65a3052b1007b339d7c063e89f65bad197c9e7d1c4037d20cc35e`
- Log: `485b7787d8580aeb965a4d059f2592a3de74887258270146788bb654437eef47`
- Production: `c993e6c314040e113e9e9bb440e05c6cdf410835216f08d0aed6783b93ba4a24`
- Root tests: `45a0ccbafbd5f2497b67c950b7d2e3b18150c9c3e3aa8b2d95f3e4883e0b5bbf`
- Assigned test/helper hashes remain the values listed at the 128-test checkpoint.

Current manual production diff: one file, 51 added/17 removed lines versus base.
Five-file packet frozen; no commit, main writes, deploy or whole acceptance.
