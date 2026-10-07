# Hospital lifecycle fixture migration

## Authorized Own Advance Root Repair (Current)

Base/HEAD: `3b0cecdda1f95a35f49b31f13504d05b6aedbca8`, named branch
`codex/modernization-v1-hospital-lifecycle-20261007`. Verification uses this base
plus the dirty sources listed below, not an integrated or committed SHA.

The authorized production change adds `session.flush([job])` after the validated
own job order_id/nonworkflow_execution update in advance_nonworkflow_order_input,
before successor preparation and subsequent strict adoption reads. The existing
initial flush is unchanged. The new flush is targeted to the own OcrJob (no model
relationships); global autoflush, transaction commits, locks, snapshots and fresh
ownership comparison remain unchanged. The existing before_flush listener still
runs. No other production file was changed.

Both single restart/force cases now observe the actual imported advance call,
call the real function, then read OcrJob columns through the same transaction's
connection (not the ORM identity map). They assert the persisted boundary equals
the own capture and original start_orders/PDF receipt remain immutable across
the advance. Existing generation/digest/attempt history and old-claim rejection
assertions are retained. No second requeue, fabricated evidence or terminal data.

Focused result: **28 passed**, 179 warnings, 15.41s, exit0. Includes restart/force
success and failure/budget siblings, duplicate input, terminal stop, stale claims,
async waiting, handoff, invalid source and concurrent duplicate claims. Provider
doubles are component-only; this is not operator/live or integrated acceptance.
Parent integrated verification remains required. No commit/merge/push/deploy.

Exact command from this WT:

```sh
env -i HOME=/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-v1-lifecycle/tmp/lifecycle-home TMPDIR=/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-v1-lifecycle/tmp/lifecycle-temp PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 DB_URI=sqlite:////Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-v1-lifecycle/tmp/lifecycle-root-r2-bootstrap.sqlite PYTHONPATH=backend /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python -m pytest backend/tests/unit/test_j1_ingest_lifecycle.py -q --tb=short -p no:cacheprovider --junitxml=tmp/lifecycle-root-r2-results.xml > tmp/lifecycle-root-r2.log 2>&1
```

SHA256 of generating dirty sources and owned artifacts (paths relative to WT):

| Path | SHA256 |
| --- | --- |
| backend/src/services/workflow_attempt_guard_service.py | 6f64d32eb5de428eacb1a1dc80f75c5bd378e39115b43e4badf3f4f68f2793ea |
| backend/tests/unit/test_j1_ingest_lifecycle.py | 1619d67b330e1cf1b2e054fb2c01bd6dff4e6a8953903e7d07bd26243458ee9a |
| backend/tests/helpers/ingest_original.py | 3cc162ad57609edb46048cfe929e7a1be9f50f8eea7db7b149955f7d5a7a517a |
| tmp/lifecycle-root-r2.log | 6fb36780a1f98786a3654bf725ad97fd03dda11ca8852055b29bc9d3e8946c94 |
| tmp/lifecycle-root-r2-results.xml | a555564004fe615ce020accf92161c8dd473c68d1612a18628b4626782d4cc9d |
| tmp/lifecycle-root-r1.log | da812d15d59402b7df9606e44bcafec086c7652353a7be55395980251faa5087 |
| tmp/lifecycle-root-r1-results.xml | 3408c71257c9dcf08184cef3d226e418162a8a698be6dbaa73afda33cccbaf06 |

Historical root-r1 used the same command with r1 artifact/bootstrap names.
It exited1 with 26pass/2fail because the added observer patched the defining
module rather than the actual imported call alias; real runner business_done
already succeeded but the observer saw no calls. The observer was corrected to
the actual call alias for r2 without replacing production behavior. All earlier
2-failure traces and the superseded double-restart results below are historical,
not current evidence. No required execution sessions remain running.

## Single Restart Monitor Correction (Historical, Before Root Repair)

### Proven Shared Session Root Investigation

Read-only runtime tracing of the unchanged authoritative guard and own advance
proves both restart/force failures are source Session retention defects, not a
legitimate stale source refusal. Failure class: owned advance lost to an
unflushed populate_existing refresh. Shared decision: advance_nonworkflow_order_input
then lock_actual_write_ownership within evidence persistence. Invariant: own
validated order/job/capture advance must persist in the same locked transaction
before strict guard rereads; immutable start_orders/PDF/template proof must not
be replaced and stale owners must still reject.

Actual source: src.db SessionLocal autoflush=False. advance_nonworkflow_order_input
(guard service lines540-559) flushes before updating job.order_id and metrics,
but does not flush the newly assigned own advance. lock_actual_write_ownership
line759 uses session.get(OcrJob, populate_existing=True); this reloads old DB
order_id=None and old nonworkflow_execution into the dirty identity object.
Its strict comparison then returns False (trace line761); evidence returns None.
Two traces show advance job dirty=True with own order_id equal boundary_order and
stored_object_order, then guard job_order_id becomes [None, own order]. Physical
order vs captured input differences are empty; stored DB still has processing
document, zero lines and old lines_updated_at while owned boundary has the actual
successful write. No changed config/PDF/template source explains this refusal.

