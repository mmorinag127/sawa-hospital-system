# Facility Detail Controls

Base/HEAD: `cefd20adef1e75bba502df75e5601acb4630a39d`.
Branch: `codex/modernization-v1-hospital-facility-detail-controls-20261008`.
Worktree: `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-v1-facility-detail-controls`.

## Source Boundary

Only page imports/rendered controls/scoped CSS changed. Sixteen native controls
became four Button, one NativeSelect, ten Input (including three multiline and
two file inputs), and one Checkbox. RHF/EditForm/SawaProvider remain unchanged.
Handlers/options/values/disabled props are compared against base by AST; event
annotations erase identically. Native default submit is explicitly retained.
All pre-render functions/state/effects are byte-identical except the UI import
and explicit erased `import type { ChangeEvent } from "react"`.
No master workbook, dependency/config, API, auth, date policy or source changes.
Page total/nonblank lines: 1021/954 before, 1028/960 after.

## Commands And Results

Working directory for commands: this worktree's `frontend`.
Node `N=/Users/mmorinag/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node`
is v24.19.0; actual MUI 9.4.0, Next 16.3.8, Playwright 1.57.0/WebKit 2227.
Dependencies were copied read-only from hospital-main; jsdom runtime was copied
read-only from school-lunch-main into owned `tmp/controls/runtime/node_modules`.
This is not npm-ci/install proof. No packages or lockfiles changed.

The exact owned environment used `env -i PATH=/usr/bin:/bin`,
`HOME=<worktree>/tmp/controls/home`, `TMPDIR=<worktree>/tmp/controls/tmp`.

- `FACILITY_CONTROLS_JSDOM_MODULE=<worktree>/tmp/controls/runtime/node_modules/jsdom $N --test tests/facility-detail-controls.contract.cjs > ../tmp/controls/focused-final.log 2>&1`: exit 0, 2 passed, no skips. Per-event call-count snapshots prevent earlier callbacks from proving later inputs. Extracted actual JSX uses installed React/MUI; supplied FileList-like object proves callback forwarding only.
- `$N node_modules/typescript/bin/tsc --noEmit --incremental false > ../tmp/controls/types-final.log 2>&1`: exit 0.
- `$N node_modules/eslint/bin/eslint.js 'src/pages/facilities/[id].tsx' > ../tmp/controls/lint.log 2>&1`: exit 0, but TSX was ignored by existing config. This is NOT lint coverage; the prior lint-success claim is withdrawn.
- `NEXT_TELEMETRY_DISABLED=1 $N node_modules/next/dist/bin/next dev --webpack --hostname localhost --port 3197`: actual unchanged Next/config/_app/styled-jsx; GET `/hospital/facilities/COMPONENT` 200. Owned server stopped with Ctrl-C, exit 0. Next-generated next-env.d.ts changes were restored, not included.
- `PLAYWRIGHT_BROWSERS_PATH=/Users/mmorinag/Library/Caches/ms-playwright $N tests/facility-detail-next.contract.cjs > ../tmp/controls/next-dirty-final.log 2>&1`: exit 0; 390/1440 both pass, no page errors/control clipping. Browser closes in finally. Existing WebKit binaries read-only; no Chrome/user browser.

The last UI check waits for `Config loaded from file.`, independently compares
parsed JSON to `{raw_zero:0,raw_null:null}`, then selects workbook bytes using
native setInputFiles. Both inputs contain real browser FileList. Existing dirty
guard visibly refuses workbook upload; zero POSTs, three GETs at each width.
This is native selection plus pre-transport refusal, NOT workbook transport,
valid XLSX/template/backend acceptance, OCR, live identity or operator flow.
Synthetic API snapshot and operator identity are explicitly bounded; unmatched
API requests fail. Actual WebKit screenshots were visually inspected for layout.

## Evidence And History

All artifacts under owned `tmp/controls`; generating lineage is the base HEAD
plus this uncommitted source packet, not an integrated/deployed SHA.

