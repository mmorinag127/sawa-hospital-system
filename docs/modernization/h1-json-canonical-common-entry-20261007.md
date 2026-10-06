# H1 Canonical JSON Common Entry Contract

Worktree: `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-h1-json-canonical`
Branch: `codex/modernization-h1-json-canonical-20261007`
Base: `c8eb78afc083890acbda32d5db8cd7387eccef7a`

## Scope and Source Acceptance

This is the common-entry source unit, not H1 completion or deployment acceptance.
The approved invariant is one canonical JSON snapshot paired with its storage revision;
every mutation uses an explicitly supplied editing revision and returns its own saved
snapshot. DB config, process caches, packaged defaults and YAML are not authorities.
No canonical data, historical dates, source indexes, seed data or locked workbook is edited.
Runtime, tests, build, DB, network, OCR, server, commit, merge, push and deploy are not run.

Frozen staged source (existing eleven files retained; order caller and sync script added):

1. backend/src/api/facilities.py
2. backend/src/api/facility_master.py
3. backend/src/services/canonical_facility_store.py
4. backend/src/services/config_service.py
5. backend/src/services/facility_master_service.py
6. backend/src/services/facility_service.py
7. backend/tests/unit/test_canonical_facility_store.py
8. docs/modernization/h1-json-canonical-common-entry-20261007.md
9. frontend/src/pages/facilities/[id].tsx
10. frontend/src/pages/facility-master.tsx
11. frontend/src/pages/orders/[id].tsx
12. frontend/src/services/facilityData.ts
13. scripts/sync_facility_config_prod_to_stg.py

## Store Configuration

| Environment | Required configuration | Missing-source behavior |
| --- | --- | --- |
| GCS, including Cloud Run | Explicit `FACILITY_MASTER_URI=gs://<operator-selected-bucket>/<existing-verified-object>` | No bucket selection from RAW_BUCKET/TEMPLATE_BUCKET; no object creation from packaged JSON. Missing object/permissions fail. |
| Local, outside Cloud Run | Explicit `FACILITY_MASTER_PATH=<operator-selected-file>` | Missing path/file fails; no packaged default and no initialization/seed. |
| Cloud Run without GCS URI | `K_SERVICE` present, URI absent | HTTP 503 `facility_master_gcs_uri_required`; local path is not a writable/read fallback. |

`infra/terraform/envs/stg/main.tf:93` and `prod/main.tf:102` currently configure only
`FACILITY_MASTER_PATH=/app/src/data/facility_master.template.json`. Their shared env maps
contain no `FACILITY_MASTER_URI`. This source unit does not change IaC or guess an object.
The actual operator-approved canonical object location, verified initial object bytes and
revision, and service-account access to that object remain external deployment inputs.
The chosen reader/writer needs object read plus permissions to create/replace an object
(including delete permission for replacement); access to an unspecified bucket is not verified.
Existing RAW_BUCKET remains the existing workbook/artifact storage input, not a JSON authority.

GCS reads pin `download_as_bytes(if_generation_match=generation)` after metadata read.
Writes use that expected generation with `retry=None`, returning uploaded bytes and the
upload response generation, never a later GET. Tokens include the configured source identity.
There is no 409 retry or substitution of a newer token. An uncertain upload outcome is
explicit `facility_master_save_outcome_unknown`, requiring operator reload, not replay.

Local reads/writes hold a shared/exclusive flock on a stable sidecar. CAS checks bytes,
inode and mtime together; writes use a unique temporary file, fsync, atomic replace and
directory fsync. Even a same-content own save returns a new revision. Direct file writers
that bypass this contract are not permitted as canonical mutations. Post-replace durability
failure is explicitly reported as saved with unconfirmed durability, not rolled back.
Duplicate JSON keys, duplicate/missing facility IDs and non-finite numbers are blockers.

## API and Own Response

| Actual entrypoint | Request | Own response |
| --- | --- | --- |
| GET /facility-master | No mutation | facility_master, revision, source=canonical_json, path, validation |
| PUT /facility-master | facility_master + expected_revision | Exact saved master + revision, updated, projection, validation |
| GET /facilities | No mutation | facilities + revision from one snapshot |
| GET /facilities/{id} | No mutation | facility/config/resolved_config/order_form_patterns + revision from one snapshot |
| POST /facilities | name, areas, expected_revision | Exact saved facility/config/resolved_config + revision/projection |
| PUT /facilities/{id} | name, optional areas, expected_revision | Same own snapshot envelope; unchanged entry/root keys retained |
| PUT /facilities/{id}/config | config + expected_revision | Raw saved config and resolved config from own master, never independent GET |
| POST /facilities/{id}/order-form-source-workbook | Multipart file, optional month_id, required expected_revision | Own config envelope + actual source_workbook receipt |

