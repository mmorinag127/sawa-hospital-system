# Hospital callback/worker receipt contract: source only

Base: 964d5cf438b244134326b5a4061827aff2f4de1e.
Branch: codex/modernization-hospital-receipt-contract-20261008.
WT: /Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-receipt-contract.
Exactly three files: the two named unit test files and this document.
No production/dependency/auth/database schema edits; no test/runtime execution.

## Root and Lineage

The old tests expect callback completion even after an empty return or crash.
Their last source change was e4788eb83e43a08e1ef74963131fde5d856a5a6c.
7cd6588ccf5efda7fbd322bcb6fa601057befe57 changed the shared execution contract:
delivery claim owns execution, reparse_order persists its own terminal result,
callback returns the exact receipt or propagates rejection, worker ACK checks
the persisted business outcome. That commit is an ancestor of both the failed
batch source 6e8afe79ff142deaa77868b7fd611cd4294c0735 and this base (read-only
git merge-base checks returned zero during the preceding triage).

Historical batch evidence is untouched:
/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-backend-isolated/tmp/consolidated-backend-20261008/logs/pytest.log.
It exited 2 at the test-authored KeyboardInterrupt("crash") at line 395.
This packet does not replace any failure log or claim that batch passed.

## Original Assertion Mapping

All five parameterized cases remain, with original order ORD-callback-contract,
job OCR-captured-job, revision 17, attempt attempt-captured, prompt and own
evidence IDs. These values are checked at service entry and applicable receipt
or completion boundaries.

- Success: the service double now supplies an explicit own done receipt.
  Assert exact returned receipt/object identity and every captured field.
  The obsolete one-completion expectation becomes zero double-completions.
- Current error: supply the exact own failed receipt with OEV-current-error
  and current_reparse_error. Assert exact return and zero double-completions.
  These receipt doubles model the component contract, not actual persistence.
- Superseded: keep OEV-superseded and WORKFLOW_REVISION_CONFLICT. The fixture
  now returns that explicit error so the current error-only persistence path
  is exercised; the refusal double still returns no receipt. Require propagated
  ValueError and the original single completion's captured fields/evidence.
  Old normal-return/silent-refusal behavior is intentionally not retained.
- Empty result: keep {} and no own evidence; require
  reparse_persisted_result_missing and zero completions. The obsolete synthetic
  reparse_evidence_missing completion expectation is explicitly replaced.
- Crash: retain KeyboardInterrupt("crash"), use pytest.raises and assert the
  same exception object, one service invocation and zero completions. No
  reparse_crashed synthetic terminal outcome or BaseException swallowing.

The old two-call source count becomes AST proof of one error-only persister,
receipt owner/status guard before direct receipt return, missing-result stop,
refusal propagation, captured keyword forwarding and no catch in callback.
The existing no-get_latest_ocr_evidence_run guard and neighboring service/cache
source assertions remain. Five additional wrong order/job/revision/attempt/status
cases reject receipts without completion or latest-evidence substitution.

## Worker and Persisted-State Definitions

Three new crash/missing/refusal cases run the real worker endpoint,
_execute_claim, callback and claim_context with explicitly doubled auth,
claim acquisition and mutex. They check same captured fields, non-ACK,
ContextVar restoration, mutex release and propagated exact error. The DB claim
row is an explicitly synthetic unit fixture, not validated capture ownership.
No master config/facility/week is guessed and no completion success is faked.

The existing isolated_formal_schema fixture performs real Alembic migration
and catalogue validation in an owned database when the parent runs the tests.
Independent Sessions compare persisted job/delivery identity, revision,
attempt, generation, claim token, lease, state, receipts and request metadata
before/after each negative unit boundary call. This proves only the defined
persisted no-write property, not real claim acquisition, source capture or OCR.

An additional formal-DB definition exercises real _business_status with saved
foreign evidence OEV-foreign versus job-owned OEV-success. It requires hard_stop
and leaves the claimed state and both evidence IDs intact. It is a saved-state
decision test, not authenticated ACK/source-registration acceptance.
Existing rollback/savepoint, refusal, pending, auth and transport tests remain.

No production invariant gap was established by this source review. Actual
transactional receipt adoption, valid captured delivery, expired-lease retry,
real transport/auth/OCR and full batch regression remain parent verification.

## Source Evidence and Commands

Read-only git status/rev-parse/branch, cat/sed/rg of tests/shared production and
backend/conftest.py; manual apply_patch; shasum -a 256, awk line counts;
explicit git add; git diff --name-only; git diff --cached --binary | shasum -a 256.
No Python/Node imports, tests, build, lint, DB/API/network, server/browser,
install, commit/push/merge/deploy or other-chat action was executed.
No owned long-running process was started.

Total/nonblank base -> current:
- test_order_workflow_revision_contract.py: 464/364 -> 552/450.
- test_workflow_ocr_delivery_runner_contract.py: 314/267 -> 492/433.

SHA256:
- backend/tests/unit/test_order_workflow_revision_contract.py:
  820f8ca432dc20520cff76aa6b05a0b73000c1dc7c3b3e4b3cc20714e80d131e
- backend/tests/unit/test_workflow_ocr_delivery_runner_contract.py:
  ba56060cc141bd287fe21f32e1751e6edd7e9c6470ce954a1cf40c9bcc5871d0

Document/patch hashes are reported externally to avoid self-reference.
All definitions are UNEXECUTED. Source freeze is not technical/live acceptance;
parent independent review, monitors, integration and isolated execution pending.