| Artifact | SHA256 |
| --- | --- |
| page source | `76e47a20835cb53b3dc7d9ccefc20d9872ac8c69e70098fe9818a26b836c853a` |
| control test | `9fa14dafc4cbf1df70906a3a3b067cf1348dc457a57e86d03f86fdce9ab26ff7` |
| Next test | `40da26b288b18a75bf74ab35cc82bcd753907f562fa9fa7ccb7873dc6bfd4ff7` |
| focused-final.log | `d65b09a9061a9a70a02b4778d90bd1b6d5627dc5d0b942a696f13335ff716cc5` |
| next-dirty-final.log | `9129d5123225eed893205f4693ee777bdbdb9d301fa7e4fa2094837f3e4d6a9c` |
| next-390.png | `261934ecf10fbd3c6b80e6839ff8b5220569f9a332dddf1d8f42cc6b6318b8ab` |
| next-1440.png | `1089364608acaba060c61c59c05c90f3528dcb8c57cd0192a3ed0b4767092388` |

Retained `focused.log`: 1 pass/1 failure, missing jsdom animation-frame globals;
fixed with actual jsdom visual bindings. `focused-2.log`: 2 pass with cross-node
controlled-input warning; final isolated keyed nodes eliminate that warning.
`next.log`: missing owned-HOME browser path; explicit existing read-only browser
path resolves it. First sandbox server attempt EPERM ::1:3197; approved loopback
execution succeeded. Earlier next-2/next-final logs are layout/native selection
only, not transport proof; final negative check adds JSON completion/dirty refusal.

Parent source review, integrated-SHA regression and final usability/live checks
remain pending. No commit, merge, push or deployment was performed.

## Explicit Lint Correction

Previous staged patch `eb6f7ec92ba1eeca2a9221dd36478ebe78ad68f170e4094f3967978cf5721025`
is obsolete and unapproved. Only new React namespace no-undef errors were fixed:
type-only ChangeEvent import replaces React.ChangeEvent in both file annotations.
Handler bodies are unchanged. The parity test allows exactly that one type import;
structured transpilation comparison against the previous staged page proves
emitted JS, including comments, identical (`typeimport-proof.json`, exit 0).
No rules/config/dependencies were changed or disabled.

Base export command from worktree root:
`git show cefd20adef1e75bba502df75e5601acb4630a39d:'frontend/src/pages/facilities/[id].tsx' > tmp/controls/base-facility-detail.tsx`.
SHA256 `80d3275ff4744e323b468d1413b4e9dc9f989ffa02c631a4efc81264da181de0`.

Explicit read-only parser
`P=/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/school-lunch-main/frontend/node_modules/@typescript-eslint/parser/dist/index.js`.
Commands run with `env -i PATH=/usr/bin:/bin` from frontend:

- `$N node_modules/eslint/bin/eslint.js --no-ignore --parser $P --stdin --stdin-filename 'src/pages/facilities/[id].tsx' --format json < ../tmp/controls/base-facility-detail.tsx > ../tmp/controls/lint-base-parser.json 2> ../tmp/controls/lint-base-parser.stderr`: exit 1.
- `$N node_modules/eslint/bin/eslint.js --no-ignore --parser $P --format json 'src/pages/facilities/[id].tsx' > ../tmp/controls/lint-fixed-parser.json 2> ../tmp/controls/lint-fixed-parser.stderr`: exit 1.
- Same fixed command with `--plugin react-hooks --resolve-plugins-relative-to /Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/school-lunch-main/frontend`, outputs `lint-fixed-parser-hooks.json/.stderr`: exit 2, actual plugin not installed at that read-only binding. No installation attempted.

Base/fixed structured diagnostics match exactly by rule/message/severity/nodeType:
two unchanged no-unused-vars for type-signature parameters `values` (51 -> 52)
and `value` (271 -> 272); missing react-hooks/exhaustive-deps rule (513 -> 514).
Zero introduced diagnostics, no React no-undef remaining. This is NOT full/global
lint success or hooks-rule coverage. Parent plugin-bound lint remains required.
Parent `lint-explicit-parser.json` (five diagnostics) and empty
`lint-explicit-parser-hooks.json` are retained unchanged.