JSON requests missing expected_revision stop with HTTP 428; stale tokens stop with 409.
Multipart omission is rejected by FastAPI's required form field. Invalid payloads stop with
400; missing facility stops with 404. Template-definition change protection is evaluated
against the same expected snapshot inside CAS, not a separate latest DB/config read.
Omitted template-definition keys preserve their exact canonical values. Supplied template
IDs and authored `columns`, `main_ocr_row_fields`, `columns_authoritative` changes (including
presence changes) remain blocked on the ordinary config endpoint. Non-column override
metadata, including table/grid geometry and recognizer parameters, remains editable using
the same CAS snapshot; no column normalization or source-index inference is performed.
Full-master editing remains the existing
explicit full JSON operation. No template/source-index inference is performed on save.

Workbook upload checks the fixed revision before storing the file and checks CAS again
before referencing it. A concurrent JSON change does not rebase the workbook patch.
CAS failure returns the upload receipt and explicit unreferenced status when non-save is
known; uncertain/saved failures do not assert rollback. Upload validation/storage errors
report that an unreferenced file may exist. Cloud Run refuses local workbook storage.

## DB Projection and Reconciliation

JSON commits before DB projection. Projection never supplies values to covered canonical
reads. The response says `updated=true` with `projection.status=synced` or
`reconcile_required`, paired to the own JSON revision. Event recording also retains the
original CRUD event and reports `event.status=record_failed` rather than claiming rollback.
Frontend shows these explicit saved-but-reconciliation/event warnings.

Projection checks the own revision before and after its transaction. A concurrent newer
JSON save or DB failure yields reconcile_required. A DB transaction cannot atomically
commit with GCS: projection can be temporarily stale and must never be used as authority.
Explicit maintenance `reconcile_projection(expected_revision=...)` reads and verifies that
revision, then rebuilds projection without a JSON write or choosing a later token. It is
not automatically invoked as a mutation retry. No new maintenance API/CLI is added here.
DB-only historical rows are retained for existing references, not merged into the catalog;
facility IDs/order references are not remapped by inferred name equality.

## Covered Reentry Removal and Frontend

- facility_master_service.get_master no longer builds a DB-overridden master; save_master
  and mutate_master return their own store receipt. DB-first commit/local-JSON save is gone.
- config_service.load_facility_master has no data cache or implicit packaged file. Matcher,
  facility-name resolution, get_facility_by_id and get_facility_config no longer query DB.
  Facility/root/pattern config composition uses one passed master, not repeated latest reads.
  It does not manufacture facility_template_id/name from a facility name or facility ID.
- facility_service list/get/raw-config reads are JSON-only. Create/update/config mutations
  all enter mutate_master with required revision. The old DB-authority builder and legacy
  config sanitizer explicitly stop rather than rebuilding from DB/latest template values.
- facility-master.tsx captures revision on initial/explicit read and advances it only from
  its accepted own save. Normal and specialized saves share that full-master CAS envelope.
  Target/input/command generation guards apply to own saves and reloads. Saving does not
  normalize all sibling facility/column schemas; unknown fields, null/zero and absent dates
  are retained unless the operator edits the corresponding value.
- facilities/[id].tsx sends its fixed revision for normal facility/config/workbook saves.
  Own config save no longer rereads GET. All adopted values/tokens come from the own receipt;
  dirty sibling inputs remain. Explicit reload requires confirmation to discard all unsaved
  facility/config/JSON inputs, then updates the whole facility/config/options baseline from
  one response. Declining keeps inputs and does not read/adopt a new revision. File-read completion also
  checks target/input generation. Workbook upload refuses unsaved config/facility inputs.
  The pinned vendor EditForm does not implement onValuesChange and does not reset on
  defaultValues changes. Caller-local RHF watch subscriptions capture actual inputs;
  accepted full reload remounts both editors together from the same snapshot. Master
  reload also re-identifies the selected facility by ID, not its former list index.
