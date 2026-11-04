# Cold start implementation

Scope: hospital, school-lunch and shift. Preserve authentication, quantities,
database drift detection and normal user operations. No additional billing,
production changes, local deploy or automatic Actions.

| Order | Target | Status |
|---|---|---|
| 1 | Precompile immutable Python dependencies and source during image build | tried and measured; extra dependency/stdlib compilation removed because benefit was not established; retain existing source compilation |
| 2 | Reuse the complete verified hospital catalog for redundant startup schema checks | implemented; complete catalog and identity checks retained, engine-bound snapshot reused |
| 3 | Authentication dependencies | implemented conditional Google SDK loading for hospital/school; same official verifier/TLS transport retained; count first-use relocation separately |
| 4 | Deterministic migration-source discovery | implemented build-time source-bound head in hospital/shift; missing production manifest or source drift fails closed |
| 5 | Facility projection preparation | reviewed: update path already projects JSON; retain necessary startup freshness reconciliation, no unsupported skip |
| 6 | School/shift startup internals | implemented separate connection/schema phase logs; school reuses its inspector without removing catalog checks |
| 7 | Update focused regressions and run serial local verification | local Linux 284 passed, 5 PG-only skipped; environment mismatches corrected without changing application assertions |
| 8 | Integrate, minimal manual stg delivery, measure cold and first-use costs | not started |

Baseline diagnostic starts: hospital 6.273017s, school 6.400086s, shift 5.524292s.
Import self totals: 4.155606s, 5.076122s, 3.641154s respectively.
The baseline used importtime and includes its overhead. Compare matching modes.

The previous suggestion to replace complete runtime catalog inspection with only
an Alembic version check is insufficient: a matching version does not prove the
absence of dropped columns/indexes or manual drift. Do not implement that shortcut.

Dedicated sources: worktrees/cold-start-reduction-20261104 (hospital),
worktrees/cold-start-school-source-20261104 (school),
worktrees/cold-start-shift-20261104 (shift). Other worktrees remain untouched.
Specified gpt-5.6-terra/low is unavailable; no substitute agents were launched.

Local import-only samples (same Linux image, one CPU, fresh process; not stg
whole-container startup): hospital 2.918912 -> 2.215237s, plus 0.344401s deferred
Google SDK work = 2.559638s combined. School 4.171530 -> 4.016993s, plus 0.154566s
= 4.171559s combined: relocation only, not a total-work reduction. Shift 3.802651
-> 3.355960s (Google SDK forced-load combined 4.117601 -> 3.678922s).

Full standard deployment receipts still require their cataloged commands. Do not
forge them from this focused batch or claim skipped/unchanged suites passed.
No test gate, billing setting or workflow dispatch has been changed in this task.
