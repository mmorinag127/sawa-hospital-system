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
- [x] Preserve the original execution binding and require owner acknowledgement
  for a native-only source upgrade. Prompt, tools, model, budget and task changes
  remain refused; unresolved mutations cannot be upgraded.
- [x] Keep literal quantity OCR text editable during native initial intake;
  defer numeric order-line materialization until quantities are corrected.
  Original/document/template/date/menu identity checks remain mandatory.
- [x] Check recovery bindings synchronously before activating a model task.
- [x] Allow a new copy only after the old native copy is proven stopped at the
  current generation. Retain uncertain provider spend and block stale proofs.
- [x] Preserve low-confidence authored note text at verified cell positions;
  keep confidence metadata without treating content confidence as missing geometry.
- [x] Save every individually approved draft before final bagging/confirmation.
  Keep human approvals, native version checks and final quantity checks intact.
- [x] Do not pause model preparation when only the first FAX has a review.
  Refresh background FAX progress after a quiescent/restarted approval phase;
  retain existing reviews and continue only the remaining preparation.
- [x] Defer native HTTP mutations while the same domain has accepted local
  worker deliveries. A shared read-only idle wait releases the tool lock,
  restores only owned worker contracts and remains recoverable after restart.
  GETs and other domains remain available; unknown write responses stay blocked.

## Status

First batch at `2bf62068`: backend 215, agent 321, frontend 189 passed, types and
build passed. Staging run 42954087675 succeeded (web 00502-gvb / worker 00949-4b5).
The same interrupted wait resumed and the native recovery endpoint returned 200.
Later aggregate at `58985343`: backend 262, agent 340, frontend 239 passed,
types/build passed; staging run 42962661205 succeeded. The original run's native
upgrade retained two binding epochs and resumed provider execution. Its unknown
provider dispatch remains reserved; native generation 10 has a positive stop
receipt. No unknown dispatch was replayed or marked successful.

Fresh real-source run `3ccdd027cc7440698fb25357d9523b7a` accepted the exact six
originals with OCR enabled. Five native deliveries have returned; the maximum
recorded native-process RSS is 3870.84 MiB, not total container memory. The first
OCR is ready. Ikoi, Shimanto and Kasuga have zero skipped cells and 56 deferred
note cells each; Shonan has zero skipped cells and 54 deferred note cells. All
four failures are note-confidence projection rejection, not geometry or quantity
confidence. Fureai's terminal result is still unverified. A first-review pause
also prevented background-result collection/restart; this is fixed locally with
generation/source preflight before native restart. All known implementation
changes are finished; the aggregate local proof is next. Live six-source
completion remains required after staging deployment.
Six saved/reloaded sheets have NOT yet been verified. Production remains out of scope.

Aggregate at `516878d8`: backend 301, agent 389 and frontend 239 passed,
types/build passed. Staging run 42972664053 succeeded, web 00506-qtt and worker
00952-wj5. Trial `3697cf72ca5041c9bd013cd6e6197208` progressed through three
successful OCRs; its first proposed value changes were rejected, and the raw
unchanged review was approved. No sheet has yet been saved in this trial.

The second OCR selection returned HTTP 500 while another native delivery was
writing the same SQLite database. The frozen source selected successfully alone,
including the real artifact/checkpoint files, foreign keys, WAL and confinement.
A concurrent writer reproduced an uncaught `database is locked` on the shared
ownership UPDATE. The native child exception itself was not externally logged;
this reproduction establishes the competing-writer failure class, not a captured
live traceback. The idle gate prevents that overlap without changing SQLite's
timeout, recognition values, permissions or error/replay safety. The new wait,
read/write gate and stop/recovery cases require the next aggregate batch.
The original HTTP 500 command remains unresolved; it was not resent, declared
successful or directly modified in the ledger.

The first idle-gate deployment (`9f2a8b30`, staging run 42981967135) passed its
then-selected aggregate but omitted MCP registration for the advertised idle
helper. Trial `2338d8d7cea24ae7952a2ed8420a2678` consequently failed before the
helper command reached Tools.execute. This was an implementation/test-selection
gap, not a successful six-FAX verification. The helper is now registered through
the same MCP invoke path as both providers. The aggregate includes the existing
Gemini advertised-tool/schema parity tests, and idle-wait execution tests now
call the real local MCP bridge instead of only Tools.execute.

Trial `4b176972a5ba467ea4ffa0a1a87aa577` reached the real registered idle helper,
completed three idle waits and deferred competing writes without HTTP 500 or
provider replay. The active-wait review found that child liveness was checked
only at entry. The same read-only wait now repeats the owned startup check
outside the tool lock, and checks authorization before any restart. Tests cover
child exit within one MCP wait and revoked authorization without child start.
The live six-copy run is retained for audited native-only upgrade and recovery;
it has not been declared saved/reloaded successfully.

The same trial completed all six native OCR deliveries (maximum native-process
RSS 4,043,210,752 bytes). All six review proposals were prepared, but none were
approved or saved. An early operator correction exposed another shared recovery
gap: partial review preparation had no `business_model_wait`, yet the controller
tried to answer such a wait. The correction now pauses at a real provider tool
boundary, and its exact saved message takes priority over generic preparation.
Older checkpoints without a pause use the normal verified provider recovery,
not a fabricated tool reply or replay of unknown writes. This fix and child
liveness changes are collected before the next local batch/staging deployment.
No six-sheet save/reload success is claimed. The trial remains at $2.32 of its
unchanged $2.50 cap; old unknown provider reservations remain reserved.

The restart transport ID is now independent of the rotating HTTP access token.
Existing checkpoints adopt their one matching saved delivery ID; ambiguous or
foreign contracts are refused. A process-owned file lock excludes surviving
native workers. Recovery redelivers only original ingest/workflow worker IDs;
native generation, input, lease and terminal checks remain authoritative.
Unknown provider mutations remain blocked. Peak native process RSS is recorded
in completed local-delivery receipts for live verification.
