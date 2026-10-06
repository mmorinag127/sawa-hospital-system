# H1 Dated Complete Facility Versions

## Source Unit

WT: `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-h1-json-canonical`
Branch: `codex/modernization-h1-json-canonical-20261007`
Base: `1d69f49b7ea1da3e9643a34f14904b981fc70df0`

The committed JSON common-entry unit is retained. This unit adds actual dated resolution
and explicit editing contracts; it does not finish downstream adoption or runtime acceptance.
Canonical JSON/user data and the locked workbook are not modified. No runtime, tests,
build, lint, DB, network, commit, merge, push or deploy is run.

Read sources: local H1 common-entry contract and hospital UI handoff; approval plan
`worktrees/menu-master-provenance-20261001/docs/maintenance/sawa-product-modernization-plan-20261004.md`
sections on facility-period management; existing inclusive `_date_range_contains` in
facility_template_version_service. The plan's proposed exclusive end is not adopted:
the explicit task and existing saved range semantics keep both boundaries inclusive.
Applicable shared UI local docs are read from `platform-main/packages/ui/README.md` and
`platform-main/docs/modernization/mui-shared-ui.md`; these are read-only integration sources.

## JSON Model

Each existing `facilities[]` entry may contain `versions[]`. Existing entry fields,
including legacy valid_from/valid_to, remain unversioned reference material. They never
create or supplement a resolved version. A version is an explicit complete record:

```json
{
  "version_id": "operator-supplied-identity",
  "valid_from": "YYYY-MM-DD",
  "valid_to": null,
  "facility": {
    "facility_id": "same-explicit-facility-id",
    "facility_name": "recorded-name",
    "areas": [],
    "fax_template": { "operator-supplied-template-content": "..." }
  }
}
```

This is a structural example, not canonical data or a seed. `facility` uses the existing
flattened facility JSON layout: its setting keys, complete fax_template, areas and name
are stored together. Additional version/facility/template keys and explicit null/zero
values remain intact. No root fax_template_base, current facility, DB, cache, template ID
inference, old override or normalized source index supplies missing version content.
The caller explicitly supplies the complete template object, not an ID-only substitute.

Version IDs are required operator inputs, unique within the selected facility. Every
record requires a valid start and an explicit end key. `valid_to: null` means an explicitly
open-ended record; it does not turn unversioned old settings into an active version.
Calendar strings must be exact YYYY-MM-DD; timestamp truncation/timezone guessing is not
performed. If full facility content retains its own valid_from/valid_to keys, they must
equal the record boundary, not silently describe a second range.

No versions are created from current settings and no historical start/ID is invented.
Facility duplication copies only the unversioned reference to a new facility identity;
the original facility's recorded history remains untouched and is not relabeled as the
new facility's history. A new dated record requires a separate explicit operation.

## Shared Actual Entry

`canonical_facility_store.resolve_facility_version(snapshot, facility_id, effective_date)`
validates the full selected-facility version set, then requires exactly one matching range.
The returned facility and record are deep copies paired with that same snapshot.revision.
All stored version mutations, including full-master CAS, pass version validation in the
store encoder; service-level validation also protects the ordinary master command path.

`facility_master_service.resolve_facility_for_date` performs one canonical snapshot read.
`config_service.get_facility_config_for_date` exposes its complete result with config equal
to the recorded full facility, without legacy config-building/normalization or rereading.
This path is used by the actual GET resolved API and facility date-preview UI.

Stops: missing/invalid date or definition (422); duplicate/missing version identity (422);
inclusive overlap, multiple facility targets, unrecorded past, gap or out-of-range date
(409). A shared end/start day overlaps; adjacent dates must be explicitly recorded by the
operator. No range is extended or date shifted by the resolver. A gap fails when queried;
the system does not invent an overall historical coverage horizon. Missing facility fails
as an unresolved target. The result never chooses latest, first, DB or an unbounded legacy row.

## Editing and Read API

| Entry | Request | Result |
| --- | --- | --- |
| GET /facilities/{id}/resolved | required explicit effective_date query | facility_id, effective_date, complete version/facility/config, revision, source=canonical_json |
| GET /facilities/{id}/versions | explicit editing read | all recorded versions + canonical revision; unversioned reference separately marked reference_only |
| POST /facilities/{id}/versions | version object + expected_revision | new operator-ID record; existing ID conflicts; own full version/list/revision receipt |
| PUT /facilities/{id}/versions/{version_id} | complete version object + expected_revision | exact existing target replacement; missing/duplicate target conflicts; own receipt |
| PUT /facility-master | existing complete master + expected_revision | same CAS; version validation applies to nested version records |

Version body identity must match the path. POST is not an upsert and PUT cannot silently
create a missing history record. Both use the common CAS, retaining reference/root/sibling
version data. Missing expected revision stops at 428; stale revision stops at 409. Neither
command retries or reads a latest token. Returned version/list values come from the saved
master and own revision, not an independent GET.

