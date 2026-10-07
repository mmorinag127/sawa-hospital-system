# Monthly Menu Ordinary Controls

Base/HEAD `6611bade8de4bd97f4198282618cd852cdc8de35`, branch
`codex/modernization-v1-hospital-monthly-controls-20261008`, owned worktree
`/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-v1-monthly-controls`.

## Scope And Parity

71 native ordinary controls replaced: Button 20, Input 32, NativeSelect 17,
Checkbox 2. No remaining native button/input/select/textarea in this page.
Existing DataTable/EditForm/RHF/SawaProvider and specialized editing remain.
No business functions/state/owner/auth/CAS/date/source/quantity changes.
File event annotation is type-only; handler body is unchanged. Radio checked,
min/step/list remain actual HTML inputProps. Original options/handlers/raw values,
disabled and default native submit are checked against base with TypeScript AST
and erased-expression parity; non-rendered source is byte-identical excluding
the exact UI/type imports. CSS is page-scoped. No new wrapper abstraction.
Total/nonblank page lines: 3646/3418 -> 3626/3397. Only replacement-introduced
empty attribute lines were removed; no business-code savings claim.

## Owned Commands

Commands below ran from owned `frontend` unless specified. Node:
`N=/Users/mmorinag/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node`.
`W` is the absolute worktree above. Runtime dependencies copied read-only from
hospital-main into own node_modules. No install/package/lock changes or npm-ci
proof. jsdom read-only binding:
`J=/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-v1-facility-detail-controls/tmp/controls/runtime/node_modules/jsdom`.
MUI 9.4.0, React 18, Node 24.19.0, Next 16.3.8, Playwright 1.57.0/WebKit 2227.

```sh
env -i PATH=/usr/bin:/bin HOME="$W/tmp/home" TMPDIR="$W/tmp/cache" MONTHLY_JSDOM_MODULE="$J" "$N" --test tests/monthly-controls.contract.cjs > ../tmp/focused-final.log 2>&1
env -i PATH=/usr/bin:/bin "$N" node_modules/typescript/bin/tsc --noEmit --incremental false > ../tmp/types-final.log 2>&1
env -i PATH=/usr/bin:/bin HOME="$W/tmp/home" TMPDIR="$W/tmp/cache" NEXT_TELEMETRY_DISABLED=1 "$N" node_modules/next/dist/bin/next dev --webpack --hostname localhost --port 3218 > ../tmp/next-server-3218.log 2>&1
env -i PATH=/usr/bin:/bin HOME="$W/tmp/home" TMPDIR="$W/tmp/cache" PLAYWRIGHT_BROWSERS_PATH=/Users/mmorinag/Library/Caches/ms-playwright "$N" tests/monthly-controls-next.contract.cjs > ../tmp/next-proof-final.log 2>&1
```

Focused: exit 0, 2 pass/0 fail/0 skip. All 71 controls have original parity;
actual production JSX rendered with installed React/MUI forwards raw sheet text,
scope/reset, checkbox functional updater and file callback. Actual upload Button
disabled=true prevents callback, false permits it, original submit type retained.
FileList-like object in jsdom is callback proof only, not native browser proof.
Global TS: exit 0. Real unchanged Next/_app/styled-jsx at matched localhost
3218: GET `/hospital/menus/2026-10` 200. WebKit exit 0 at 390/1440: raw text,
tag selection, checkbox check/uncheck, two native setInputFiles/FileList inputs,
enabled/default-submit upload control, disabled empty DataTable pagination,
no control clipping/pageerror, no API writes. Screenshots visually inspected.
Table overflow exemptions apply only actual horizontally scrolling tables.
Server stopped by Ctrl-C, exit 0; browser closes in finally. Next-generated
next-env.d.ts restored; no config changes included.

## Actual Scoped Lint

Read-only `S=/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/shift-main/frontend`.
ESLint 8.57.1, parser/plugin 8.64.0, hooks 7.1.1. Core unused rule is replaced by
the TS-aware extension with identical checks/options, not bypassed. Exact command:

```sh
env -i PATH=/usr/bin:/bin "$N" node_modules/eslint/bin/eslint.js --no-ignore --parser "$S/node_modules/@typescript-eslint/parser/dist/index.js" --plugin react-hooks --plugin @typescript-eslint --resolve-plugins-relative-to "$S" --rule 'no-unused-vars: off' --rule '@typescript-eslint/no-unused-vars: [error, {vars: all, args: after-used, caughtErrors: none, ignoreRestSiblings: false}]' --rule 'react-hooks/rules-of-hooks: error' --rule 'react-hooks/exhaustive-deps: error' --format json 'src/pages/menus/[monthId].tsx' > ../tmp/lint-final.json 2> ../tmp/lint-final.stderr
```

