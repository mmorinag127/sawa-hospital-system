import type { JobStatusRecord } from "@sawa/ui";

export type WorkflowV2 = {
  order_id: string;
  common_job_status?: JobStatusRecord | null;
  ocr_job_binding_error?: string | null;
  revision?: number | null;
  source?: string | null;
  state: string;
  headline?: string | null;
  primary_action?: string | null;
  selected_ocr_result_id?: string | null;
  saved_sheet_id?: string | null;
  confirmed_snapshot_id?: string | null;
  facility_id?: string | null;
  week_start?: string | null;
  week_end?: string | null;
  template_id?: string | null;
  template_version_id?: string | null;
  expanded_cell_copy_mode?: ExpandedCellCopyMode | null;
  context_suggestion?: ContextSuggestion | null;
  bagging_result_id?: string | null;
  output_bundle_id?: string | null;
  menu_amount_resolution?: OrderDetail["menu_amount_resolution"];
  pre_save_checks?: PreSaveChecks | null;
  blockers?: string[] | null;
  warnings?: string[] | null;
  recognition_override_invalidation?: {
    reason: string;
    geometry_kind: string;
    invalidated_at: string;
  } | null;
  quad_override?: {
    quad_px?: number[][];
    quad_source?: string | null;
    decision?: string | null;
    created_at?: string | null;
  } | null;
  row_axis_override?: {
    corrected_ys?: number[];
    coordinate_space?: { mode?: string; width?: number; height?: number };
    source?: string | null;
    created_at?: string | null;
  } | null;
  ocr_job?: {
    ocr_job_id?: string | null;
    order_id?: string | null;
    workflow_attempt_id?: string | null;
    native_ingest?: { mode: string; intake_protocol: string; execution_id: string } | null;
    status?: string | null;
    created_at?: string | null;
    updated_at?: string | null;
    started_at?: string | null;
    finished_at?: string | null;
    elapsed_seconds?: number | null;
    processing_stage?: string | null;
    result_state?: string | null;
    progress_step?: number | null;
    progress_total?: number | null;
    progress_label?: string | null;
    error?: string | null;
    error_message?: string | null;
    error_detail?: string | null;
    error_user_message?: string | null;
    recovery_action?: string | null;
    evidence_run_id?: string | null;
    quantity_content_editable?: boolean;
    recognition_review?: {count: number; cells: Array<{sheet_cell: string; raw_text: string; reason: string}>} | null;
  } | null;
};

export type OrderVersion = {
  id?: string;
  version_no?: number | null;
  document_id?: string | null;
  message_id?: string | null;
  received_at?: string | null;
  is_current?: boolean | null;
};

export type OrderDetail = {
  id?: string | null;
  ocr_job_binding_error?: string | null;
  menu_amount_resolution?: {
    status: "resolved" | "unresolved";
    blocker?: string | null;
    message?: string | null;
    affected_lines: Array<{
      line_id?: string | null;
      date?: string | null;
      daypart?: string | null;
      menu_name: string;
      diet_type?: string | null;
      area_id?: string | null;
      unresolved_fields: string[];
    }>;
  };
  version_count?: number | null;
  current_version?: OrderVersion | null;
  versions?: OrderVersion[] | null;
  versions_next_before_id?: string | null;
};

export type PreSaveCheckEntry = {
  confirmed?: boolean;
  sheet_hash?: string | null;
  confirmed_at?: string | null;
  anomaly_review_id?: string | null;
};

export type PreSaveChecks = {
  anomaly_review?: PreSaveCheckEntry | null;
  sheet_review?: PreSaveCheckEntry | null;
};

export type PreSaveStatus = {
  sheet_hash?: string | null;
  anomaly_review_confirmed?: boolean;
  sheet_review_confirmed?: boolean;
  ready?: boolean;
};

export type QuadReviewPayload = {
  order_id: string;
  source_receipt?: Record<string, unknown> | null;
  status?: string | null;
  image_png_base64?: string | null;
  image_size?: number[] | null;
  suggested_quad_px?: number[][] | null;
  saved_override?: {
    source_receipt?: Record<string, unknown> | null;
    quad_px?: number[][];
    quad_source?: string | null;
    decision?: string | null;
    created_at?: string | null;
  } | null;
  estimate?: {
    status?: string | null;
    reasons?: string[];
    warnings?: string[];
    refined_quad_px?: number[][];
    initial_quad_px?: number[][];
    corner_shift_px?: Record<string, number>;
    metrics?: Record<string, Record<string, number>>;
  } | null;
  tolerance_policy?: Record<string, number>;
  ocr_job?: WorkflowV2["ocr_job"];
};

