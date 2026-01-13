# OCR Setup (Tesseract + pdfplumber)

1. Install Tesseract with Japanese language data.
   - macOS (brew): `brew install tesseract-lang`
   - Ubuntu/Debian: `apt-get install tesseract-ocr tesseract-ocr-jpn`
2. Set `TESSDATA_PREFIX` (see backend/.env.example).
3. Install Python deps: `pip install -r backend/requirements.txt` (includes pdfplumber/pytesseract/pillow).
4. Validate install:
   - `tesseract --list-langs | grep jpn`
   - `python -c "import pdfplumber; import pytesseract; print('ok')"`  
5. Performance tuning:
   - Use retries=3 (default) from env.
   - Prefer exact region extraction via pdfplumber before OCR full-page.
6. Container builds: ensure OS packages include `libjpeg`, `libpng`, `libtiff`, `poppler-utils` for pdfplumber.

## Facility OCR Pipeline (Template + ROI)

1. Register facility templates in `backend/src/data/fax_templates.yaml` (match/warp/rois/postprocess).
2. Point facilities to templates via `fax_template_id` in the facility master.
3. Ensure OCR pipeline is configured for ROI extraction:
   - `OCR_MAIN_PROVIDER=pipeline` (or omit to use default)
   - `OCR_PIPELINE_BUCKET` and `OCR_PIPELINE_URL` configured
4. Artifact/output storage:
   - `OCR_ARTIFACT_DIR` controls where OCR outputs and unclassified inputs are written.
