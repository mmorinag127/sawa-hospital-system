# Gmail Watch Runbook

1. Obtain necessary OAuth credentials/consent and refresh tokens (manual).
2. Configure Cloud Scheduler job to call the watch-refresh endpoint at least every 7 days (recommended daily).
3. Store tokens in Secret Manager (do not commit to state or logs).
4. Log success/failure and expiration; set alerting for failures.
5. Re-run watch if expiration approaches to avoid notification stop.

## Troubleshooting
- `invalid_grant` persists after updating `gmail-refresh-token` in Secret Manager:
  Cloud Run reads secret-backed env vars at process start. Create a new revision (redeploy `worker-*`) so it picks up the latest secret version, then re-run `/watch-refresh`.
- Automatic recovery attempt:
  `OPERATOR_USER=admin OPERATOR_PASSWORD=****** task recover_prod_gmail_watch`
  - This probes known secret versions (`gmail-client-id`, `gmail-client-secret`, `web-client-1-secret`, `gmail-refresh-token`) against Google's token endpoint.
  - If a valid combination exists, it promotes values to latest, redeploys `worker-prod`, and triggers `/watch-refresh`.
  - If no valid combination exists, manual OAuth re-consent and new refresh token issuance is required.
- Cloud Scheduler `gmail-scan-*` keeps returning `401 Unauthorized`:
  The endpoint requires admin auth and validates Cloud Scheduler OIDC tokens by audience URL. Ensure the app trusts reverse-proxy headers (Uvicorn `--proxy-headers --forwarded-allow-ips=*`) so `request.url` is the public HTTPS URL, and ensure Cloud Scheduler can mint OIDC tokens for the worker service account (TokenCreator).

## Required env vars (Cloud Run worker)
- GMAIL_CLIENT_ID
- GMAIL_CLIENT_SECRET
- GMAIL_REFRESH_TOKEN
- GMAIL_WATCH_TOPIC (full Pub/Sub topic name)
- RAW_BUCKET (GCS bucket for incoming PDFs)

## Optional env vars
- GMAIL_WATCH_USER (default: `me`)
- GMAIL_WATCH_LABEL_IDS (comma-separated)
- GMAIL_WATCH_LABEL_FILTER_ACTION (default: `include`)
- GMAIL_WATCH_INCLUDE_SPAM_TRASH (`true`/`false`)
- GMAIL_WATCH_SCOPES (default: `https://www.googleapis.com/auth/gmail.modify`)
- GMAIL_INGEST_QUERY (default: `is:unread has:attachment`)
- GMAIL_INGEST_LABEL_IDS (comma-separated)
- GMAIL_INGEST_MAX_RESULTS (default: `10`)
- GMAIL_INGEST_MARK_READ (`true`/`false`, default: `true`)
- GMAIL_INGEST_PREFIX (default: `gmail`)
- GMAIL_WATCH_STATE_URI (default: `gs://$RAW_BUCKET/gmail/watch_state.json`)
- GMAIL_WATCH_STATE_BUCKET (fallback if `GMAIL_WATCH_STATE_URI` unset)
- GMAIL_WATCH_STATE_OBJECT (default: `gmail/watch_state.json`)
