# PDF Upload + Google Login Production Readiness

## Goal

Replace the legacy mail-based ingest with direct PDF upload while keeping Google login.
This removes mail-watch operational risk and keeps the current OCR/order workflow.

## Current Concrete Domain Plan

Current concrete target for this customer:

- Website: `https://sawa-food.com/`
- Production app: `https://hospital-app.sawa-food.com/`
- Development app: `https://dev-hospital-app.sawa-food.com/`

Detailed step-by-step runbook:

- [sawa_food_xserver_gcp_domain_cutover_20260318.md](/Users/mmorinag/Sawa/2025.12/workspace/docs/sawa_food_xserver_gcp_domain_cutover_20260318.md)

## Current Repo Facts

- Google login already exists in the frontend login page.
- Order creation is not fundamentally Gmail-specific.
  Existing ingest path accepts:
  - `message_id`
  - `pdf_uri`
  - `received_at`
- Existing order/OCR flow after ingest can already be reused.

Relevant code:
- [workspace/frontend/src/pages/login.tsx](/Users/mmorinag/Sawa/2025.12/workspace/frontend/src/pages/login.tsx)
- [workspace/backend/src/api/ingest.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/api/ingest.py)
- [workspace/backend/src/services/order_service.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/services/order_service.py)
- [workspace/infra/terraform/envs/prod/main.tf](/Users/mmorinag/Sawa/2025.12/workspace/infra/terraform/envs/prod/main.tf)
- [workspace/infra/terraform/envs/dev/main.tf](/Users/mmorinag/Sawa/2025.12/workspace/infra/terraform/envs/dev/main.tf)

## Why Move Off Legacy Mail Ingest

Benefits:
- Removes mail watch refresh failure risk.
- Removes mail-ingest specific verification and secret management burden.
- Removes watch/scheduler/mark-read operational complexity.
- Avoids dev/prod accidentally sharing the same inbox.
- Makes testing deterministic because uploaded PDFs are explicit inputs.

Tradeoff:
- Need upload API and upload UI.
- Need duplicate-upload handling.
- Need explicit operator workflow for intake.

## Google Login Requirements

If Google login remains and the app is used as an external production app, plan for:

- Homepage URL
- Privacy Policy URL
- Authorized domain verification

Recommended interpretation:
- For external production use, treat all three as required.
- For internal-only Google Workspace use, the process is easier, but still keep the same structure to avoid later migration pain.

### What this means in practice

- Do not keep production only on `*.a.run.app` as the long-term public face.
- Put the app on a real domain such as:
  - `app.example.com` for prod
  - `dev-app.example.com` for dev
- Host:
  - homepage
  - privacy policy
  - optional terms
  on the same verified parent domain.

## Recommended Operating Model

### Production

- Real customer/operational PDFs only.
- Google login production OAuth client only.
- Dedicated prod GCP project or at minimum fully separate prod resources.
- Direct PDF upload only.
- No legacy mail-ingest secrets configured.
- No mail-ingest scheduler dependence.

### Development

- Test PDFs only.
- Separate OAuth client from prod.
- Separate DB, buckets, Pub/Sub, service accounts, secrets.
- No production PDFs copied in by default.
- Same upload flow as prod for realistic testing.

### Optional Staging

If possible, add staging later:
- Same shape as prod
- Separate domain and OAuth client
- Synthetic or scrubbed PDFs only

## Direct PDF Upload Migration Plan

### Implemented in this workspace

Implemented now:

- Backend upload endpoint:
  - `POST /ingest/upload`
  - file field: `pdf_file`
  - optional form fields:
    - `facility_hint`
    - `week_hint`
    - `facility_name`
    - `received_at`
    - `force`
    - `skip_ocr`
- Upload persistence:
  - `RAW_BUCKET` -> GCS
  - local dev / tests -> local temporary storage fallback
- Stable duplicate key:
  - `message_id = upload:sha256:<digest>`
- Frontend upload page:
  - `/pdf-upload`
