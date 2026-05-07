# Master Facility Template / OCR Pipeline Validation - 2026-05-08

## Scope

This report records the proof for the master-template and facility-template
generation work, all-facility local Hakodate OCR pipeline validation, visual
inspection, sub-agent inspection, and stg deploy-source verification.

Covered requirements:

- all generated facility templates have the same table width
- facility category labels are placed from facility configuration
- two-level headers are applied where configured
- generated templates do not contain visible extra/stray lines
- local OCR pipeline is run fresh for all facilities
- each pipeline step artifact is visually checked
- a sub-agent also checks the template and pipeline artifacts
- stg deploy is performed from the resolved `develop` worktree source

## Source And Commits

Deploy source worktree:

`/Users/mmorinag/Sawa/2025.12/worktrees/stg-develop-deploy-20260507`

Deploy branch and HEAD:

- branch: `develop`
- HEAD: `59283f25aeb73f9ab1988e5b533b050074620387`

Relevant commits, newest first:

- `59283f2 Normalize volatile workflow deploy check timestamp`
- `c7484d2 Keep Hakodate OCR target regions template-owned`
- `a51dd20 Fix generated facility template pipeline`
- `84e76a0 Fix master facility template generation`
- `720a1d5 Adjust master template generated column widths`
- `085b9e6 Fix workbook renderer merged borders and anchors`
- `ee44b75 Centralize facility header group settings`
- `19b1e3c Generate facility templates from master layout`

Worktree status before final validation was clean.

## Facility Template Validation

Generation command:

```bash
PYTHONPATH=backend backend/.venv/bin/python tmp/generate_and_render_all_facility_templates_from_master.py
```

Validation command:

```bash
PYTHONPATH=backend backend/.venv/bin/python tmp/verify_generated_facility_templates.py
```

Validation result:

```json
{"output":"tmp/generated_all_facility_templates_fixed_renderer/verification_report.json","facility_count":16,"failure_count":0}
```

Parent visual artifacts:

- `tmp/generated_all_facility_templates_fixed_renderer/review_images/all_facilities_chunk_1.png`
- `tmp/generated_all_facility_templates_fixed_renderer/review_images/all_facilities_chunk_2.png`
- `tmp/generated_all_facility_templates_fixed_renderer/review_images/all_facilities_chunk_3.png`
- `tmp/generated_all_facility_templates_fixed_renderer/review_images/all_facilities_chunk_4.png`

Parent visual conclusion:

- all facility templates use the same table outer width
- generated facility labels are present
- quantity column labels are present and contained in the table
- two-level header groups are visible where configured
- no obvious extra table-line artifacts remain in the reviewed pages

Sub-agent visual conclusion:

- visual sub-agent inspected the same facility-template chunks and returned OK
- sub-agent reported consistent widths, expected two-level headers, correct
  quantity label placement, and no obvious stray lines

## Local OCR Pipeline Validation

Fresh all-facility local run command:

```bash
PYTHONPATH=backend backend/.venv/bin/python tmp/run_all_facility_master_template_ocr_validation.py --include-supplemental-missing --output-dir tmp/all_facility_master_template_ocr_validation_all16_supplemental_after_helper
```

Scope:

- 16 facilities
- FAC00011 and FAC00013 use supplemental real FAX samples because the 2026-04-26
  reference FAX set does not include those facilities

Step PDFs produced:

- `tmp/all_facility_master_template_ocr_validation_all16_supplemental_after_helper/step1_original_fax_accepted_quad_all16_with_supplemental_real_fax.pdf`
- `tmp/all_facility_master_template_ocr_validation_all16_supplemental_after_helper/step2_rectified_fax_by_accepted_quad_all16_with_supplemental_real_fax.pdf`
- `tmp/all_facility_master_template_ocr_validation_all16_supplemental_after_helper/step3_extracted_fax_lines_all16_with_supplemental_real_fax.pdf`
- `tmp/all_facility_master_template_ocr_validation_all16_supplemental_after_helper/step4_axis_match_candidates_and_selected_all16_with_supplemental_real_fax.pdf`
- `tmp/all_facility_master_template_ocr_validation_all16_supplemental_after_helper/step5_rectified_merge_aware_grid_all16_with_supplemental_real_fax.pdf`
- `tmp/all_facility_master_template_ocr_validation_all16_supplemental_after_helper/step6_post_menu_target_red_points_all16_with_supplemental_real_fax.pdf`

Parent visual contact sheets:

The parent agent opened and visually checked every row below with the local
image viewer in this thread before requesting final monitor judgment.

