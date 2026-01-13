# Pub/Sub Push → Cloud Run (Authenticated)

1. Create push subscription targeting Cloud Run worker URL + `/pubsub/push`.
2. Use dedicated push SA; grant `roles/run.invoker` to that SA on worker service.
3. Verify with stub publish (e.g., gcloud pubsub topics publish ...); confirm worker logs receive message and no 403.
4. Keep endpoints auth-required; do not allow unauthenticated worker.
5. For ingest testing, use `task pubsub_ingest_test` (uploads a PDF and publishes JSON payload).
