# OCR Setup

1. Install Python deps: `pip install -r backend/requirements.txt`.
2. Validate install:
   - `python -c "import pdfplumber; print('ok')"`
3. Performance tuning:
   - Use retries=3 (default) from env.
   - Prefer exact region extraction via pdfplumber before OCR full-page.
4. Container builds: ensure OS packages include `poppler-utils` for PDF rendering.

## Facility OCR Pipeline (Template + ROI)

1. Register facility templates in `backend/src/data/fax_templates.yaml` (match/warp/rois/postprocess).
2. Point facilities to templates via `fax_template_id` in the facility master.
3. Ensure OCR pipeline is configured for ROI extraction:
   - `OCR_MAIN_PROVIDER=pipeline` (or omit to use default)
   - `OCR_PIPELINE_BUCKET` and `OCR_PIPELINE_URL` configured
4. Artifact/output storage:
   - `OCR_ARTIFACT_DIR` controls where OCR outputs and unclassified inputs are written.
