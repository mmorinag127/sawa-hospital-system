# Stored workflow revision fixture calls (source only)

Base c0c46aec8cb0807907ef8a2e9a6e022ec6f5fe2b; branch
codex/modernization-hospital-revision-fixture-20261008.
Failure class: OLD_TEST_CALLER_MISSING_WORKFLOW_REVISION.

Existing evidence:
/private/tmp/sawa-mr-20261008-02/logs/results-initialized.xml.
The original full module had 118 passes and 28 failures among 146 cases.
Eight failures in three functions stopped before their intended business boundary:
test_step4_uses_adopted_candidate_not_old_order_lines (four cases),
test_step4_empty_candidate_keeps_existing_stop (two), and
test_step4_http_enrichment_error_creates_no_artifacts (two).
The first six observed workflow_revision_required; HTTP observed 428 instead
of its original 400 expectation.

The service decorator at order_workflow_v2_service.py:93-99 requires an explicit
revision. API orders.py:1332 accepts a plain integer string or quoted integer;
the bagging route passes it through at :1703. The new helper reads actual
OrderWorkflowState.revision and requires an actual stored integer >=1,
rejecting missing/invalid/zero values, never supplies
the model's default or a guessed number. Each command reads immediately before
the call; HTTP uses the actual stored revision string in If-Match.

Only four call sites in the three named functions changed, plus the strict helper.
Seed, candidate, mock shape, quantities, snapshots, expected errors/status and
assertion checks remain unchanged (the empty assertion's call receives only the
new keyword). No new case was added; original collection count remains 146.
The other 20 failure cases are untouched, including the two held same-name
dated-menu policy cases. Downstream candidate/source-receipt deficits are not
waived; none of the eight cases is claimed passing after this source-only edit.

Parent must execute the complete original module after integration using its
registered owned SQLite/PostgreSQL setup and reviewed harness:

    python -m pytest backend/tests/integration/test_menu_readonly_boundary.py

No donor test/import/database/server execution, stage, commit, merge, push or
deploy occurred. Strict helper refusal is authored in the helper itself; no extra
fixture-only mock or case was introduced. Parent full 146-case regression remains
the verification gate, not a claim that revision alone fixes downstream failures.
