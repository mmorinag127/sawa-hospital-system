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

- PR170 is integrated into develop at `2a180d57`; fixture follow-up PR171 is
  integrated at `51bef89d`. School PR101 and Shift PR94 are integrated separately.
  These integrations do not establish staging delivery of the performance work.
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
- The remaining whole-ledger reads are now replaced by a transactional PostgreSQL
  row index. The compressed SQLite ledger remains canonical. Writes update both
  in the same transaction; readers verify epoch and canonical payload digest,
  use one repeatable-read snapshot and refuse unprepared or inconsistent indexes.
  Config, owner-scoped latest-100 runs, explicit run detail/events and owner-bound
  target lookup reads transfer only their required rows. SQLite retains original
  scalar/query semantics. An unloaded table read is rejected, not treated as empty.
  An old writer that changes only the canonical payload causes reads to stop;
  a canonical write rebuilds the index, never a stale read fallback.

## Remaining implementation and measurements

Known implementation was completed before the following batch verification.
No additional commit, merge, Actions or deployment was performed during that
implementation phase. The earlier fixture-first integration was a sequencing
violation, not evidence that the backlog had been finished.

- Input tracking schema 2 stores global/fixture/module inputs once, with per-case
  references and deltas. Collection reads follow static relative/bare/helper
  imports; unresolved dynamic imports remain conservative. Conftest/production
  reads remain shared. Changed shared inputs invalidate their consumers.
- Selection hashes each unique input once at selection and once before publishing
  reusable history. Runtime mutation/unsafe-input guards are retained. Newly
  added files no longer invalidate unrelated successful cases solely by filename.
- Failed/interrupted attempts preserve the prior valid tracking database and
  metadata; explicit failed/planned identities remain executable on retry. Owned
  child process groups are stopped and reaped before baseline restoration.
  Legacy metadata larger than 64MiB is rejected without loading it into memory.
- Tracker/runner/codec batch: 101 passed, 27 subtests, 38.23s. Agent related suite:
  423 passed, 21 skipped, one missing-backend-import setup failure. The corrected
  PostgreSQL plus packaged preparation batch then passed all 22 cases in 1.87s;
  skips in the first batch are not counted as passes. Native Linux confirmation
  remains part of the signed serving-image verification, not this local result.

Measured local comparisons (not staging/full-page latency):

| Scenario | Before | After | Invariant |
| --- | ---: | ---: | --- |
| PG16 detail read, 10,000 unrelated events + 10 requested, median of 7 | 16.37ms | 2.40ms | Exact response SHA256 equal |
| Same read, peak Python allocations | 48,189,585 bytes | 30,887 bytes | Same canonical committed ledger |
| 200 cases, 200 shared input files, initial tracking | 1.106s | 0.429s | 200 executed in both |
| Same cases unchanged | 0.806s | 0.256s | 200 reused in both |
| Tracking metadata | 3,438,824 bytes | 46,487 bytes | Shared input checks retained |

Raw evidence:
`/private/tmp/sawa-page-pg-20261026-r4/output/ledger-before-after.json`,
`/private/tmp/sawa-impact-measure-20261026/results.json`,
`/private/tmp/sawa-page-pg-20261026-r4/output/current-agent-pg-validation.xml`.
The metadata comparison used the original plugin from `51bef89d` and the current
plugin on identical synthetic inputs in separate temporary repositories.
These measurements do not establish a 15-minute full verification batch or
2-3-second staging page completion. Full source-bound verification, minimum
legitimate staging delivery and all-page required-data timings remain pending.

The first fixed-source batch was interrupted after 183.69s without a receipt.
A narrowly measured audit-guard shortcut showed no improvement (20.18s to
20.29s on the same real workflow regression) and was removed. Profiling instead
identified 87 calls to print-workbook generation from logical target validation;
87 styled workbook loads consumed 18.67s of a 33.05s instrumented profile.
Logical validation now reads the same authored XLSX values and merge ranges,
then uses the existing facility/week/body/column/schema and target validators.
It does not restore print-only cell styles or images. The print/output path is
unchanged. No previous plan or cached source replaces the current authored file.
The actual worksheet XML cell coordinates, rather than a dimension hint, bound
the copy. All authored facilities have exact target-plan parity across a
month boundary; missing source/sheet/menu/columns retain their errors.

Related batch: 141 passed and 23 subtests; one new-fixture invalid physical-week
ID was corrected to the existing September-anchored canonical format. The entire
master-template module then passed all 18 cases in 5.48s. The same real workflow
case with the original tracking instrumentation measured 20.18s before and
12.36s after this logical-workbook change (single comparisons, not page latency).
Evidence: `/private/tmp/sawa-unsafe-tracking-measure-20261026/results.json`,
`/private/tmp/sawa-unsafe-tracking-measure-20261026/profile/calls.prof`.
An actual child-process regression also verifies TERM cleanup when an enclosing
shell supplied ignored SIGINT; handlers are restored after the child is reaped.

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
- PostgreSQL setup subsequently recovered by removing only segment `74317855`,
  whose key matched this task's failed initdb, NATTCH was zero and creator PID
  49576 was no longer running. The previous 31 segments were not modified.
  An owned Unix-only PG16 cluster then passed the shift duplicate-delivery test
  (1 case) and agent persistence/restart/fencing module (12 cases), serially.
  Both invocations verified shutdown. Raw JUnit files are
  `/private/tmp/sawa-page-pg-20261026-r4/output/results.xml` and
  `/private/tmp/sawa-page-pg-20261026-r4/output/agent-results.xml`.
  This does not replace the complete Linux serving-image verification receipt.