Smallest complete root change needed (not implemented): persist validated own
job/capture advancement in advance_nonworkflow_order_input before any subsequent
populate_existing ownership refresh, within its existing Session/transaction,
after successor preparation if applicable. Retain real before_flush ownership
checks and full fresh comparison; do not remove populate_existing, return True,
fabricate receipts or commit early. Regression must cover both single restart/
force successes, failure/budget siblings, actual own advance + fresh reread,
stale owner/capture rejection and rollback. Production scope remains unedited
pending parent root review/authorization.

Ignored diagnostic plugin tmp/lifecycle_guard_trace.py uses sys.settrace only;
does not replace guard results, flush, mutate metrics or mock ownership. Repeated
same scoped -k old_attempt command adds PYTHONPATH=backend:tmp and
-p lifecycle_guard_trace, unique trace-r2 bootstrap/log/XML. Actual exit1,
2 failed, 26 deselected; not a passing acceptance run. SHA256:
plugin `e9fbd4cce9bfa46c686d275796d430b40c79bc10b7ff273823030b48e65005c7`;
log `14e54d53d64e35cf889a72795e2e8c6d07665b6da1d959bdbcbacb96c7c666b0`;
XML `2e427c29bf7e7967f718b081db3120105a4daee043408082195fdc8ba82b06d1`.
Both diagnostic attempts/logs retained. No production/test source changed in
this investigation; only this report is updated. Independent 26-pass result
below remains component evidence, not completion.

Base remains `3b0cecdda1f95a35f49b31f13504d05b6aedbca8`, dirty dedicated source.
Earlier 28-pass results below are historical and superseded: those fixtures
called restart/force and then uploaded requeue, creating a second generation.
They are not proof of single explicit restart/force execution.

The two named restart/force tests now use original payload without uploaded_pdf_id,
retaining the physical original PDF/digest row needed by real receipt capture.
Actual atomic intent, claim, registration and archived selected template are used;
no physical state repair, fake terminal result or second requeue is performed.
Assert selected restart generation stays exact through owned execution; failure
budget siblings assert generation_attempts=1, unchanged digest, actual provider
double call count every generation, lifetime attempts and stale recovery history.
Uploaded requeue remains separately covered by its own original lease test.

Current actual result: **26 passed, 2 failed**, 179 warnings, 16.79s, exit1.
Failures are restart and force variants of
test_old_attempt_cannot_finish_fail_or_capture_new_attempt. Real initial pending
provider handoff succeeds; actual new-generation normal execution reaches
worker._process_ingest_captured -> order_service.create_order_from_ingest:5087 ->
_persist_ingest_pipeline_evidence:4815 and raises first_pass_own_evidence_persist_failed.
The actual evidence call returns no accepted record; assertions are retained,
not replaced with expected failure or skipped. Source ownership checks compare
job_order_id/captured/stored boundaries. The shared Session root is now proven
by the trace investigation above: populate_existing reloads unflushed owned
advance fields while autoflush is disabled. Physical input matches the capture;
this is not a legitimate stale-proof refusal. Production correction requires
parent review/authorization; no ownership check was weakened. No production edit.
This blocker prevents the requested single-restart success proof; independent
failure/budget and remaining lifecycle cases were executed, not abandoned.

Historical correction attempts retained: r1 2pass/2fail (missing physical PDF);
r2 4fail (source failures exposed, not actual provider); r3 one diagnostic fail
showing selected_pdf_digest_required; r4 2pass/2fail after physical PDF retained.
No missing receipt was guessed or current config substituted.

Exact final command from this dedicated WT:

```sh
env -i HOME=/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-v1-lifecycle/tmp/lifecycle-home TMPDIR=/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-v1-lifecycle/tmp/lifecycle-temp PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 DB_URI=sqlite:////Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-v1-lifecycle/tmp/lifecycle-single-final-bootstrap.sqlite PYTHONPATH=backend /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python -m pytest backend/tests/unit/test_j1_ingest_lifecycle.py -q --tb=short -p no:cacheprovider --junitxml=tmp/lifecycle-single-final-results.xml > tmp/lifecycle-single-final.log 2>&1
```

Earlier scoped commands used the same closed env with unique single/r2/r3/r4
bootstrap/log/XML paths and -k 'old_attempt or manual_new_generation' (r3 only
'old_attempt and restart'). Formal per-case databases are owned tmp outputs.
No SDK/provider network or other-worktree writes. Actual own-source SHA256:
- lifecycle test: `473412398a4696450a2f7cd19b98ca9fcec9cfac51f097b7b95d2af4435b96b4`
- shared helper: `3cc162ad57609edb46048cfe929e7a1be9f50f8eea7db7b149955f7d5a7a517a`
- tmp/lifecycle-single-final.log: `abea0d415c0d0f225f2d4580ef562e83d2b6e72f1bf2f231a358359b2c76434f`
- tmp/lifecycle-single-final-results.xml: `e39248ecae6857b399da69dae031b38f0a7372c65a217735c3e0afdc1196a707`