Projection retains the common entry's explicit failure/reconcile behavior. Its scope is
unversioned reference only, declared in version command receipts; no dated DB active-version
selection/stamp is claimed. DB projection never supplies dated resolution values.

## Facility UI

The facility-master screen links the selected facility to dated management. The detail
screen explicitly reloads a version list/revision pair, then selects a record or a blank
new-version input. It does not select a first existing record or copy old data into a new
version. New ID/start/content are blank required operator input. Open end has an explicit
checkbox; an untouched blank end is not automatically made null. Existing explicit null
end is represented by that checked state.

Selection/reload confirms dirty input discard. Saves capture exact target, input generation,
operation generation and the fixed list revision. Only matching own result body/revision
advances that baseline. Failed saves preserve inputs; conflict or unconfirmed dispatched
outcome blocks writes until explicit reload. Ordinary facility/config editors retain their
separate fixed bases; a version save does not paste its newer token into unrelated drafts.
Full ordinary reload clears the version editor only after its discard confirmation and
accepted response. Background refresh does not advance a dirty version editing base.

Date preview is separately guarded and never sets an editing revision. Undated composed
config and the old DB version display are explicitly reference-only, not a selected JSON
date version. The actual dated result is displayed separately with its date and revision.

## Source Cases Added

`backend/tests/unit/test_canonical_facility_versions.py` defines inclusive boundary cases,
exact null/zero/unknown/template preservation, no ID inference, invalid/missing calendar
dates and timestamps, overlap/duplicate ID, missing/inverted/end boundaries, conflicting
nested dates, unrecorded past/gaps, no implicit legacy upgrade, one-snapshot config entry,
own version result/unchanged siblings, missing edit target, actual local store CAS, stale/
missing revision rejection, and second save using own returned revision. None is executed.

## Concrete Follow-Up Integration

- facility_template_version_service.py: replace DB resolve_facility_config_for_date (733),
  active-version/lazy creation (680), legacy backfill/current-version stamping (484/524),
  and version record references with verified JSON identity/content provenance.
- output_builder.py: `_effective_facility_config_date_from_order` and `_resolve_facility_config_for_order`
  DB-version lookup/date resolver (294-315) must adopt the actual dated JSON snapshot.
- order_service.py: registration's ensure_active_template_version (9799), config-for-template
  (9065/29123), and dated/output/ingest uses of undated get_facility_config (445/7365/13795/
  15370/18190/20893/29659/30219) require explicit authoritative date/version lineage.
- order_workflow_v2_service.py: undated config reads (413/737/1676/3259) and active-version
  creation (1709) must share that recorded dated version, not initialize from latest settings.
- hakodate_cell_ocr_batch_service.py (1037), hakodate_step_review_pipeline_service.py (3602),
  config_service.get_facility_config_for_template/config candidate resolution (1154+):
  distinguish the recorded facility date version from the explicit FAX structure version.
- order_form_service.py (596/1450/1500), master_order_form_template_service.py (1097),
  API generated-fax-template-diagnostics and total_service.py (70, currently `or {}`):
  remove undated/reference/default continuation in actual dated materialization paths.
- Shipping/bagging/labels/delivery/aggregation must consume the same resolved lineage from
  their selected orders; no independently refreshed facility config or newest DB stamp.
- Bind verified JSON version ID/content provenance across Order, OcrJob,
  OrderOcrEvidenceRun, OrderSheetDraft, OrderWorkflowState, OrderCurrentState,
  OrderConfirmedSnapshot, OrderBaggingResult, OrderOutputBundle, Bag, LabelRow, DeliveryNote,
  ManufacturingAggregateRow. This unit does not invent IDs/dates for existing rows.
- Historical migration/maintenance scripts remain source-verification work: lineage
  backfill, source-index normalization, template-artifact generation and old DB projection
  import/seed/evaluation utilities require verified historical source, not latest stamping.

The human week-boundary choice remains unanswered. This day-only resolver neither selects
a week start nor decides how a multi-day document spans versions. Independent day-based
resolution is implemented without treating that question as a blocker.

## External Configuration and Verification

Cloud Run still requires an explicit operator-selected existing `FACILITY_MASTER_URI` GCS
object and reader/writer generation-CAS permissions. The existing stg/prod Terraform maps
only supply the packaged local FACILITY_MASTER_PATH; this unit does not guess an object,
write deployment config or migrate canonical data. Outside Cloud Run, an explicit local
path uses the committed locked/atomic CAS. There is no Cloud Run local writable fallback.

Source diff inspection and git diff --check only; execution, type/build, DB/schema/live
acceptance and human week-boundary decisions are not claimed. Parent performs source audit
and integration judgment. No H2 worktree or backend orders.py is written.