export type HeaderAxisReviewPayload = {
  order_id: string;
  source_receipt?: Record<string, unknown> | null;
  status?: string | null;
  error_detail?: string | null;
  image_png_base64?: string | null;
  image_size?: number[] | null;
  crop_box?: number[] | null;
  canvas_size?: number[] | null;
  x_positions?: number[] | null;
  y_levels?: number[] | null;
  saved_override?: {
    source_receipt?: Record<string, unknown> | null;
    corrected_xs?: number[];
    coordinate_space?: { mode?: string; width?: number; height?: number };
  } | null;
  axis_evidence?: Record<string, unknown> | null;
  coordinate_space?: { mode?: string; width?: number; height?: number } | null;
};

export type RecognitionReviewPayload = {
  order_id: string;
  mode: "diagnostic_subset/v1" | "full_manual/v1";
  retained_cell_count: number;
  source_receipt: Record<string, any>;
  error_detail?: string;
  cells: Array<{cell: string; image_png_base64: string}>;
  review_cells?: Array<{sheet_cell: string; raw_text: string; reason: string}>;
};

export type RowAxisReviewPayload = {
  order_id: string;
  source_receipt?: Record<string, unknown> | null;
  status?: string | null;
  error_detail?: string | null;
  calendar_days?: Array<{date: string; expected_band_count: number | null}>;
  body_day_boundary_indexes?: number[] | null;
  header_band_count?: number;
  boundary_diagnostics?: {expected?: number[] | null; actual?: number[] | null; missing?: number[] | null; extra?: number[] | null};
  image_png_base64?: string | null;
  image_size?: number[] | null;
  canvas_size?: number[] | null;
  y_positions?: number[] | null;
  x_levels?: number[] | null;
  saved_override?: {
    source_receipt?: Record<string, unknown> | null;
    corrected_ys?: number[];
    body_day_boundary_indexes?: number[];
    day_labels?: string[];
    coordinate_space?: { mode?: string; width?: number; height?: number };
  } | null;
  axis_evidence?: Record<string, unknown> | null;
  row_height_quality?: {
    manual_review_required?: boolean;
    reason?: string;
    median_height?: number;
    min_height?: number;
    max_height?: number;
    outlier_count?: number;
    outliers?: Array<Record<string, unknown>>;
  } | null;
  coordinate_space?: { mode?: string; width?: number; height?: number } | null;
};

export type FacilitySuggestionCandidate = {
  facility_id?: string | null;
  id?: string | null;
  facility_name?: string | null;
  name?: string | null;
  score?: number | string | null;
  reason?: string | null;
};

export type ContextSuggestion = {
  source?: string | null;
  facility_id?: string | null;
  facility_name?: string | null;
  facility_candidates?: FacilitySuggestionCandidate[];
  week_code?: string | null;
  week_start?: string | null;
  week_end?: string | null;
  week_label?: string | null;
  date_hints?: string[];
  confidence?: string | null;
  created_at?: string | null;
};

export type OcrResult = {
  ocr_result_id: string;
  detail_state?: "not_loaded";
  selectable?: boolean;
  recognition_acceptance?: { edited_by: string; created_at: string; values: Record<string, string> } | null;
  recognition_review?: {
    count: number;
    cells: Array<{ sheet_cell: string; raw_text: string; reason: string }>;
  } | null;
  status?: string | null;
  source?: string | null;
  selected?: boolean;
  artifact_digest?: string | null;
  artifact_manifest?: Record<string, unknown> | null;
  overlay_url?: string | null;
  sheet_review_base_url?: string | null;
  overlay_status?: string | null;
  overlay_message?: string | null;
  created_at?: string | null;
};

export type InspectionPayload = {
  display_scope?: "summary" | "ocr" | "sheet" | "output";
  loaded_sections?: string[];
  ocr_result_count?: number;
  order_id?: string;
  order_header?: OrderDetail;
  ocr_candidates?: {
    order_id: string;
    selected_ocr_result_id: string | null;
    workflow_state: string;
    blockers: string[];
    candidate_template_version_id: string | null;
    hidden_template_mismatch_result_count: number;
    next_before_id?: string | null;
    total_count?: number;
    results: OcrResult[];
  };
  workflow?: WorkflowV2;
  ocr_results?: OcrResult[];
  saved_sheet?: {
    saved_sheet_id: string;
    source_ocr_result_id?: string | null;
    sheet?: Record<string, unknown>;
    edited_at?: string | null;
    created_at?: string | null;
  } | null;
  artifact_lineage?: Record<string, unknown>;
  bagging_result?: Record<string, unknown> | null;
  anomaly_review?: Record<string, unknown> | null;
  pre_save_checks?: PreSaveChecks | null;
  pre_save_status?: PreSaveStatus | null;
  output_bundle?: Record<string, unknown> | null;
};

