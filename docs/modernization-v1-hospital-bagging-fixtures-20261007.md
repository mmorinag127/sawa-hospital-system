# H2 bagging fixture migration: blocked source investigation

- Branch: `codex/modernization-v1-hospital-bagging-fixtures-20261007`.
- Base/inspected HEAD: `8fcb1e418303b7824dd3cc63a4226f9bf9a811b4`.
- Scope: the first two cases in `backend/tests/integration/test_bagging_delivery_slots.py` only. Neither case nor the other three cases has been edited.
- This is source evidence, not an executed fixture migration or successful bagging/OCR/operator flow.

## Production blocker

Failure class: the workflow saved-sheet template eligibility guard rejects the canonical JSON projection produced by the original-input registration path.

1. `tests/helpers/ingest_original.py:97` registers the original payload; its same-session canonical `resolve`/`materialize` calls assign the selected projection ID to `Order.template_version_id`.
2. `src/services/canonical_facility_lineage.py:80` produces that projection with `status="archived"`; `verify_projection` at line 38 requires exactly that status. Changing the fixture projection to active would violate the canonical source contract.
3. `src/services/order_workflow_v2_service.py:786` `_require_workflow_template_version_from_context` rejects that same ID at line 797 unless status is active, returning `template_version_mismatch`. The actual `save_sheet` producer calls this guard before creating its saved draft.
4. The sibling workflow template guard at line 1521 also requires active. These are production paths, not missing arbitrary fixture dictionaries.

Invariant required by the assignment: the selected original canonical projection must remain authoritative through draft save, bagging receipt production and same-transaction output adoption. A separately invented active template, fixture status rewrite, manually attached receipt or patched guard cannot establish that invariant.

The referenced `test_h2_operator_repair_revision.py:1202` output fixture creates a separate active template and changes workflow/evidence template IDs. Its manually assembled acceptance metadata cannot replace the requested real original registration -> saved draft -> `run_bagging` path here.

No production patch is authorized in this worktree. The two dependent fixture executions therefore remain blocked on resolution of the workflow canonical-projection eligibility conflict, rather than bypassed or reported as passed. This is established by source inspection only; no runtime reproduction is claimed.

## Preserved scenarios and subsequent execution boundary

- Condiment: regular/2F quantity 3 and soft/3F quantity 4; exactly one sauce bag, quantity 7, null diet and area.
- Stale materialized bags: retain duplicate 15/15/9 rows and the expected current regular 2F/3F quantities 9/8 and mixer 2F/3F quantities 2/3. `get_bag_summary` is a transient read when rebuilding is necessary, not a physical write; do not change that production behavior to satisfy the old test name.
- Once the producer blocker is resolved in assigned production scope, use the real `run_bagging` acceptance proof and `_capture_output_adoption` with caller-owned Session for `rebuild_bags`. Do not synthesize either proof.
- Explicit synthetic facility/date/PDF/menu fixtures are component inputs only. The 13 date-policy callers, saved-sheet/bagging capture policy and override policies remain unresolved; this investigation chooses none of them.

## Checks

Read-only `git status --short --branch`, `git rev-parse HEAD`, `rg` and `sed` inspected the named sources and guards. Initial status was clean. No Python/import/tests, SQLite initialization, provider, network, browser, commit, merge, push or deploy ran. Focused case count executed: **0**; passed: **0**. Source changes: this document only.

## Subsequent authorized root repair (same base)

The preceding section is the historical stopped checkpoint, not current status.
Parent subsequently authorized the three pinned template guards in
`order_workflow_v2_service.py`. They now share `_canonical_workflow_template_version`:
Core `read_projection`, strict `verify_projection`, original facility membership,
and existing column validation. The context boundary retains its existing date
applicability check. Missing selected ID no longer resolves a legacy/current active
candidate: it returns `template_version_required`. No date precedence was changed.