## Authored Snapshot Correction

The dated read and every canonical write now share validate_authored_snapshot through
facility_versions. A nonempty object or template_id alone is not a complete template.
The production validate_template_columns requires nonempty authored columns, a quantity
column, valid/unique physical source indexes for read roles and unique supplied column IDs.
config_validator validates the existing index/role schema and authored row-field agreement;
canonical_field_name_from_template_column is used only to reject unresolved quantity
semantics, never to persist inferred fields. No normalization/repair function is called.

Concrete snapshot-field audit:
- Identity/name/areas: existing full snapshot identity and name checks remain; areas key
  is required, recorded null and absent area identifiers remain unchanged.
- Column layout/quantity: production column and row-field validators above; no source_index
  repair, area-ID reconstruction, root template merge or stored derived field list.
- Geometry: supplied facility_name_box/table_box/grid_table_box/date_boxes use existing
  bbox validator; supplied match/warp/rois/postprocess use existing structured validators,
  including warp output dimensions and ROI quantity schema/box counts. Geometry is not
  synthesized; no new mandatory fixed grid is imposed on production image-detected layouts.
- OCR/provider/source-workbook settings: existing validators applied to both recorded
  facility config and template settings; month-source types and provider settings checked.
- Invoice/packaging/label/bag settings: validate_facility_config covers existing normal-form
  types and invoice column name/source requirements. Optional absence/null is not filled.
- Unknown fields, numeric zero and optional null: passed through exactly. Optional null
  column semantic is omitted only in a disposable validation view for the production
  validator's object access; original content and saved/returned JSON remain untouched.

Source cases use the production normal-form date/daypart/menu/quantity/note pattern with
explicit source indexes, authored geometry, row fields and header_rows=0. ID-only, empty,
quantity-less, unmapped quantity, missing source index, malformed geometry, mismatched row
fields and malformed config are rejected on both write and dated read. They are not run.

Date preview has its own previewGeneration, never advances versionGeneration or editing
revision. Starting save invalidates old previews; target/reset invalidates preview ownership.
Preview input/button and version reload are disabled during save, with handler guards too.
Own-save adoption remains guarded by its original editing input/target/command ownership.

## Read/Command Ownership and Geometry Root Correction

All dated-list, save and preview operations capture one shared captureVersionOwnership:
target, editing generation, input generation, command generation, target lifecycle owner
and browser auth-cache generation plus active authentication. Reads additionally require
no save owner; save requires its own owner. Guards run before dispatch and after await,
before clearVersionEditor, body/revision adoption or scoped errors. Starting any ordinary
or version command invalidates older reads even after that command finishes. A read started
before save cannot reset the saved base, working input or busy owner. Handler guards also
reject read started during save; this is not only a disabled-button fix. Source assertions
cover read-before-save, save-before-read, input change during read/save, same-target new
generation, target changes and authentication changes.

Geometry definitions inspected (not one interchangeable bbox schema):
- fax_extractor._crop_to_bbox: top-level facility_name_box and date_boxes[].bbox are
  normalized XYXY. Reject nonfinite/bool, negative origin, inverted/zero extent and >1.
- order_service._parse_grid_table_box/_synthesize_grid_column_edges: table_box and
  grid_table_box are XYXY, with recorded template.units. Normalized domain is [0,1];
  non-normalized pixel boxes require recorded warp dimensions for upper-bound validation.
- template_builder and fax_roi_extractor._normalize_box/_coerce_box: rois name/menu/notes/
  table and qty cells are XYWH, including authored dictionaries {x,y,w,h} for quantity.
  Positive width/height and nonnegative origin are checked; the consumer's <=1.5 normalized
  classification is used only for domain validation, without coordinate conversion.
  Pixel XYWH limits come from recorded warp.output_size, never guessed image dimensions.
- warp.output_size: exactly two finite positive dimensions, no zero-size output.
- grid_column_edges/grid_row_edges: finite, strictly ascending, nonnegative, within
  recorded normalized/pixel domain. ROI qty column_edges/row_edges are normalized per
  template_builder. Static positive rows*cols requires all recorded cells; dynamic zero
  row counts remain allowed. Negative/bool schema dimensions stop.
- Optional null geometry remains optional null. Numeric zero origins/header_rows and
  unknown keys remain untouched. Invalid zero extents are not confused with legitimate
  zero coordinates. No common bbox validator is globally tightened: XYXY and XYWH caller
  contracts are validated independently at the canonical dated entry.

Both read and write source cases cover inverted XYXY, zero extent, normalized overflow,
nonfinite coordinates/output size, invalid date/name/grid boxes, pixel overflow, malformed
XYWH cells and unordered edges. A real builder-pattern pixel XYWH/dictionary-cell case
asserts exact original JSON preservation and prevents accidental XYXY reinterpretation.
All cases remain source-only and unexecuted.
