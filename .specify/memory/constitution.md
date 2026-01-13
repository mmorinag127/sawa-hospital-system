<!--
Sync Impact Report
- Version change: 0.0.0 -> 1.0.0
- Modified principles: n/a -> Code Quality & Safety; Testing Discipline & Data Fidelity; User Experience Consistency; Performance & Reliability
- Added sections: Core Principles, Operational Standards, Development Workflow, Governance
- Removed sections: Placeholder principles (5), unused template sections
- Templates requiring updates: ✅ .specify/templates/plan-template.md, ✅ .specify/templates/tasks-template.md, ⚠ pending none
- Follow-up TODOs: none
-->

# hospital-order-system Constitution

## Core Principles

### I. Code Quality & Safety
Build for readability, debuggability, and configuration-first behavior. Domain logic must be
driven by facility master data (templates, mappings, policies) rather than hard-coded rules.
Error handling MUST surface actionable guidance to operators, and logging MUST capture every
ingest, edit, and configuration change with traceable IDs (FAC/WEK).
Rationale: Frequent facility-specific variance demands maintainable, auditable code paths
that can adapt without deployments.

### II. Testing Discipline & Data Fidelity
Contract and integration tests MUST guard PDF ingestion, facility resolution, menu mapping,
and output generation (label CSV, delivery note Excel, manufacturing totals). Tests MUST
cover OCR retry logic, duplicate facility-week replacement, zero-quantity suppression, and
change-column precedence. Red/green refactors are required before feature completion.
Rationale: Data correctness is critical when OCR is imperfect; regressions directly impact
production labels and invoices.

### III. User Experience Consistency
The operator UX MUST be consistent: PC browser only, PDF viewer always available, inline
edits deferred until a single “確定” commit, and status vocabulary limited to 未着/要確認/
確定/エラー. Filters and error cues must take operators directly to the fix location.
Rationale: Operators rely on repeatable flows to reconcile imperfect OCR quickly and safely.

### IV. Performance & Reliability
Ingestion MUST be immediate on email receipt in JST. The system MUST clear backlog after
downtime and sustain at least 100 facilities per day without manual triage. Output updates
must be triggered automatically on every確定, and failures must requeue safely without data
loss.
Rationale: Production depends on timely labels, invoices, and aggregates; lag breaks
downstream manufacturing and delivery.

## Operational Standards

- Security and auditing: Authenticate admins via Google accounts and operators via ID/password
  (expandable per plan); log all uploads, edits, and configuration changes with retention of
  at least 1–2 months.
- Configuration over code: Facility differences (templates, label rules, invoice mappings)
  MUST remain in master data to avoid code forks for new facilities.
- Data retention: Store inbound email + PDF for the configurable retention window (initial
  1–2 months) with recoverable history for disputes.
- Observability: Structured logs for ingest, OCR attempts, retries, and output generation
  must include facility/week IDs to trace issues quickly.

## Development Workflow

- Planning must document the chosen project structure and testing approach before coding.
- Every change must pass Constitution Check gates: code quality rules enforced, required
  contract/integration tests identified and executed, UX behaviors (status terms, inline
  confirmation) preserved, and performance/backlog recovery scenarios covered.
- Code review is mandatory for all changes; reviewers verify master-data-driven design,
  test coverage of ingest/output flows, and adherence to UX/performance commitments.
- Deployments require evidence of passing tests for ingest, facility resolution, output
  generation, and backlog recovery where impacted.

## Governance

- This constitution supersedes other practice docs for scope it covers.
- Amendments require consensus from project maintainers, an explicit changelog entry,
  migration/operational impact notes, and version bump per semantic rules (MAJOR for
  removals or incompatible rewrites, MINOR for new principles/sections, PATCH for clarifying
  edits).
- Compliance reviews occur during planning (Constitution Check), code review, and pre-release
  verification; violations need a documented risk acceptance and mitigation plan.
- Store ratification/amendment dates and version in this file; all downstream templates must
  stay in sync.

**Version**: 1.0.0 | **Ratified**: 2025-12-23 | **Last Amended**: 2025-12-23