Latest bounded checks (no Next/broad suite rerun):
`FACILITY_CONTROLS_JSDOM_MODULE=<worktree>/tmp/controls/runtime/node_modules/jsdom $N --test tests/facility-detail-controls.contract.cjs > ../tmp/controls/focused-typeimport.log 2>&1`
with `env -i PATH=/usr/bin:/bin`: exit 0, 2 pass/0 fail/0 skip.
`$N node_modules/typescript/bin/tsc --noEmit --incremental false > ../tmp/controls/types-typeimport.log 2>&1`: exit 0.
Focused log SHA256 `ff94fc506cdcc4a0755da00beca635261d56b7f78a69c4e1f5e2b173a55b423b`;
structured diagnostic/JS proof SHA256 `90f0d159de213c1f1c299c105653d6c9901437258589028bf4d2599e0e89ab89`.
Earlier Next artifacts remain unchanged; the new type import/annotations have
identical emitted runtime JS. Monitor reapproval is pending before parent commit.

## Trusted TS-Aware Scoped Lint

The missing school plugin binding above is historical, superseded by the
authorized read-only shift-main modules. No source/test/dependency/config changed
for this verification. ESLint 8.57.1 uses parser/plugin 8.64.0 and hooks 7.1.1.
Core no-unused-vars is replaced by its TS-aware extension for this command only,
not removed: both have `vars: all`, `args: after-used`, `caughtErrors: none`,
`ignoreRestSiblings: false`, with no ignored binding patterns. This avoids treating
type-signature parameter names as unused runtime variables. Actual runtime
bindings remain checked. Both hooks rules are enabled at error severity.

From owned frontend, `S=/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/shift-main/frontend`;
`N` is the exact Node path recorded above. Exact final base command:

```sh
env -i PATH=/usr/bin:/bin "$N" node_modules/eslint/bin/eslint.js --no-ignore --parser "$S/node_modules/@typescript-eslint/parser/dist/index.js" --plugin react-hooks --plugin @typescript-eslint --resolve-plugins-relative-to "$S" --rule 'no-unused-vars: off' --rule '@typescript-eslint/no-unused-vars: [error, {vars: all, args: after-used, caughtErrors: none, ignoreRestSiblings: false}]' --rule 'react-hooks/rules-of-hooks: error' --rule 'react-hooks/exhaustive-deps: error' --format json --stdin --stdin-filename 'src/pages/facilities/[id].tsx' < ../tmp/controls/base-facility-detail.tsx > ../tmp/controls/lint-shift-base-final.json 2> ../tmp/controls/lint-shift-base-final.stderr
```

Exact final current command:

```sh
env -i PATH=/usr/bin:/bin "$N" node_modules/eslint/bin/eslint.js --no-ignore --parser "$S/node_modules/@typescript-eslint/parser/dist/index.js" --plugin react-hooks --plugin @typescript-eslint --resolve-plugins-relative-to "$S" --rule 'no-unused-vars: off' --rule '@typescript-eslint/no-unused-vars: [error, {vars: all, args: after-used, caughtErrors: none, ignoreRestSiblings: false}]' --rule 'react-hooks/rules-of-hooks: error' --rule 'react-hooks/exhaustive-deps: error' --format json 'src/pages/facilities/[id].tsx' > ../tmp/controls/lint-shift-current-final.json 2> ../tmp/controls/lint-shift-current-final.stderr
```

Both exit 0: messages empty, errorCount 0, warningCount 0. Both retain one existing
inline-suppressed exhaustive-deps diagnostic: useEffect missing `loadFacility`,
base line 514/current 515. This suppression is unchanged and is a limitation,
not proof of that effect's exhaustive dependency correctness. No new suppression.
This proves explicit scoped TSX lint, not global/full lint or monitor acceptance.
Earlier default-option runs `lint-shift-base.json`/`lint-shift-current.json` also
exit 0 and remain retained; final runs explicitly match core unused options.
Current NOT OK gate remains until parent reviews this evidence. No broad tests,
browser, install, process launch or production edits were performed in this turn.
