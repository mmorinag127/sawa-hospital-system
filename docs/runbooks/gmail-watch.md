# Gmail Watch Runbook

1. Obtain necessary OAuth credentials/consent and refresh tokens (manual).
2. Configure Cloud Scheduler job to call the watch-refresh endpoint at least every 7 days (recommended daily).
3. Store tokens in Secret Manager (do not commit to state or logs).
4. Log success/failure and expiration; set alerting for failures.
5. Re-run watch if expiration approaches to avoid notification stop.

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