| Step | Chunk | Artifact | Parent visual result |
| --- | --- | --- | --- |
| step1 | 1 | `tmp/all_facility_master_template_ocr_validation_all16_supplemental_after_helper/step_contact_sheets/step1_all_facilities_contact_chunk_1.png` | OK: accepted four-point quads contain the FAX table |
| step1 | 2 | `tmp/all_facility_master_template_ocr_validation_all16_supplemental_after_helper/step_contact_sheets/step1_all_facilities_contact_chunk_2.png` | OK: accepted four-point quads contain the FAX table |
| step1 | 3 | `tmp/all_facility_master_template_ocr_validation_all16_supplemental_after_helper/step_contact_sheets/step1_all_facilities_contact_chunk_3.png` | OK: accepted four-point quads contain the FAX table |
| step1 | 4 | `tmp/all_facility_master_template_ocr_validation_all16_supplemental_after_helper/step_contact_sheets/step1_all_facilities_contact_chunk_4.png` | OK: accepted four-point quads contain the FAX table |
| step2 | 1 | `tmp/all_facility_master_template_ocr_validation_all16_supplemental_after_helper/step_contact_sheets/step2_all_facilities_contact_chunk_1.png` | OK: rectified FAX pages keep the table inside the expected outer grid |
| step2 | 2 | `tmp/all_facility_master_template_ocr_validation_all16_supplemental_after_helper/step_contact_sheets/step2_all_facilities_contact_chunk_2.png` | OK: rectified FAX pages keep the table inside the expected outer grid |
| step2 | 3 | `tmp/all_facility_master_template_ocr_validation_all16_supplemental_after_helper/step_contact_sheets/step2_all_facilities_contact_chunk_3.png` | OK: rectified FAX pages keep the table inside the expected outer grid |
| step2 | 4 | `tmp/all_facility_master_template_ocr_validation_all16_supplemental_after_helper/step_contact_sheets/step2_all_facilities_contact_chunk_4.png` | OK: rectified FAX pages keep the table inside the expected outer grid |
| step3 | 1 | `tmp/all_facility_master_template_ocr_validation_all16_supplemental_after_helper/step_contact_sheets/step3_all_facilities_contact_chunk_1.png` | OK: extracted line overlays cover the table body without visible one-row header drift |
| step3 | 2 | `tmp/all_facility_master_template_ocr_validation_all16_supplemental_after_helper/step_contact_sheets/step3_all_facilities_contact_chunk_2.png` | OK: extracted line overlays cover the table body without visible one-row header drift |
| step3 | 3 | `tmp/all_facility_master_template_ocr_validation_all16_supplemental_after_helper/step_contact_sheets/step3_all_facilities_contact_chunk_3.png` | OK: extracted line overlays cover the table body without visible one-row header drift |
| step3 | 4 | `tmp/all_facility_master_template_ocr_validation_all16_supplemental_after_helper/step_contact_sheets/step3_all_facilities_contact_chunk_4.png` | OK: extracted line overlays cover the table body without visible one-row header drift |
| step4 | 1 | `tmp/all_facility_master_template_ocr_validation_all16_supplemental_after_helper/step_contact_sheets/step4_all_facilities_contact_chunk_1.png` | OK: selected axis match lines are aligned to the FAX table |
| step4 | 2 | `tmp/all_facility_master_template_ocr_validation_all16_supplemental_after_helper/step_contact_sheets/step4_all_facilities_contact_chunk_2.png` | OK: selected axis match lines are aligned to the FAX table |
| step4 | 3 | `tmp/all_facility_master_template_ocr_validation_all16_supplemental_after_helper/step_contact_sheets/step4_all_facilities_contact_chunk_3.png` | OK: selected axis match lines are aligned to the FAX table |
| step4 | 4 | `tmp/all_facility_master_template_ocr_validation_all16_supplemental_after_helper/step_contact_sheets/step4_all_facilities_contact_chunk_4.png` | OK: selected axis match lines are aligned to the FAX table |
| step5 | 1 | `tmp/all_facility_master_template_ocr_validation_all16_supplemental_after_helper/step_contact_sheets/step5_all_facilities_contact_chunk_1.png` | OK: merge-aware grid remains on the table and does not visibly escape into table-external areas |
| step5 | 2 | `tmp/all_facility_master_template_ocr_validation_all16_supplemental_after_helper/step_contact_sheets/step5_all_facilities_contact_chunk_2.png` | OK: merge-aware grid remains on the table and does not visibly escape into table-external areas |
| step5 | 3 | `tmp/all_facility_master_template_ocr_validation_all16_supplemental_after_helper/step_contact_sheets/step5_all_facilities_contact_chunk_3.png` | OK: merge-aware grid remains on the table and does not visibly escape into table-external areas |
| step5 | 4 | `tmp/all_facility_master_template_ocr_validation_all16_supplemental_after_helper/step_contact_sheets/step5_all_facilities_contact_chunk_4.png` | OK: merge-aware grid remains on the table and does not visibly escape into table-external areas |
| step6 | 1 | `tmp/all_facility_master_template_ocr_validation_all16_supplemental_after_helper/step_contact_sheets/step6_all_facilities_contact_chunk_1.png` | OK: red target points are present through expected quantity columns and lower blank rows |
| step6 | 2 | `tmp/all_facility_master_template_ocr_validation_all16_supplemental_after_helper/step_contact_sheets/step6_all_facilities_contact_chunk_2.png` | OK: red target points are present through expected quantity columns and lower blank rows |
| step6 | 3 | `tmp/all_facility_master_template_ocr_validation_all16_supplemental_after_helper/step_contact_sheets/step6_all_facilities_contact_chunk_3.png` | OK: red target points are present through expected quantity columns and lower blank rows |
| step6 | 4 | `tmp/all_facility_master_template_ocr_validation_all16_supplemental_after_helper/step_contact_sheets/step6_all_facilities_contact_chunk_4.png` | OK: red target points are present through expected quantity columns and lower blank rows |

