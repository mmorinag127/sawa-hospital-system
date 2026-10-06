# C2 menu readonly: frozen investigation evidence

Base: `91ce0c92ac350b0ba05a36942dada1a3c7889f98`.
Branch: `codex/modernization-c2-hospital-menu-readonly-20261006`.
This is not a completed root fix: operator order GET currently propagates
`MenuReadUnresolved`, and additional API read-projection scope requires review.
No commit, stage, push, merge, deploy, cloud or browser operation was performed.

## Exact formal runs

Artifacts: `/private/tmp/sawa-c2-hospital-menu-readonly-20261006`.
Both result JSON files record exact Python argv, allowlisted environment,
UTC start/end/mtime and source hashes; both report `sourceUnchanged: true`.

| Run | Command | Result / exit | UTC start / log mtime | Log SHA256 |
| --- | --- | --- | --- | --- |
| Archive baseline | `node /private/tmp/sawa-c2-hospital-menu-readonly-20261006/run-formal-full.mjs baseline` | 402 failed, 2570 passed, 42 skipped / 1 | 2026-10-06T12:11:51.911Z / 2026-10-06T12:24:45.831Z | `38938f32234e73595f6a4ebfcc916942822fb09fd27b4db1b73da720d3234392` |
| Frozen current | `node /private/tmp/sawa-c2-hospital-menu-readonly-20261006/run-formal-current-full.mjs current` | 586 failed, 2432 passed, 42 skipped / 1 | 2026-10-06T12:25:14.820Z / 2026-10-06T12:33:24.155Z | `b42a3583f10e48171971be34b946d085b4bbc069ca1e1e1913ca7f08192b7914` |

Absolute logs: artifact root + `/formal-baseline-full.log` and
`/formal-current-full.log`. Manifests: corresponding `*-results.json`.
Current formal full specifies both the historical C1/formal/bootstrap PG55470
URIs and `C2_MENU_READONLY_PG_URI` on PG55471. New boundary cases: 46 passed,
including 23 PostgreSQL cases; none skipped. Historical 42 skips remain,
including dialect-only and pre-existing opt-in reasons; not all are dialect-only.
Full case-ID comparison and exact skip messages: `formal-comparison.json`,
SHA256 `187fc0e58497ee3e86b6635ec4469883ca500e713bb8dc138e0133b48811201a`.
Current-only failures: 188; common: 398; baseline-only: 4. Of the 188, 184
are `MenuReadUnresolved`; four other assertion differences remain unclosed.

Separate focused proof: `focused-list-final.log`, 46 passed, exit 0,
UTC 2026-10-06T12:08:35.247Z to mtime 2026-10-06T12:08:59.525Z,
SHA256 `9392812e9f10c27448e15ffce9556a7d31c4802d677ee026c9ea12fee73d2386`.
Its exact argv/environment/source hashes are in `focused-list-final.json`.
Focused success does not replace formal full or order HTTP proof.

## Historical results and context differences

All earlier attempt logs remain in the artifact directory. Local partial-profile
baseline 404/2229/335skip/46errors and current 588/2091/335skip/46errors are
not the historical 400-failure formal profile. Interrupted/source-changing runs
are historical only. Related current/base runs both retain 3 cross-month failures.

Old native 400 failed IDs are all present in archive baseline 402. Two added IDs
are the weekly weight workbook and stg output import-entrypoint tests.
The archive has neither ancestor `input_example` nor `.git`: workbook loading
raises FileNotFoundError; the script executes repo-root `git rev-parse HEAD`
before Actions validation and returns its generic error code. These are recorded
context differences, not closed/benign product results. A genuine named baseline
WT comparison would remove those archive-specific prerequisites.
`baseline-history-differences.json` records source/trace details, SHA256
`061b07185bd7e7f57f882dccbd74cfc7875a97034e2a0b3a10e0fd3a7ecc7dfc`.
Runtime engine<object> failure already belongs to the old 400; source inspection
finds `test_db_engine_config` reimporting src.db with a mock object engine left
in the module. No new isolation rerun or assertion modification was performed.

## Historical Operator Read Design Before Scope Approval

