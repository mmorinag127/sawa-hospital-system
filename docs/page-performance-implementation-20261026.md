# Page performance implementation

## Contract

Preserve order/OCR values, canonical ownership, operator edits, authentication,
revision fences and error behavior. Reduce redundant work, not validation.
No TTL substitution, additional billing, automatic CI/deployment or local deploy.
Implement all known changes before updating tests and running one serial local
verification batch. Delivery and live verification follow that batch.

## Ordered backlog

Source investigation: /Users/mmorinag/Sawa/2025.12/tmp/page-navigation-20261026/complete-action-report.md

1. P03/P04/P05: workflow response, duplicate order request and snapshot/binding reads.
2. P06/P07/P08/P09/P10: paginated orders, OCR projections, quality/status/log reads.
3. P11/P12/P13: coherent agent reads, blocking I/O, independent UI loads and menus.
4. P14/P15/P16: school scoping, exact route geometry reuse and shift pagination.
5. P01/P02: startup dependencies and request occupancy without paid scaling.
6. Update affected tests together; serial local verification; review/integration;
   minimum legitimate staging deliveries; exact live flow and timing verification.

## Status

- PR170 remains the starting implementation; not deployed.
- Hospital: workflow order-header projection removes the duplicate detail GET;
  selected raw evidence is retained once for history and saved-sheet inspection.
  Native JSON canonical comparison avoids the normalization round trip only for
  native JSON with string keys; unusual inputs retain the original normalizer.
  The API shares the binding validation from its fresh list read, without TTL.
- Stored-fact dashboards use SQL grouping; facility navigation uses SQL paging
  and complete-scope facets/progress, separate from guarded execution reads.
  Daily navigation uses a scalar fact projection. Candidate reads cover only
  displayed rows, including the daily table.
- OCR monitoring projects terminal lineage/review rather than whole recognition
  archives. Native terminal validation uses the same metadata/lineage checks;
  write callers retain full evidence reads. Quality reads use eight values and
  six key-presence flags rather than whole metrics. Operations-only status omits
  unused quota/quality/legacy sections; counts use fewer SQL round trips.
- Logs opt into summary reads and fetch one authenticated detail on selection.
- Agent reads share per-response snapshots and bounded reusable connections.
  Blocking list reads execute off the event loop. Independent UI sections publish
  separately; completed/stopped/rolled-back work stops polling.
- Menu navigation reads the latest ID alone. Editor master lookups and recent
  menu upload/item/entry lookups are batched; revision input coverage is unchanged.
- School branch `codex/page-performance-school-20261026` at
  `worktrees/page-performance-school-20261026`: date bounds, column projections,
  facility endpoint, thread-owned scope-validation Session, exact shared wall
  intersection index and deferred imaging/workbook imports.
- Shift branch `codex/page-performance-shift-20261026` at
  `worktrees/page-performance-shift-20261026`: paginated history with older-page
  access, page-scoped counts, direct explicit history reads, lightweight period
  listing for creation/calendar and deferred solver imports in delivery reads.
- Startup gateway accepts no agent operation until full source verification,
  epoch fencing, hydrate, recovery and MCP lifespan initialization succeed.
  Ordinary web no longer depends on that restoration. Other container startup
  dependencies are preserved. No paid scaling or minimum-instance changes.
- Implementation is not live verification. Local serial checks have started;
  no Actions, deployment or budget changes have run. Review, integration,
  staging and required-data timings remain pending.
- The investigation's suggested normalized SQL ledger migration is not silently
  substituted for an implementation: the canonical ledger format is retained;
  repeated transfer/decode is reduced to coherent response reads. Whether more
  restructuring is required remains subject to the final measured read cost.

## Local verification in progress

Hospital runtime: `/private/tmp/sawa-hospital-secure-20261024/bin/python`,
`PYTHONPATH=backend`, explicit disposable SQLite URI under `/private/tmp`.

- Eight targeted backend files: 116 passed, two snapshot-test harness failures;
  after restoring the baseline's exact type annotations and deferring the import
  until after the standalone probe establishes its path, the entire snapshot
  file passed (5 tests). No production failure was hidden by changing outputs.
- New order-overview regression plus existing OCR quality integration: 7 passed.
- Frontend changed-test batch: 566 passed, no skipped tests; 75.51 seconds.
- Hospital frontend `tsc --noEmit --incremental false`: exit 0.
- Agent startup gateway: 2 passed; staging runtime/business workflow: 30 passed;
  connection reset/discard/capacity regressions: 4 passed.
- School target batch after scope factory and candidate-error boundary fixes:
  71 passed, four date-filter/SQL-contract failures. The corrected response-parity,
  material-journey and school-scope files then passed all 39 cases. Exact wall
  index parity: 6 passed. Prefix-search behavior remains unchanged; actual
  YYYY-MM queries use date ranges without CAST/LIKE.
- Shift history/delivery/public acceptance: 107 passed, one PostgreSQL setup
  error (ownership receipt not configured). New complete-history pagination
  slice regression: 1 passed. Frontend typecheck: exit 0.
- The dedicated Unix-only PostgreSQL attempt did not start: macOS `shmget`
  returned `No space left on device`, including a mmap/16MB retry outside the
  sandbox. This is not a passed PostgreSQL test. Docker and other clusters were
  not started/stopped, and no shared-memory segments belonging to other work
  were removed.
- All three frontend production builds completed successfully in serial.
  Hospital required replacing this worktree's external node_modules symlink
  with its own offline `npm ci`; the original linked directory was untouched.
- Additional assistant/source contracts: 55 passed after pinned baselines were
  updated to the implementation commit and the fixture clock honored cleared
  intervals and the actual 5000ms creation-reconciliation timer.
- Local WebKit readonly browser regressions: 11 passed outside the browser-launch
  sandbox. Earlier launch aborts were not product failures or successful tests.
- School procurement frontend: 15 passed. Shift original-result and wire-caller
  regressions: 73 passed after fixtures supplied the new explicit period API.
- Implementation commits: hospital `f3ff2507`, school `8e59128`, shift `bfa2d47`.
  These are local commits, not develop integration or staging delivery.