New `test_h2_canonical_workflow_template.py` has 21 producer/structured-session
boundary cases (normal, missing row, active status, foreign facility, altered columns,
missing creation proof, altered digest at all three boundaries) and one real
per-case Alembic SQLite materialization/Core-read test proving a cached archived
ORM row cannot conceal physical status tampering. The final root-focused run is
**22 passed**, exit 0, 1.05 seconds. Earlier 21-case runs are retained; the first
run's context binding was replaced with actual explicit context metadata before
the final proof. No always-true context/ownership binding remains.

The first two integration tests now use the narrow synthetic fixture helper.
Every old quantity/result assertion remains; the other three tests are unchanged.
The helper authors matching PDF/JSON/monthly menu, selects/materializes the original
version through actual registration, reads actual workflow revisions for CAS,
confirms an explicit one-day component period, processes the actual delivery with
only the external OCR adapter doubled, selects the owned evidence, and saves the
operator sheet through the real producer. Menu category/diet/date/amount identities
are explicit physical fixture records, not guard overrides. It then invokes actual
`run_bagging`; no acceptance proof is manually assembled.

### Newly executed production blocker

Final integration run: **2 failed, 3 deselected**, exit 1, 2.77 seconds.
Both stop inside `run_bagging` -> `_build_bagging_result_payload` ->
`_canonical_payload_digest`: `json.dumps` rejects the actual candidate's Python
`date` (`2025-01-06` / `2025-02-18`). The mapper's captured physical inputs contain
dates; the real immutable receipt digest does not serialize them. This is a
production receipt serialization failure, not missing fixture quantity/ownership.
No date coercion shim, digest mock, quantity guard bypass or additional production
fix was applied. Bagging acceptance, output adoption and the original bag result
assertions therefore remain **unverified**, not passed. Resolve that additional
shared serialization defect in explicitly assigned scope before completing these
two scenarios. Whole H2/operator/OCR/live acceptance remains unclaimed.

### Commands and artifacts

Working directory for pytest: this worktree's `backend`.
Trusted executable: `/Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python`.
Readonly SDK directory: `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-main/tmp/v1-hospital-task-sdk-20261007`.
No installation or donor writes occurred.

Final root command (exit 0):
```sh
env -i PATH=/usr/bin:/bin HOME="$PWD/../tmp/canonical-workflow-proof/home" TMPDIR="$PWD/../tmp/canonical-workflow-proof/temp" PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. DB_URI="sqlite:///$PWD/../tmp/canonical-workflow-proof/bootstrap-r3.sqlite3" NO_GCE_CHECK=true GOOGLE_CLOUD_DISABLE_GRPC=true /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python -m pytest tests/unit/test_h2_canonical_workflow_template.py -q -p no:cacheprovider --basetemp=../tmp/canonical-workflow-proof/cases-r3 --junitxml=../tmp/canonical-workflow-proof/results-r3.xml > ../tmp/canonical-workflow-proof/pytest-r3.log 2>&1
```

Final integration command (exit 1):
```sh
env -i PATH=/usr/bin:/bin HOME="$PWD/../tmp/canonical-workflow-proof/home" TMPDIR="$PWD/../tmp/canonical-workflow-proof/temp" PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-main/tmp/v1-hospital-task-sdk-20261007:. DB_URI="sqlite:///$PWD/../tmp/canonical-workflow-proof/bootstrap-bags-r9.sqlite3" NO_GCE_CHECK=true GOOGLE_CLOUD_DISABLE_GRPC=true /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python -m pytest tests/integration/test_bagging_delivery_slots.py -k 'condiment_bags_are_merged or get_bag_summary_rebuilds_stale' -q -p no:cacheprovider --basetemp=../tmp/canonical-workflow-proof/bag-cases-r9 --junitxml=../tmp/canonical-workflow-proof/bags-r9.xml > ../tmp/canonical-workflow-proof/bags-r9.log 2>&1
```

