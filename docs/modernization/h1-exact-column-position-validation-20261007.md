# Exact authored column positions: source-only correction

Worktree hospital-h1-lineage-callers, branch
codex/modernization-h1-lineage-callers-20261007, base HEAD
79932bb37ec4c081df61fdfc0e2a323638612a7d.
Parent f2f05 facility_template_version_service was read-only.

Failure class: int(raw) accepted bools, numeric strings and truncated fractional
source positions. The shared parser now accepts only type int, nonnegative,
including zero. No authored values are normalized or replaced.

index is the mandatory authored column position; source_index is the separate
source position. Existing role-based source_index requirements are unchanged.
Optional roles are not assigned a source_index. Invalid explicit non-null
source_index values reject even on optional roles. Optional null remains null;
required-role null/missing stops. Shared column validation rejects malformed
containers and duplicate positions. Config validator also rejects bool and
negative index without making source_index mandatory for unrelated schemas.

## Actual Paths Read

- facilities create/update version -> facility_service.save_facility_version ->
  facility_master_service.mutate_master -> _validated ->
  canonical_facility_store.validate_facility_versions -> facility_versions ->
  validate_authored_snapshot -> validate_template_columns.
- update_snapshot -> _encode validates versions before replacement;
  local/GCS read_snapshot -> _decode rejects duplicate JSON keys/nonfinite
  literals. Dated resolution separately validates the complete authored version.
- facilities resolved -> config_service.get_facility_config_for_date ->
  facility_master_service.resolve_facility_for_date -> resolve_facility_version.
- facility_template_version_service.resolve_facility_config_for_date ->
  canonical_facility_lineage.resolve uses that same dated JSON selection.
- normalize_template_columns and four order_workflow_v2_service validator
  references were inventoried, not adopted or refactored.
- Authored facility_master.template.json base columns and fax columns were read:
  index and source_index are distinct; base columns omit source_index.
  config_validator._validate_columns, template field schema auxiliary position
  helper, master workbook schema serialization and menu ordering references were
  inspected without changing their source selection.

## Defined Cases And Remaining

Cases cover exact zero, bool, negatives, fractional/integral floats, NaN/Inf,
strings, malformed values/containers, required missing/null, duplicate index
and source_index, optional source-index absence, unchanged raw unknown/null/
zero/bool fields, JSON encode/decode -> dated config, malformed literal JSON,
and actual dated save rejecting before replacing original JSON.
No validator is replaced in these definitions; only the external snapshot read
is supplied where needed. Date literals are test inputs, not effective-date
guesses or backfill.

No runtime, tests, build, network or DB operation was performed.
Diff hygiene only is verified. The other 13 effective-date caller adoptions,
legacy migrations, DB projection execution and runtime verification remain
outside this bounded correction. No overall modernization completion claim.
