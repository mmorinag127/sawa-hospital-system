# Hospital Agent Memory And Recovery

## Contract

- Fix memory exhaustion and restart recovery without changing recognition values.
- Finish all known implementation changes before serial local batch verification.
- Deploy to staging through Actions and verify all six private originals through
  OCR, sheet generation, save and reload.
- No production operations, budget changes, IAM additions or history overwrite.

## Findings

- Staging run `0dfe4c133d8042c6a667983d7b9665ab` exceeded 8704 MiB at
  8762 MiB on 2026-11-11 01:47:52 UTC; Cloud Run restarted the instance.
- Request-local transport started a new thread for every accepted delivery.
  All six native OCR jobs were running and claimed, with no terminal receipt.
- Its durable working copy contains 519912090 bytes, including six approximately
  75 MB intermediate template overlays. Cloud Run writable storage consumes RAM.
- `wait_for_fax_work` is a read-only queue wait, but Recovery omitted it from
  its recoverable waits, unlike native reconciliation. It was the only pending
  command, not an unknown mutation.

## Implementation Checklist

- [x] Bound native delivery execution before image allocation; preserve FIFO,
  context, duplicate claims, receipts and normal business execution fences.
- [x] Avoid copying immutable packaged model blobs into request RAM storage.
  Preserve private writable paths and deny writes to the shared image cache.
- [x] Recover the exact read-only wait across both providers without resending
  model input, changing generation fences or replaying unknown mutations.
- [x] Restore pending local native deliveries only from owned saved contracts,
  retaining original job/generation/input identities and completed receipts.
- [ ] Add all regression cases, then run a single serial local batch.
- [ ] Integrate develop, deploy staging once and verify six original copies.

## Status

Implementation and regression additions are finished. Serial batch verification
is next; no tests or deployments run for this change yet. Production `fc76bc51`
and its cleared selections remain untouched.

The restart transport ID is now independent of the rotating HTTP access token.
Existing checkpoints adopt their one matching saved delivery ID; ambiguous or
foreign contracts are refused. A process-owned file lock excludes surviving
native workers. Recovery redelivers only original ingest/workflow worker IDs;
native generation, input, lease and terminal checks remain authoritative.
Unknown provider mutations remain blocked. Peak native process RSS is recorded
in completed local-delivery receipts for live verification.