- facilityData.ts exposes and verifies the revision/envelope contract. Catalog name reads
  fail on missing/duplicate identity/name rather than pretending an empty successful list.
- orders/[id].tsx freezes raw config/revision/target together; opening the geometry editor
  explicitly reads its editing snapshot, and explicit reset confirms dirty input discard.
  Target, input generation (including same-value/reverted operator edits), and command
  generation guard reads, saves and errors. Background OCR metadata updates display but
  does not replace open/dirty geometry inputs or units. Geometry save and persisted expanded
  cell-copy save both send the fixed expected_revision. Accepted own responses advance the
  full config/revision pair; returned geometry is adopted from the actual saved override.
  Conflict/unconfirmed receipt keeps inputs and invalidates the base until explicit reload.
  No save-dispatch GET, latest-token substitution or post-save config GET is used. The
  existing expanded-copy mode effect uses the existing sheet baseline, preserving that
  operation without a new sheet read. A stale target command may already have committed;
  its response never changes the new target's inputs, tokens or message.
- sync_facility_config_prod_to_stg.py captures a complete destination snapshot/revision
  before reading the intended source config. Its sole PUT sends that fixed destination
  revision and validates the target/own receipt; summary values come from the own response.
  `--write-mode` accepts only `api`. DB bypass, API-error DB fallback, retry, and post-save
  destination GET are removed; source config content is not silently rewritten to fit.

GET facility detail keeps the legacy DB active-version reference separately as
template_version_reference and explicitly reports date_version_integration_required.
It is not stamped onto resolved_config as if it described the JSON snapshot.
DB-reference failure is explicitly template_version_reference_status=unavailable;
it does not substitute cached metadata or suppress the complete canonical config snapshot.
The facility-master page displays the legacy reference with this unresolved-status warning.

## Exact Remaining Integration

Source caller audit covers backend/src, frontend/src, scripts and backend/scripts, including
unqualified function/import searches and REST route strings:

| Changed contract | Actual source callers | Status |
| --- | --- | --- |
| save_master | api/facility_master.py PUT | Required expected_revision; own master receipt |
| create_facility | api/facilities.py POST | Required expected_revision; own facility envelope |
| update_facility | api/facilities.py PUT | Required expected_revision; own facility envelope |
| update_config | api/facilities.py config PUT and workbook POST | Strict config wrapper / required multipart revision; own receipt |
| Frontend ordinary command callers (7) | facility-master.tsx normal + specialized PUT (2); facilities/[id].tsx facility/config/workbook (3); orders/[id].tsx geometry + expanded-copy config (2) | All send captured revision and adopt own response |
| Script config command (1) | sync_facility_config_prod_to_stg.py | Fixed destination revision; own receipt; no DB fallback |

No other production direct/imported caller of these four changed mutation signatures was
found. main.py sync_facility_names_from_master, outputs.py get_facility, order_service and
facility_template_version_service ensure_facility_materialized retain their existing
signatures and consume JSON-backed identity. import_facility_master_to_db.py calls the
unchanged explicit projection upsert, not a canonical mutation. Display-only facility list
consumers in order-forms, pdf-upload, orders and workflow-v2 retain the facilities array.
OCR registry PUT/delete explicitly stop in api/ocr_registry.py; the similarly named
update_facility is not a caller of facility_service.update_facility. workflow-v2 fax-template
PUT and old order column-template commands remain existing explicit disabled version-writer
paths; they are not silently adapted to a guessed current JSON/date version.

Old fixture migration is deferred, not reported as passing. The seven source files using
old facility commands/envelopes are backend/tests/contract/test_facility_config.py,
test_user2_permissions_api.py, test_orders_ocr_sheet_history_api.py and
backend/tests/integration/test_bagging_delivery_slots.py, test_ocr_sheet_history.py,
test_facility_config_apply.py, test_expanded_cell_copy.py. They require explicit read-pair
capture and own-envelope expectations, not hidden latest revisions or weakened assertions.

Next source unit must replace facility_template_version_service's DB date resolver,
lazy active-version creation, current-config fallback and unproven lineage backfill with
full canonical JSON version schema, unique date resolution and verified history migration.
It must connect order/ingest/candidate/config-for-template, workflow-v2, OCR, order-form,
output_builder, shipping, total and master-template callers to that chosen version.
It must adopt one verified JSON version ID/digest across the thirteen output model types:
Order, OcrJob, OrderOcrEvidenceRun, OrderSheetDraft, OrderWorkflowState, OrderCurrentState,
OrderConfirmedSnapshot, OrderBaggingResult, OrderOutputBundle, Bag, LabelRow, DeliveryNote,
ManufacturingAggregateRow. No newest-active backfill is acceptable.

