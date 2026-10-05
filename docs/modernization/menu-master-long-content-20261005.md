# Menu-Master Long-Content Regression

Base/source: `356df78e7538633f294d73467a369ab8633bbec8`, WT `hospital-c1-live`. No stage/commit/deploy/live writes. Backend/API/business rules, dependency versions, nine-field assertions and unknown-reference/cleanup guards are unchanged.

## Reproduction And Fix

Actions `37266102226` completed real POST rev1, PUT rev2 and fresh-document nine-field checks, then failed; its owned record was cleaned with API absence verified. Local reproduction used the exact name `c1-live-37266102226-1-aabd0ce687864d539b5b6f9ea984dcdd`, unchanged base product source, actual Next 16.3.8/React18 app, isolated actual SQLite backend and headless WebKit. Only external Google identity verification was locally faked.

The first failing assertion was **updated-page-width**, not row count or Japanese cell text. At 360px the document was 364px wide. The edit heading box ended at 344px, but its text ended at 364.125px (text width 348.125px inside a 328px box). This confirms unbroken name text overflow from the edit heading; the internally scrolling table was correctly bounded.

The shared menu-page content boundary now inherits `overflowWrap: anywhere`. No truncation, hidden overflow, smaller name/font or relaxed assertion. With the same name, document width is 360px, text right edge 341.879px inside the 344px boundary; at 1280px document width remains 1280px. Normal Japanese stays on one heading line. Three tracked regression cases cover the exact name, 96 unbroken ASCII characters, and Japanese control, each at 360/1280 including full text/input preservation and heading/field separation.

The live script now records static checkpoints plus numeric viewport/row-count/HTTP-status/DOM dimensions. Added diagnostics contain no DOM text, headers, token, exception text or storage values. Existing owned-only screenshot and receipt rules remain. An exported test option selects 360 or 1280 for actions; the Actions executable still defaults to 1280 and both width assertions always run.

## Fresh Evidence

All paths below are relative to this WT under `tmp/live-tests/`; old evidence was not overwritten.

| Path | Actual Result |
| --- | --- |
| `long-name-before-DWAA3U/local-bind-failure.json` | Initial sandbox bind EPERM, no services/test started; retained |
| `long-name-before-6XEaMQ/` | Reproduced width failure after real POST/PUT; build exit0, harness exit1 |
| `long-name-after-b4oKVH/` | First fix pass, retained; predates the optional action viewport argument |
| `long-name-mobile-after-3L3iO4/` | Final source, 360px action flow and both width assertions: passed |
| `long-name-after-63O2vu/` | Final source, default 1280px action flow and both width assertions: passed |
| `long-name-checks-psW5Kg/` | Final unit/config 122/0fail/0skip, type/lint exit0, tracked WebKit 30/0fail/0skip/0flaky, retries0 |

Both final actual-backend runs freshly built Next (Node20.17.0, exit0), performed all nine-field POST/PUT, fresh-document values/null/0/Japanese, actual second-editor stale409 with draft retention/explicit reload, and explicitly injected pre-backend network-abort draft retention. Each had zero page/hydration/unexpected-write errors, three acknowledged receipts and no pending write. Separate actual-backend long-ASCII/Japanese controls passed at both widths. Tracked WebKit is explicitly synthetic API evidence, separate from the actual-backend runs; all 27 prior cases plus 3 new cases passed.

Commands: Node20 `tmp/live-tests/long-name-browser-356df78.mjs before|after`, `tmp/live-tests/long-name-mobile-browser-356df78.mjs after`, and `tmp/live-tests/long-name-checks-356df78.mjs`. Per-run `commands.json` contains exact build/server/test commands and exits; `source/`, `source.diff` and `manifest.json` preserve source bytes and hashes. Consolidated candidate/diff/evidence hashes: `tmp/live-tests/long-name-356df78-handoff.json`.

Images from final actual backend: `long-name-after-63O2vu/owned-row-360.png`, `owned-edit-360.png`, `unbroken-ascii-360.png`, and `japanese-control-1280.png`. Numeric before/after heading evidence is in each `browser-result.json`; control dimensions are in `focused-layout.json`.

Owned backend/Next/WebKit processes stopped and `next-env.d.ts` restored byte-exact; no package/lock change. Local records remain only in isolated SQLite evidence files. No live retry, Google human-login proof, backend full-suite rerun or C1/STG completion is claimed; parent owns integration and Actions execution.