`api/orders.py:1504` -> `order_service.get_order_by_id:6853` -> serializer
currently raises before returning canonical OCR lines. Proposed API-only read
projection preserves original/corrected quantities and displays unresolved
derived fields as None with existing warning details after workflow/review
attachment. Default strict serializer remains for `total_service.py:126`.
Additional `api/orders.py` and minimal get_order_by_id opt-in scope is not yet
approved here. No warning-only substitution for downstream calculation stop.
HTTP order/STEP3 entrance regression remains required; no new UI is proposed.

## Historical Cleanup and Freeze Before Operator Implementation

Both PG55470 and PG55471: physical root/OWNER/PID/SHOW verified, non-system
DB count 0 and other client count 0 before stop. pg_ctl stop exit 0, status 3,
pg_isready exit 2. `owned-stop-proof.json` SHA256
`7127b1a00d7d60e93f2b3690f6b02103ac9f8310bb8ea75df4c947120ea0e3b2`.
PG15 was not handed off and was not accessed. Source edits are frozen pending
operator-read design review. Full regression is unsuccessful; C2 is not complete.

## Operator Implementation Handoff, 2026-10-06

The parent approved the operator recovery scope after the historical sections
above. Current product/test edits are frozen for handoff, not accepted as complete.
No new test/build/typecheck/E2E/full run is started after the user's stop instruction.
The 586-failure historical run is retained and is not a success. The parent reports
the genuine named baseline as 400 failed / 2572 passed / 42 skipped; current formal
full remains pending an explicit PG55470 handoff. PG55470 was not accessed here.

Implemented shared boundaries:
- Five menu reads use the readonly transaction boundary, retaining formal schema checks.
- Operator order read preserves original lines and exposes menu_amount_unresolved;
  strict serializer remains the default for confirmation, totals and output callers.
- Workflow warning and Step4 gates use the version-checked saved-sheet candidate,
  not stale persisted order lines. Empty/broken candidate stops remain explicit.
- Enrichment errors stop with bagging_output_enrichment_failed; MenuReadUnresolved
  stops with menu_amount_unresolved. Raw candidate bag substitution is removed.
- Month-only remains stored but unresolved (week_choice_required). Received-at
  promotion and received-at selected markers are removed. Explicit ranges remain.
- Candidate identity includes saved monthly item ID, month, explicit master ID and
  existing identity fields including scope. Distinct identities stop even with equal
  amounts. Repeated identical references are recognized without choosing a first
  source. Explicit unit aliases use the existing normalizer (including 枚 -> cut).

Serializer caller classification (order_service.py unless noted):
| Caller | Explicit policy | Reason |
| --- | --- | --- |
| create_order_from_ingest, new/duplicate response | allow_unresolved_menu_amount=True | Input registration response; preserve input lines |
| apply_latest_draft response | True | Operator input/draft projection, not final confirmation |
| get_order_by_id | False default; API orders GET explicitly True | Operator repair view separated from strict consumers |
| rerun_ocr_evidence_only input | True | Recognition input, no amount calculation |
| audit clone response | True | Operator recognition projection |
| reparse_order response | True | Recognition result projection |
| confirm_order_authoritatively | Strict | Confirmation |
| total_service.build_totals | Strict | Amount calculation/output |

Quantity-free current-sheet/candidate projection callers explicitly opt in where
changed; output bag-summary/editor callers remain strict. No all-caller tolerance
was introduced. The new ingest test supplies explicit owned fixture lines to the
registration service; it is not proof of PDF recognition or an end-to-end OCR run.

Recorded runs (absolute root: /private/tmp/sawa-c2-hospital-menu-readonly-20261006):
| Log | Result | UTC mtime | SHA256 |
| --- | --- | --- | --- |
| operator-related-final1.log | 197 passed, 11 failed; exit 1 | 2026-10-06T13:11:16.863Z | 2c3665a9c39d527b1cf167c6a76ffa10bf2d92adf525604c61c023c3a4543cec |
| operator-focused-final1.log | 95 passed, 21 deselected; exit 0 | 2026-10-06T13:13:34.677Z | 24d5c18b69879738ec90c9345847a2d51b2a13e4408ad9507b846ec312f16b2a |
| operator-ui-type-final1.log | typecheck exit 0, earlier source environment | 2026-10-06T13:07:14.851Z | e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 |