Source frozen with known failed normal branches for parent root review, NOT OK
for successful acceptance/commit. No running needed session remains. Existing
staged helper migration retained; only lifecycle/doc changed in this correction.

Start SHA: `3b0cecdda1f95a35f49b31f13504d05b6aedbca8`.
Branch: `codex/modernization-v1-hospital-lifecycle-20261007`.
Worktree: `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-v1-lifecycle`.

## Scope and invariant

Failure class: old component fixtures omitted atomic original delivery config,
formal schema and common session bindings, then fabricated execution/terminal
receipts or invoked unsupported direct workers. Fixtures now use
`isolated_formal_schema`, `src.db.session_scope`, explicit case-local transport
configuration and authored facility/menu/date/PDF sources. The shared original
helper registers explicit inputs through actual registration/lineage APIs.
No production edits, `create_all` or provider network.

Positive cases execute `ingest_delivery_service.process` using original intents
created atomically by `create_ingest_receipt`. External OCR is a component double;
capture, pipeline delivery persistence, terminal producers, handoff and ACK are
real APIs. This is not live provider or external task-runner coverage. Narrow
lease/capture rejection units are not presented as end-to-end runner proof.

The explicit continuation resolved stale-reset expectations: automatic recovery
retains business generation, input digest and claim history; exhausted budget
does not reset. Only explicit operator restart/force/requeue creates a new
generation. Uploaded-PDF requeue itself creates a new original generation;
tests read the same known job ID after that operation, not a superseded receipt.
Lifetime attempts never rewind. Exhausted direct entrances retain budget three;
the uploaded exhausted case retains budget one.

Coverage retains duplicate immutable input in processing/done/error states,
terminal failure, four exhausted-budget entrances, manual restart/force,
old ingest/upload lease fencing, asynchronous waiting/handoff, concurrent
duplicate execution and changed-execution refusal. Interrupted ACK tests
interrupt only acknowledgement after actual producer persistence; `settle_saved`
reconciles without recognition or extra business attempts. Missing week stops
at the real payload builder, and the unsupported wrapper refuses missing intent
without mutation. Its positive sibling uses the explicit authored week, not
inference. Old claims become obsolete only after explicit manual restart.

## Actual verification

Historical first fixture-only run: `tmp/lifecycle-results.xml`, 5 passed,
23 failed, 179 warnings in 7.37 seconds; not current acceptance evidence.
An initial bootstrap attempt without owned tmp directories stopped before cases
with `unable to open database file`.

Focused results after migration:

- `tmp/lifecycle-restart-r2-results.xml`: 4 passed, 24 deselected, 4.67 seconds.
- `tmp/lifecycle-terminal-neighbors-results.xml`: 3 passed, 25 deselected, 2.36 seconds.
- `tmp/lifecycle-ack-results.xml`: historical 2 passed, 1 failed; a test read a
  budget field absent from the actual lease. Now compared to actual snapshot.
- `tmp/lifecycle-stops-results.xml`: historical 4 passed, 2 failed; missing week
  correctly stopped before intent construction and preserved original claim
  was not obsolete until manual restart. Tests now distinguish those boundaries.

Full focused-file command, cwd this worktree:

```sh
env -i HOME="$PWD/tmp/lifecycle-home" TMPDIR="$PWD/tmp/lifecycle-temp" PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 DB_URI="sqlite:///$PWD/tmp/lifecycle-bootstrap.sqlite" PYTHONPATH=backend /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python -m pytest backend/tests/unit/test_j1_ingest_lifecycle.py -q --tb=short -p no:cacheprovider --junitxml=tmp/lifecycle-final-results.xml
```

Actual result: **28 passed**, 178 warnings, 15.34 seconds.
XML: `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-v1-lifecycle/tmp/lifecycle-final-results.xml`.
Warnings: existing expression-index reflection and SWIG deprecations.
No skips/xfails or fabricated execution/terminal receipts.

After removing unused imports and correcting the module description, the same
command with `--junitxml=tmp/lifecycle-freeze-results.xml` was repeated:
**28 passed**, 178 warnings, 15.56 seconds, exit 0. This is the final source-freeze
test receipt, not an integrated main receipt.

## Handoff

Changed only lifecycle test, necessary original helper and this new document.
No commit/merge/push/deploy, other-worktree writes or production changes.
Source frozen for main review/integration. Integrated original/neighbor/stop
acceptance remains pending in main; component success is not broad completion.
