# J1 retired upload backfill: source only

Base: `cfedb6ea72735bd6a2b5307699e5a9c35eb90503`.
Branch: `codex/modernization-j1-backfill-stop-20261008`.

Failure class: legacy job recovery guesses original hash/date/owner/status/current links without an original upload receipt. The unchanged callable signature now raises `ValueError("original_uploaded_pdf_receipt_required")` unconditionally before any DB/source/time/mutation call. No success-zero return. This packet does not change terminal business-error handling or normal explicit upload/requeue behavior.

Caller census: repository-wide rg found the production definition and only an unused import in `backend/src/workers/ingest_worker.py:59`; integration test import/call and automatic-backfill tripwires at original lines 736/805 are test references. No active production call or helper import/alias/dynamic reference was found. Six removed helpers had references only inside the retired implementation. Removed imports: re and Path. IngestJob remains required by normal completion receipt checking. Worker import and automatic-backfill tripwires remain unchanged.

Normal production functions are untouched in the source diff. Unexecuted unit definitions compare every remaining nonretired function's exact source segment and AST against base, plus signature/return annotation and the single unconditional raise. Six explicit limit variants and an omitted-limit call have side-effect tripwires. Runtime parity is pending, not claimed.

The integration fixture retains its original processing job ID, payload, attempts and timestamps. Its old success contract is intentionally replaced: created >= 1, matched row present, processing status, ingest_running stage and attempt_count 1. Actual baseline contains five assert statements (the task referred to six expectations); none of these positive recovery assertions is represented as retained. New definitions assert the exact stop, unchanged UploadedPdf raw count, payload/status/attempts/timestamps. All other test functions and assertions remain unchanged. Fixture is synthetic component data, not PDF/live proof.

Total/nonblank lines: service 944/871 -> 742/681; integration 929/834 -> 938/842; new unit definitions 58/43.

Source SHA256:
- service: `eb261a31559d5eab50d3c60c2a16b0fe5223aa5473a0d4683b2825c1a67b02a7`
- integration: `bdd812a41584e343de90734370a4ff94b5cecc135168529b2bc68b850af1c75c`
- unit definitions: `245547e6a03827a6996268e4be9bc4bd991544b616034c69c1d2d81e923239d4`

Commands: git rev-parse HEAD; git branch --show-current; rg caller/import/helper census; cat/sed source and AGENTS; git show HEAD:path; git diff; shasum -a 256; git add exact four allowed files; git diff --cached --binary | shasum -a 256; git status --short. Source reads/hashes only, no runtime imports or tests. Initial census naming nonexistent root tests directory exited 2; corrected repository census exited 0. No failed test run exists because no tests were executed.

All consolidated unit/integration/normal upload/requeue/lease/completion/source-receipt/sibling stop and live verification remains pending, not waived. No DB/migration/backfill/network/API/browser/server/install/generation/commit/deploy occurred. Source freeze is not root or whole-request completion; parent independent monitors and integration are required. Patch hash is reported externally, not embedded self-referentially here.