Each matching .json contains exact argv, allowlisted environment, source SHA256s,
exit status and UTC timing. The focused selection excludes other candidate-resolution
tests; it is not a full suite or a resolution of the 11 related failures. The related
run precedes two additional boundary tests in the focused source. Earlier attempt
logs, sandbox PG connection failures, draft-fixture failures and identity test failures
are retained. No new skip was introduced.

Remaining: classify the 11 candidate-resolution failures against the genuine named
baseline; compare the four previously reported non-MenuReadUnresolved failures;
verify the complete confirmation/output behavior after integration. Actual headless
screen load remains failed: existing frontend/src/proxy.ts rewrites /hospital/orders
to /orders, whose redirect returns to /hospital/orders (ERR_TOO_MANY_REDIRECTS).
No proxy edit or bypass was made. The existing lint command ignores TS/TSX, so its
exit 0 is not evidence of checking the changed page. No new lint setup was added.

Cleanup: PG55471 OWNER/source path, PID69680 and server metadata verified;
non-system DB count 0, other client count 0. Stop exit 0, status exit 3, readiness
exit 2 at 2026-10-06T13:14:57Z. Evidence:
/private/tmp/sawa-c2-hospital-menu-readonly-20261006/operator-owned-stop-final.json,
SHA256 99aa268d8090fa277ee54190209a19440c62201590b367138b00b970da0bfcb0.
Owned headless server port3117 has no listener. No owned test session remains.
No stage, commit, push, merge or deployment was performed. This is an unverified
implementation handoff, not completion of this root or C2 overall.

## Frozen Follow-up: Same-Scope Ambiguity And Broken Candidate Stops

## Static Review Follow-up Freeze

Unverified static-review implementation after the preceding frozen work:

- Facility item selection now accumulates only eligible base, facility, and inherited tag scopes in one identity-to-best-rank candidate group. Unregistered scopes are excluded before grouping and cannot re-enter as base candidates.
- Same-rank duplicate identities retain the explicit ambiguity stop. Their readonly projection clears carrier IDs and candidate-specific metadata rather than presenting one candidate as selected.
- The obsolete unused `_pick_latest_item_by_identity` helper was removed.
- Bag builder exceptions remain explicit stops without raw-bag fallback. A list result, including `[]` for an all-zero order, remains the builder's canonical result; only a non-list result stops as `bagging_output_invalid`.
- Workflow blocker labels now explicitly describe bag output and saved-sheet stops in Japanese.

Added regression test code covers foreign-scope exclusion, base-plus-foreign-scope non-ambiguity, same-scope equal-value ambiguity, facility-to-group inheritance, zero-quantity empty bag preservation, and non-list bag-builder stop. Per instruction, no test, build, typecheck, lint, audit, server, DB, network, or browser command was executed after these edits.

Unverified implementation added after the preceding historical records:

- `menu_service._read_items_for_facility` no longer chooses a same-priority,
  same-identity item by ID. It projects `menu_identity_ambiguous` with null
  amount/unit and strict reads stop. Facility -> tag/group -> base precedence
  remains unchanged when the winning scope is unique.
- Direct month, latest month, and item-list reads use the same ambiguity
  projection. No read path writes or repairs menu rows.
- Candidate amount validation rejects non-list/empty lines, non-dict rows,
  absent/invalid dates, absent names, and invalid quantities. `0` remains a
  valid corrected or original quantity. Date-bearing order lines require an
  explicit matching menu date; serializer amount metadata has no name-only key.
- Bag payload enrichment has no raw candidate bag substitution. A canonical list
  result, including an empty list for an all-zero order, is retained; only a
  non-list result is an explicit invalid-output stop.

Added boundary-test descriptions cover same-scope equal-amount ambiguity, distinct
scope precedence, all menu-read entry points, broken candidate rows, zero quantity,
and non-list bag output stop. Per the current instruction, these tests, builds, lint,
typecheck, audit, server, DB, network, and browser checks were not executed.
