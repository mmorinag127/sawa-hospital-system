# Data Model: Facility OCR Pipeline

## Entities

### Template

- **Purpose**: Defines facility layout, matching thresholds, ROI coordinates, and postprocessing rules.
- **Key Attributes**: template_id, facility_id, version, match_thresholds, warp_size, roi_definitions, postprocess_rules.

### Facility

- **Purpose**: Maps operational facilities to a template version.
- **Key Attributes**: facility_id, name, active_template_id, status.

### Job

- **Purpose**: Tracks a single ingestion attempt and its lifecycle.
- **Key Attributes**: job_id, input_reference, status, template_id, output_reference, metrics, error_summary, created_at.

### OCR Result

- **Purpose**: Captures extracted quantities and notes for a job.
- **Key Attributes**: job_id, quantities_by_row_col, notes_text, failed_cells, warnings.

### Artifact

- **Purpose**: Stores intermediate assets for review or debugging.
- **Key Attributes**: job_id, artifact_type, storage_reference, created_at.

## Relationships

- Facility 1..N Template (versioned templates per facility).
- Job N..1 Template (each job selects one template).
- Job 1..1 OCR Result.
- Job 1..N Artifact.