Per-case fresh formal SQLite databases and engines are provided/disposed by the
existing `isolated_formal_schema` fixture. All output is under own `tmp`.
Earlier focused iterations bags through bags-r8 are retained (each exit 1):
initial revision 0 fixture error; response-key fixture errors; missing menu identity;
duplicate physical slot; corrected menu identity. None is production success proof.
No unchanged broad test loop ran. No running processes remain. No commit/merge/push/deploy.
The recorded date/capture/override business holds remain unchanged.

SHA256 (all generating HEAD `8fcb1e418303b7824dd3cc63a4226f9bf9a811b4` plus this uncommitted source):
- `tmp/canonical-workflow-proof/results-r3.xml`: `b63b4090c6568ca602aa4b1fcfc770c7b7bbcdd3327b2ed14c842c8eb5a4b3fb`
- `tmp/canonical-workflow-proof/bags-r9.xml`: `1b8c68e744debf19483082f1b54e735dff49569ef2d6251398644d95a2ac6260`
- `backend/src/services/order_workflow_v2_service.py`: `0cf0723a5abf0d225d2cf196381cfffdcbd3f4a69e6d05f47a996d543c20209d`
- `backend/tests/helpers/newnarrowbaggingfixture.py`: `5dd5571509cdbdd8d8a87603827ab4b492ed98896f1341cf7a5834e4d7d91a19` (only unused import removed after the final run).

## Subsequent authorized date serialization repair / current frozen result

The preceding failures and hashes are historical. Parent authorized the actual
candidate JSON root repair in the same production file. `_canonical_payload_json`
serializes exact `date` and `datetime` through ISO format, rejects other unsupported
objects and nonfinite floats, and preserves existing JSON serialization formatting
for already JSON-compatible inputs. No `default=str`, arbitrary stringify, digest
mock or quantity change was introduced. The bagging producer returns the resulting
JSON projection, so artifact storage and its existing digest verifier see the same
representation as the shared materialization digest.

Shared digest caller inventory: bagging production, output bundle production,
final-confirm candidate/proof verification in `order_workflow_v2_service.py`, and
confirmed snapshot, acceptance proof and candidate checks in `output_builder.py`.
The latter consumers were inspected but not changed. Artifact service still uses
its existing strict JSON digest on the now JSON-compatible producer payload.

Tests include reported date, naive datetime and timezone-aware datetime parity,
unchanged original typed input, zero/null/False/raw-string preservation, unknown
object/set/bytes stops, confirmed-context missing ID without selection/materialization,
missing-ID existing gate, unexpected DB error propagation and existing context
date boundary/outside-boundary checks. The two integration cases now additionally
read the real persisted `OrderBaggingResult` and compare its complete payload and
both artifact/materialization digests with the producer result. The original
condiment 7/null/null and regular9/8/mixer2/3 assertions pass unchanged.

The condiment fixture's actual monthly item now references its authored menu master,
so the real producer receives the explicitly authored condiment rather than a
mocked bag type. An intermediate run retained **1 failed, 29 passed, 3 deselected**
until that missing physical master linkage was corrected. Subsequent focused runs
retain 30 passed and 37 passed records. Latest final source run: **37 passed**, exit
0, 3.05 seconds, 29 warnings (reflection and library deprecations retained).
Breakdown: 35 root/neighbor cases and the two requested integration cases.

Final command, cwd own `backend`:
```sh
env -i PATH=/usr/bin:/bin HOME="$PWD/../tmp/canonical-workflow-proof/home" TMPDIR="$PWD/../tmp/canonical-workflow-proof/temp" PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-main/tmp/v1-hospital-task-sdk-20261007:. DB_URI="sqlite:///$PWD/../tmp/canonical-workflow-proof/bootstrap-persisted.sqlite3" NO_GCE_CHECK=true GOOGLE_CLOUD_DISABLE_GRPC=true /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python -m pytest tests/unit/test_h2_canonical_workflow_template.py tests/integration/test_bagging_delivery_slots.py::test_condiment_bags_are_merged_across_diet_and_area tests/integration/test_bagging_delivery_slots.py::test_get_bag_summary_rebuilds_stale_materialized_bags -q -p no:cacheprovider --basetemp=../tmp/canonical-workflow-proof/persisted-cases --junitxml=../tmp/canonical-workflow-proof/persisted.xml > ../tmp/canonical-workflow-proof/persisted.log 2>&1
```

