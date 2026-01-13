# Feature Specification: Facility OCR Pipeline

**Feature Branch**: `001-ocr-pipeline-update`  
**Created**: 2026-01-08  
**Status**: Draft  
**Input**: User description: "workspace/ocr_pipeline_spec.md に追加のOCRの処理に関する仕様を示したので、これに沿って追加の計画とタスクを作るために仕様を作って"

## User Scenarios & Testing *(mandatory)*

<!--
  IMPORTANT: User stories should be PRIORITIZED as user journeys ordered by importance.
  Each user story/journey must be INDEPENDENTLY TESTABLE - meaning if you implement just ONE of them,
  you should still have a viable MVP (Minimum Viable Product) that delivers value.
  
  Assign priorities (P1, P2, P3, etc.) to each story, where P1 is the most critical.
  Think of each story as a standalone slice of functionality that can be:
  - Developed independently
  - Tested independently
  - Deployed independently
  - Demonstrated to users independently
-->

### User Story 1 - Auto-extract weekly order data (Priority: P1)

Operations staff upload a single-page weekly order PDF and receive a structured extraction that uses the correct facility template, including quantities by category and any notes.

**Why this priority**: This is the primary business outcome and enables downstream validation and fulfillment.

**Independent Test**: Upload a known single-page order PDF for a registered facility and verify that a structured output is produced with quantities per defined row/column.

**Acceptance Scenarios**:

1. **Given** a valid single-page order PDF for a registered facility, **When** it is ingested, **Then** the system outputs a structured JSON with template id, row/column labels, quantities, and notes.
2. **Given** the same input file and generation is ingested again, **When** it is processed, **Then** the system detects a duplicate job and does not create a second output.

---

### User Story 2 - Handle unknown templates (Priority: P2)

Operations staff need unrecognized order formats to be flagged and queued for manual review and template registration.

**Why this priority**: The system must fail safely when a facility format is not yet registered, without producing incorrect data.

**Independent Test**: Upload an order PDF that does not match any existing template and verify that it is flagged and preserved for review.

**Acceptance Scenarios**:

1. **Given** an order PDF that does not match any template thresholds, **When** it is ingested, **Then** the system marks it as unclassified and saves the input and artifacts for review.

---

### User Story 3 - Highlight low-confidence cells (Priority: P3)

Operations staff need visibility into cells that the OCR could not confidently read so they can validate or correct them.

**Why this priority**: It prevents silent errors and supports auditability when OCR is imperfect.

**Independent Test**: Process a PDF with known illegible cells and verify those cells are flagged while other cells still produce values.

**Acceptance Scenarios**:

1. **Given** a PDF where some quantity cells are unreadable, **When** it is processed, **Then** the output lists those cells as failed while keeping other extracted values.

---

[Add more user stories as needed, each with an assigned priority]

### Edge Cases

- Multi-page PDFs are ingested.
- No templates are registered or a template is missing required ROI definitions.
- OCR returns repeated garbage lines or non-numeric values for quantity cells.
- Input file is reuploaded or reprocessed with the same generation identifier.
- Storage or job tracking is temporarily unavailable during processing.

## Requirements *(mandatory)*

<!--
  ACTION REQUIRED: The content in this section represents placeholders.
  Fill them out with the right functional requirements.
-->

### Functional Requirements

- **FR-001**: System MUST accept single-page order PDFs from an intake source and create a job record with a unique id and status.
- **FR-002**: System MUST render the PDF into a high-resolution image suitable for both template alignment and OCR.
- **FR-003**: System MUST select the best-matching template from registered templates using defined matching thresholds.
- **FR-004**: System MUST mark inputs as unclassified when no template passes thresholds and store the input and artifacts for review.
- **FR-005**: System MUST align the page to the selected template’s coordinate space before applying ROI definitions.
- **FR-006**: System MUST extract OCR text for each defined ROI, including cell-level quantity ROIs and optional notes.
- **FR-007**: System MUST map quantity outputs to the template’s row and column labels and return values or null per cell.
- **FR-008**: System MUST validate quantity cell outputs against numeric rules and detect repetition anomalies.
- **FR-009**: System MUST retry OCR for failed cells using alternate preprocessing options and record failed cells if still unreadable.
- **FR-010**: System MUST output a structured JSON that includes template id, quantities, notes, failed cells, and job metadata.
- **FR-011**: System MUST prevent duplicate processing of the same input generation and return the existing job result.
- **FR-012**: System MUST support template versioning and associate facilities with the appropriate template version.

### Key Entities *(include if feature involves data)*

- **Template**: Defines the facility format, matching thresholds, ROI definitions, and validation/retry rules for extraction.
- **Facility**: Represents an operational unit and its mapping to a template version.
- **Job**: Tracks a single ingestion attempt, status, input reference, output reference, and metrics.
- **OCR Result**: The structured extraction output, including quantities, notes, and failed cells.
- **Artifact**: Stored intermediate assets for review when classification or extraction fails.

### Assumptions

- Each order PDF is a single page.
- Facilities have a known set of templates that can be updated over time.
- Quantity grids are fully defined by templates and require outputs for every cell (value or null).

### Dependencies

- An intake source for PDF uploads and a place to store outputs and artifacts.
- A template registry that provides ROI definitions and matching thresholds.
- An OCR service that can process image regions and return text.
- A job tracking store for de-duplication and audit status.

## Success Criteria *(mandatory)*

<!--
  ACTION REQUIRED: Define measurable success criteria.
  These must be technology-agnostic and measurable.
-->

### Measurable Outcomes

- **SC-001**: 95% of valid single-page inputs produce a structured output within 2 minutes of ingestion.
- **SC-002**: 100% of inputs that do not match any template are flagged as unclassified with review artifacts saved.
- **SC-003**: 100% of quantity cells defined by the template are present in the output as a value or null.
- **SC-004**: Duplicate ingestion events do not create more than one job record per input generation.
- **SC-005**: 100% of failed quantity cells are listed with their row/column identifiers in the output.