Base exported from root with
`git show 6611bade8de4bd97f4198282618cd852cdc8de35:'frontend/src/pages/menus/[monthId].tsx' > tmp/base-monthly.tsx`.
Base lint uses same command/options with `--stdin --stdin-filename 'src/pages/menus/[monthId].tsx' < ../tmp/base-monthly.tsx`, outputs `lint-base.json/.stderr`.
Both exit 1 with the same six existing diagnostics: redundant Boolean at base
515/current 516; missing selectEntry effect dependency 964/965; missing
selectedEntry/selectedItem dependencies 968/969; three unnecessary quote escapes
1342/1343. Zero introduced diagnostics. Existing single inline suppression remains
for loadMenu/loadRecentMenus/loadSavedImports/loadScopeOptions dependencies.
No new suppression/rule/config changes. NOT full/global lint green.

## Evidence And Limits

Artifacts under absolute `$W/tmp`, generated from base HEAD plus this staged dirty
packet, not integrated/deployed SHA. SHA256:

| File | SHA256 |
| --- | --- |
| page | `dd6fea481fb6f3a5585738b355ec11ffd0366d745cc09e6843e3b027c049f10e` |
| monthly-controls.contract.cjs | `6ed0b781b24c512f42d5dc8da06161b7b1a3c38331c6ae30bf3378101996d4dd` |
| monthly-controls-next.contract.cjs | `b12342b54c38d5a8e3077869ba7f4687da86f3066938fe98614c8073316a4eb2` |
| focused-final.log | `1fe64e08e6d9c8115f284cf7b064feb2f2facf8511eb38f28b4af42db03f552e` |
| next-proof-final.log | `3f611d203faf9772beb4930c7256edc9b7b92c35432f88def48871124f6f3f92` |
| browser/next-390.png | `b1830e2192fed93002e19149ac6cbad8eebfc7f848a61fd5b8b6158a0af40a78` |
| browser/next-1440.png | `80060813a399718fdb9d112b4fc051e4a44e0596b34a6f91cf7244f3e01b0d89` |

Retained failures: `next-server.log` EADDRINUSE 3198 (foreign process untouched);
`next-proof.log` timeout from old native-select CSS locator, corrected to actual
MUI root/select DOM in test only. `focused.log`, `types.log`, `next-proof-2.log`,
`lint-current.json`, final logs and `parity-summary.json` retained. No broad suite.

Identity/API are explicit synthetic component boundaries; unmatched APIs and
all writes refuse. Empty authored fixture reaches initial controls only; conditional
review/exception/master controls have source/AST parity, not whole live flow proof.
Synthetic file bytes only test native selection, not XLSX/FAX validity, import,
OCR/review/export/backend acceptance. No customer data or production writes.
Parent monitor/integrated-SHA regression and human usability review remain pending.
No business root-fix/completion, commit/merge/push/deploy claim.

## Bounded Whitespace Review Correction

Prior patch `6293cd99391be1e00e12dc5e2bb4633cf8a1174ad94baad9a21449b45f888a57`
is obsolete, not approved. Removed exactly 29 replacement-introduced whitespace-only
attribute lines and one doubled space in `<Checkbox  checked` via apply_patch.
Base contained no whitespace-only lines. No unrelated cleanup or formatter used.
Source equality against previous staged page after exactly these two replacements
was asserted. Transpiled JS bytes are identical with ES2020/React/CommonJS options;
printed emitted-JS AST is also identical. Proof: `tmp/whitespace-proof.json`, exit 0,
SHA256 `b10af6846708527f2175eac48a7eeabbe63bdf2e200b2c2a74b06ace0191f6ef`.
No Next rerun; prior screenshots have identical runtime emit lineage.

Repeated only the exact focused command above, redirecting to
`../tmp/focused-whitespace.log`: exit 0, 2 pass/0 fail/0 skip; SHA256
`1555ccea5b9dfcfb0e58397c34b3a6a123a2d7785bd4b042f43863579a09971e`.
Existing globalTS exit 0 and inherited lint6 remain recorded, not rerun or fixed.
All previous logs retained. Updated four-file packet requires fresh parent review;
no approval, commit, deploy or broader verification claim.