Final SHA256, generating base unchanged plus these uncommitted sources:
- `tmp/canonical-workflow-proof/persisted.xml`: `540d064c347b725830ea2d93a0e5f874a2012b98d357ca2a13f1c54152f950a5`
- `backend/src/services/order_workflow_v2_service.py`: `d14e878666ef211c4b4bc370b140bd580b5618b06ae1277245f8aaf02e76def8`
- `backend/tests/unit/test_h2_canonical_workflow_template.py`: `23476bdaf2bb420f4664c4bd42783e400bae8eb5f77acb28f0e7504f38afe8fb`
- `backend/tests/helpers/newnarrowbaggingfixture.py`: `62c6f1560e37619898a52a2b82b5427848ce7f97ee555a56cc6cdfc345cb7b5a`
- `backend/tests/integration/test_bagging_delivery_slots.py`: `36a9413472ea203bd50f6b61f977d0083dae075a52f49605dd161619f9cd965d`

`git diff --check` exit 0; shell `diff` of the entire suffix beginning at the third
test against base exit 0. Five files frozen for parent review; no commit/main write/
merge/push/deploy or running process. Component verification is not real OCR,
complete final-confirm/operator flow, integrated-SHA acceptance or live proof.
Unresolved date/capture/override policies remain unchanged.

## Final parent-requested serialization assertions (2026-10-08)

Production source unchanged (SHA256 still `d14e878666ef211c4b4bc370b140bd580b5618b06ae1277245f8aaf02e76def8`). Added only three explicit ValueError cases for NaN/+inf/-inf and one exact original hashlib/json.dumps digest parity case with nested Unicode, raw strings, 0, null, False and finite floats. All existing 37 cases/assertions retained.

Focused root plus exactly the two bagging tests: **41 passed**, exit 0, 3.13 seconds, 29 retained warnings. No broad suite or live proof. Same generating base and owned per-case migration setup as above.

Executed command, cwd own `backend`:
```sh
env -i PATH=/usr/bin:/bin HOME="$PWD/../tmp/canonical-workflow-proof/home" TMPDIR="$PWD/../tmp/canonical-workflow-proof/temp" PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-main/tmp/v1-hospital-task-sdk-20261007:. DB_URI="sqlite:///$PWD/../tmp/canonical-workflow-proof/bootstrap-nonfinite.sqlite3" NO_GCE_CHECK=true GOOGLE_CLOUD_DISABLE_GRPC=true /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python -m pytest tests/unit/test_h2_canonical_workflow_template.py tests/integration/test_bagging_delivery_slots.py::test_condiment_bags_are_merged_across_diet_and_area tests/integration/test_bagging_delivery_slots.py::test_get_bag_summary_rebuilds_stale_materialized_bags -q -p no:cacheprovider --basetemp=../tmp/canonical-workflow-proof/nonfinite-cases --junitxml=../tmp/canonical-workflow-proof/nonfinite.xml > ../tmp/canonical-workflow-proof/nonfinite.log 2>&1
```

SHA256:
- `tmp/canonical-workflow-proof/nonfinite.xml`: `3d5d4da409016c1418105f12b00e2d22725e7c14f0a73bc336262b80c812a49b`
- `tmp/canonical-workflow-proof/nonfinite.log`: `7847799fe495da468a9fc7c5c93c3ddd667a864ea7c534b8003c61b1aaecc1e7`
- `backend/tests/unit/test_h2_canonical_workflow_template.py`: `0a28303543374b35819e44f9a27196a1004e8ded5f2ab84aa837cec84a06f364`

Prior failure records and 37-case proof remain historical evidence. Five-file staged unit refrozen, no commit/merge/push/deploy/main writes. Parent integrated-SHA verification remains required.