- Public pages for Google OAuth readiness:
  - `/about`
  - `/privacy`
  - `/terms`
- System status / nav now understand intake mode:
- `INGEST_MODE=manual_upload`

Relevant code:
- [workspace/backend/src/api/ingest.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/api/ingest.py)
- [workspace/backend/src/services/manual_upload_service.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/services/manual_upload_service.py)
- [workspace/backend/src/services/intake_mode_service.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/services/intake_mode_service.py)
- [workspace/frontend/src/pages/pdf-upload.tsx](/Users/mmorinag/Sawa/2025.12/workspace/frontend/src/pages/pdf-upload.tsx)
- [workspace/frontend/src/pages/about.tsx](/Users/mmorinag/Sawa/2025.12/workspace/frontend/src/pages/about.tsx)
- [workspace/frontend/src/pages/privacy.tsx](/Users/mmorinag/Sawa/2025.12/workspace/frontend/src/pages/privacy.tsx)
- [workspace/frontend/src/pages/terms.tsx](/Users/mmorinag/Sawa/2025.12/workspace/frontend/src/pages/terms.tsx)

### Reuse

Keep these existing pieces:
- order creation from ingest payload
- OCR pipeline / yomitoku
- OCR reparse flow
- OCR sheet UI
- bagging / labels / delivery-note flow

### Add

Backend:
- `POST /uploads/order-pdf` or similar
- Accept PDF file upload
- Store PDF in GCS
- Generate synthetic `message_id`
- Build ingest payload:
  - `message_id`
  - `pdf_uri`
  - `received_at`
  - optional `facility_hint`
  - optional `week_hint`
- Reuse existing ingest enqueue/order creation path

Frontend:
- Upload page or upload panel
- Drag-and-drop or file picker
- Optional:
  - facility selection
  - week selection
  - note / source tag
- Show created order link immediately

### Strongly Recommended Safeguards

- PDF-only MIME/type validation
- Max file size limit
- SHA256 duplicate detection
- Upload audit log
- Upload actor tracking
- Explicit source flag such as `source=manual_upload`

## Dev / Prod Separation Rules

Rules to fix before go-live:

- Separate Google OAuth client IDs for prod and dev.
- Separate allowed/admin email lists.
- Separate DB.
- Separate raw/artifact buckets.
- Separate service accounts.
- Separate Secret Manager values.
- Never point dev at prod secrets or prod DB.
- Never use production PDFs in dev unless intentionally scrubbed and approved.

## Minimum Implementation Scope

### Backend

1. Add upload endpoint.
2. Save uploaded PDF to GCS.
3. Reuse ingest payload format.
4. Create order.
5. Trigger OCR job.
6. Record upload metadata.

### Frontend

1. Add upload screen.
2. Require Google-authenticated operator.
3. Upload PDF.
4. Show created order and OCR state.

### Infra

1. Add upload size/config settings if needed.
2. Add public app domain.
3. Add prod/dev OAuth clients.
4. Set `INGEST_MODE=manual_upload` in prod.
5. Remove legacy mail-ingest secrets from prod once migration is complete.

## Test Checklist

### Functional

- Upload one valid PDF -> order created
- OCR starts automatically
- OCR sheet page opens correctly
- Reparse still works
- Confirm flow still works
- Labels / bags / delivery notes still build

### Safety

- Non-PDF upload rejected
- Oversized upload rejected
- Duplicate PDF detected or warned
- Unauthorized upload blocked
- Dev upload cannot affect prod data

### Regression

- Existing OCR flows still pass current OCR regression suite
- Existing known-good orders do not change without explicit rerun

## Decision Recommendation

Recommended:
- Keep Google login.
- Stop using Gmail watch for production intake.
- Move to direct PDF upload as the only intake path.
- Add homepage + privacy policy + verified domain before external production use.
- Run dev and prod with separate domains, OAuth clients, buckets, DBs, and secrets.

This is the lowest-risk route to production compared with keeping Gmail ingest.