Parent visual conclusion:

- step1: accepted four-point quads contain the FAX table
- step2: rectified FAX pages keep the table inside the expected outer grid
- step3: extracted line overlays cover the table body without obvious one-row
  header drift
- step4: selected axis match lines are aligned to the FAX table across normal,
  two-level-header, and merged-header examples
- step5: merge-aware grid remains on the table and does not visibly escape into
  table-external areas
- step6: red target points are present through the expected quantity columns,
  including lower blank rows; the previous bottom-row target truncation class is
  not visible in the reviewed contact sheets

Sub-agent visual conclusion:

- visual sub-agent inspected facility-template chunks, step1-step6 contact
  sheets, and the best-method overlay preview
- sub-agent returned OK
- sub-agent reported no catastrophic drift in step3/step4/step5/step6, and
  target points through expected quantity and lower blank rows

## Equality Against Previous Accepted Local Output

Previous accepted local directory:

`tmp/all_facility_master_template_ocr_validation_all16_supplemental_after_untruncate`

Current local directory:

`tmp/all_facility_master_template_ocr_validation_all16_supplemental_after_helper`

Comparison result:

- step contact sheets: `24/24` exact-match
- best-method preview image: exact-match
- best-method preview sha256:
  `fae8148798dff40597197c60948bb09f06960ff1ce18f5d684f78cbe859e8faa`

## Tests

Command:

```bash
PYTHONPATH=backend backend/.venv/bin/pytest backend/tests/unit/test_hakodate_best_method_runtime_regions.py backend/tests/unit/test_workbook_pdf_renderer.py backend/tests/unit/test_master_order_form_template_service.py backend/tests/unit/test_config_service.py -q
```

Result:

```text
17 passed
```

## STG Deploy Verification

Final stg backend image:

`asia-northeast2-docker.pkg.dev/sawahospitalsystem/backend/backend:stg-backend-20260508-062244`

Cloud Run worker revision:

`worker-stg-00329-mxn`

Traffic:

`worker-stg-00329-mxn 100%`

Deploy task result:

- worker workflow-v2 API check passed
- web proxy workflow-v2 parity check passed
- system predeploy checks passed
- non-blocking `ocr_reparse_quality.gate.status insufficient_data` warning was
  reported by the existing deploy gate

Postdeploy API spot check:

- URL: `https://web-stg-avlnzjjrca-dt.a.run.app/api/orders/ORDb6f4d715/workflow-v2`
- HTTP status: `200`
- state: `confirmed`
- facility_id: `FAC00014`
- week_start: `2026-04-26`
- template_id: `fax_layout_regular_staff_daycare_v1`
- blockers: `[]`

## Deploy Source Hygiene

Predeploy / postdeploy source checks:

- deploy worktree branch: `develop`
- deploy HEAD equals local `develop` HEAD:
  `59283f25aeb73f9ab1988e5b533b050074620387`
- deploy worktree status: clean
- `develop` contains the deploy HEAD
- `worker-stg` is running the image built from this final HEAD

Known non-source caveat:

- old dirty sibling worktrees exist on disk, but they were not used as deploy
  source and did not contain unmerged current master-template or Hakodate OCR
  path fixes for this deployment