export type SheetPayload = {
  fields: string[];
  header: string[];
  rows: string[][];
  row_ids?: string[];
  cell_confidence_rows?: string[][];
  cell_provenance_rows?: string[][];
  ocr_numeric_cell_items?: OcrNumericCellItem[];
  ocr_numeric_cell_summary?: Record<string, unknown>;
  target_cell_map?: TargetCellMapItem[];
  [key: string]: unknown;
};

export type OcrNumericCellItem = {
  classification?: string | null;
  value?: string | null;
  confidence_tier?: string | null;
  target_row_index?: number | null;
  target_col_index?: number | null;
  placement_basis?: string | null;
};

export type SheetAutoEditPatch = {
  row_index: number;
  col_index: number;
  field?: string | null;
  label?: string | null;
  current_value?: string | null;
  suggested_value?: string | null;
  confidence?: string | null;
  reason?: string | null;
  evidence?: string | null;
  alternatives?: string[];
  source?: string | null;
};

export type SheetAutoEditResult = {
  status?: string | null;
  patches?: SheetAutoEditPatch[];
  rule_patches?: SheetAutoEditPatch[];
  llm_patches?: SheetAutoEditPatch[];
  llm?: Record<string, unknown> | null;
  job?: {
    job_id?: string | null;
    status?: string | null;
    started_at?: string | null;
    updated_at?: string | null;
    finished_at?: string | null;
    error?: string | null;
    workflow_result_revision?: number | null;
  } | null;
};

export type SheetAnomalyWarning = {
  type?: string | null;
  severity?: string | null;
  row_index?: number | null;
  col_index?: number | null;
  field?: string | null;
  label?: string | null;
  value?: string | null;
  suggested_value?: string | null;
  message?: string | null;
  date?: string | null;
  daypart?: string | null;
  menu?: string | null;
  context_label?: string | null;
  evidence?: Record<string, unknown> | null;
};

export type TargetCellMapItem = {
  target_row_index: number;
  target_col_index: number;
  field?: string | null;
  sheet_cell?: string | null;
  target_cell_id?: string | null;
  bbox?: number[] | null;
  center?: number[] | null;
};

export type OverlayBox = {
  left: number;
  top: number;
  width: number;
  height: number;
  centerLeft: number;
  centerTop: number;
};

export type ConfidenceDisplayMode = "strict" | "assisted" | "suggestion";

export type Step3LayoutMode = "side-by-side" | "stacked";

export type OcrPreviewMode = "overlay" | "original" | "sheet";

export type OcrRunMode = "hakodate" | "llm";

export type OutputPreviewType = "labels" | "delivery" | "order_form_saved_sheet" | "aggregate";

export type OutputPreview = {
  type: OutputPreviewType;
  headers: string[];
  rows: string[][];
};

export type LlmPromptPreset =
  | "numeric_verification"
  | "column_missing"
  | "row_alignment"
  | "special_diet_semantics"
  | "merged_cell_quantity_spans"
  | "freeform";

export type FacilityOption = {
  id: string;
  name: string;
};

export type CanonicalFacilityResolution = {
  status: "ready" | "pending" | "error" | "missing" | "unregistered" | "ambiguous";
  option: FacilityOption | null;
};

export type FacilityTemplateStatus = {
  facilityId: string;
  templateId: string;
  templateIds: string[];
  facilityTemplateId: string;
  loading: boolean;
  error: string;
};

export type ExpandedCellCopyMode = "auto" | "enabled" | "disabled";

export type FacilityTemplateColumn = {
  index: number;
  source_index?: number;
  role: string;
  header?: string;
  header_group?: string;
  name?: string;
  diet_type?: string;
  area_id?: string;
};

export type WeekOption = {
  week_id: string;
  label: string;
  date_from?: string | null;
  date_to?: string | null;
  selected?: boolean;
};

export type WorkflowRevisionToken = {
  revision: number;
  bootstrap: boolean;
};