Maintenance bypasses outside this unit still require migration/removal:
scripts/import_facility_master_to_db.py (projection only),
scripts/backfill_facility_template_version_lineage.py (unproven current-version stamps),
backend/scripts/normalize_facility_source_indexes.py (inferred JSON writes), and
backend/scripts/generate_facility_template_artifacts.py (unfixed date/version source).
backend/scripts/seed_facilities.py is an explicit DB seed/projection writer, not a canonical
JSON updater; backend/scripts/evaluate_reconstruction_strategies.py still reads a packaged
file for offline evaluation. Their source/date migration is not substituted for verified
canonical history. batch_compare_default_column_drift.py and template-artifact generation
now require explicit canonical source env for config_service reads. None was executed.
Disabled version writers are not re-enabled. Week-boundary human selection remains held;
this unit chooses neither week start nor a split policy, and invents no historical dates.

## Field and Area Serialization Source Audit

- facilities/[id].tsx compares each form value with its fixed configRecord draft, not the
  edited raw JSON text. Only changed form keys are serialized. Untouched explicit null,
  zero, missing keys, original tag arrays and unknown JSON keys retain their exact values.
  Raw JSON/file edits are retained when the corresponding form field is unchanged. Name
  save omits untouched areas; explicit areas=null differs from omitted areas. JSON display
  retains null instead of converting it to an empty object.
- facility-master.tsx ordinary save compares fields to the fixed edit-start baseline,
  preserving explicit specialized/JSON edits when the corresponding form is unchanged;
  it merges only changed draft fields. Specialized/full
  master save uses the same unchanged master serialization. Area edits carry the original
  array position and patch only the chosen name/label or ID field. Add/delete preserves all
  other raw elements, including strings, null, zero, unknown objects and absent IDs. A name
  edit never supplies ID from name; explicit ID input alone adds/changes that ID field.
- String-list edit/add/delete uses the raw array, not display string normalization. FAX
  column edit updates only the selected raw position, and add/delete keeps untouched raw
  columns. Header/format/other non-semantic edits do not regenerate main_ocr_row_fields;
  existing explicit semantic column operations retain their row-field derivation behavior.
- facility_service update copies supplied areas exactly and never normalizes existing
  entries. Config mutation retains canonical identity/areas; workbook mutation changes
  only its explicit config fields. Catalog responses no longer add an id alias to area_id.
  Full-master mutations do not infer identifiers or normalize sibling objects.
- New facility POST is the only area helper that creates new random identities for new
  inputs with no ID key; it does not derive them from names or repair supplied null IDs.
  Existing update, materialization and projection never call this creation helper.
  Projection uses exact explicit IDs/names without trimming values. Missing/duplicate/
  conflicting identity stops projection; JSON remains saved and the own response reports
  reconcile_required, not invented IDs, missing-row success or JSON rollback.

Source behavior additions exercise the actual pure frontend TypeScript serializers for
one-field changes, raw JSON edits, untouched null/zero/tag-array/unknown preservation,
name-only edits with missing/null IDs, and explicit ID entry. Backend cases exercise
name-only/one-area mutations, untouched sibling/root values, projection missing/ambiguous
ID rejection and isolated new-identity creation. These test definitions are not executed.

## Deferred Execution

Focused behavior test source is added in backend/tests/unit/test_canonical_facility_store.py:
local CAS/second own save/value preservation, missing revision/config, Cloud Run local stop,
atomic replacement failure, malformed/duplicate JSON, no cached/DB read reentry,
GCS pinned-read race, GCS write race without retry and own-generation response adoption.
Added pure source behavior cases cover geometry changes with unchanged authored column
identity, rejection of column/source-index/template-ID changes, and no invented columns
when adding geometry to an override without columns.
No tests are executed. Old fixture/source migration is not performed in bulk.
Source review and git diff --check are the only performed checks; runtime, IAM, deployment
object contents, API behavior, frontend type/build and full H1 acceptance remain unverified.
Monitoring is assigned to the parent by the task. Independent monitor approval is not
claimed here; no commit, runtime acceptance or overall H1 acceptance is authorized by this freeze.
