# OCR Entry Ordinary Controls

Base/HEAD `1edb3d8628ea416dd5f5f69edae9b640c105e4b1`.
Branch `codex/modernization-v1-hospital-ocr-entry-controls-20261008`.
Owned worktree `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-v1-ocr-entry-controls`.

## Rendering Scope

All 16 remaining native controls replaced (pdf 9, queue 5, results 2).
Including the existing pdf Button, final controls: pdf Input4/Checkbox2/Button3/
NativeSelect1; queue Input1/Button3/NativeSelect1; results Checkbox1/Button1.
No native button/input/select/textarea remains in these three pages.
Existing pdf RHF/FormProvider/SawaProvider retained. Queue/results returned main
is wrapped in existing SawaProvider with page-specific theme key; original page
functions/names/hooks remain intact, no auth/ownership mechanism added or changed.
File ref maps to inputRef (actual HTML input), accept/multiple to inputProps,
handler body unchanged with erased ChangeEvent annotation. Native default submit
explicitly retained. No new abstraction, dependency/config, business/source/date,
quantity, master, API or backend changes. Scoped CSS only.

| Page | Original Total/Nonblank | Final Total/Nonblank |
| --- | --- | --- |
| pdf-upload.tsx | 1211/1165 | 1212/1166 |
| ocr-queue.tsx | 612/559 | 618/565 |
| ocr-results.tsx | 380/336 | 383/339 |

## Exact Commands And Results

Commands from owned frontend. Definitions:
`N=/Users/mmorinag/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node`;
`W` is the absolute worktree above;
`J=/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-v1-facility-detail-controls/tmp/controls/runtime/node_modules/jsdom`;
`S=/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/shift-main/frontend`.
Own node_modules copied read-only from hospital-main, not install/npm-ci proof.
Node24.19.0/Next16.3.8/MUI9.4.0/Playwright1.57.0, existing WebKit2227 read-only.
No installs/shared cache writes.

```sh
env -i PATH=/usr/bin:/bin OCR_ENTRY_JSDOM_MODULE="$J" "$N" --test tests/ocr-entry-controls.contract.cjs > ../tmp/focused-frozen.log 2>&1
env -i PATH=/usr/bin:/bin "$N" node_modules/typescript/bin/tsc --noEmit --incremental false > ../tmp/types-frozen.log 2>&1
env -i PATH=/usr/bin:/bin HOME="$W/tmp/home" TMPDIR="$W/tmp/cache" NEXT_TELEMETRY_DISABLED=1 "$N" node_modules/next/dist/bin/next dev --webpack --hostname localhost --port 3221 > ../tmp/next-server.log 2>&1
env -i PATH=/usr/bin:/bin HOME="$W/tmp/home" TMPDIR="$W/tmp/cache" PLAYWRIGHT_BROWSERS_PATH=/Users/mmorinag/Library/Caches/ms-playwright "$N" tests/ocr-entry-controls-next.contract.cjs > ../tmp/next-proof.log 2>&1
```

Focused exit0, four pass/zero fail/skip. All original props/options/handler
expressions compared with AST/erased parity; non-rendered source byte-identical
except explicitly permitted imports. Actual installed React/MUI evaluates exact
production JSX: raw month/datetime/memo, checkbox callbacks, multiple-file callback
and actual input ref, disabled Button prevents dispatch; enabled permits dispatch.
jsdom file array is callback proof, not native browser FileList proof.
Global TS exit0. Actual unchanged Next/config/_app/styled-jsx serves all pages200
at matched localhost. Six WebKit cases pass (each page390/1440), screenshots
visually inspected, no pageerror/control clipping. GET hold proves loading-disabled
controls. PDF native two-file FileList selection updates visible count; no submit,
no selected facility/week, no accepted PDF or OCR claim. Queue empty template
command shows exact existing required-ID refusal before any POST, raw template
input and status select forwarded. Results checkbox check/uncheck retained.
Synthetic auth/API boundaries explicit; unmatched GET/non-GET fails, total writes0.
Server stopped by Ctrl-C exit0; browser closes in finally; own next-env restored.

Explicit scoped TSX lint command:

```sh
env -i PATH=/usr/bin:/bin "$N" node_modules/eslint/bin/eslint.js --no-ignore --parser "$S/node_modules/@typescript-eslint/parser/dist/index.js" --plugin react-hooks --plugin @typescript-eslint --resolve-plugins-relative-to "$S" --rule 'no-unused-vars: off' --rule '@typescript-eslint/no-unused-vars: [error, {vars: all, args: after-used, caughtErrors: none, ignoreRestSiblings: false}]' --rule 'react-hooks/rules-of-hooks: error' --rule 'react-hooks/exhaustive-deps: error' --format json src/pages/pdf-upload.tsx src/pages/ocr-queue.tsx src/pages/ocr-results.tsx > ../tmp/lint-final.json 2> ../tmp/lint-final.stderr
```

ESLint8.57.1/TS parser+plugin8.64.0/hooks7.1.1 read-only. TS-aware unused extension
retains same core checks/options (no ignored variables); no suppression/config edit.
Base exported via `git show 1edb3d8628ea416dd5f5f69edae9b640c105e4b1:frontend/src/pages/<page>.tsx > tmp/base-<page>.tsx`
from root. Same lint options per base with `--stdin --stdin-filename src/pages/<page>.tsx < ../tmp/base-<page>.tsx`, outputs `lint-base-<page>.json/.stderr`.
Base pdf/queue exit1, results exit0. Current combined exit1: exact same two inherited
diagnostics: unused setFacilityHint (pdf), missing loadQueue effect dependency
(queue). Results clean. No suppressed diagnostics, no introduced diagnostics.
Structured equality/count evidence `tmp/summary-final.json`, exit0. NOT global lint
green and no pre-existing business/lint correction.

## Provenance, Failures And Limits

Source/artifacts from base HEAD plus this dirty packet, not integrated/deployed SHA.
SHA256:

| File | SHA256 |
| --- | --- |
| pdf-upload.tsx | `7bf0ca8a1cbfcb736ab5475a841d2147f7c39933feddf353124c66c519c40637` |
| ocr-queue.tsx | `4df0a29a542ad422f8cf96254eeb73beed7e3014f535c87282ceb5da061d33c7` |
| ocr-results.tsx | `6142f226dd9bdf66a3c543650610167aad8d68f3cc37d0411c8a4e9609d71f17` |
| control test | `244642f3ba65c600b7acc761e9fb7f277801369cff8bc5bedd0dff978eb92b82` |
| Next test | `a21219c8f44223f2d2f9c8644ee5802d45f20e67eaa4ba032923ff832c10dc8f` |
| focused-frozen.log | `da41513adba28b7fd6af0fb1c29a965e679b0eceac57a3776b35eb6c159a343f` |
| next-proof.log | `fc94d664b4a4b06baa1a5352b462bd1e1df04b39a38a820289be1fbd306f6932` |
| browser/pdf-upload-390.png | `77bdbcba08fec789c7200542c1f1b761a209958efefddf616f93656ef19da275` |
| browser/pdf-upload-1440.png | `dac49730d5609b6304d33e40d80e6d17bd2d4940faa710732d7ed988fa2322f8` |
| browser/ocr-queue-390.png | `a29c9a40c2b4fd67e56ca32b9cb6e2f86184c7b6d48eca0f9642763344543fc7` |
| browser/ocr-queue-1440.png | `34262176dec3a1dc840336d2bf5dda42731830efe7b3e4e4b2e0d8f1846e4bed` |
| browser/ocr-results-390.png | `09c3e77680a5d090c704854c9b0bd7cb4597225f06b67b4c92573a41b3c2fe98` |
| browser/ocr-results-1440.png | `1c1fb6c83268f035c4e52cf053c61acb85fc6e90f24d4b3342a868ad3c06b05f` |

Retained types.log exit2: missing required SawaProvider sessionKey, corrected by
theme-only page key without hook changes. Retained lint-current.json: two new
unused UI imports in results, removed; import-parity.log proves removal emitted JS
byte-identical, so no repeat Next run needed. Earlier focused/types/lint logs remain.
No wholefile formatter, introduced blank attribute lines, broad suites or assertion
weakening. All temporary outputs owned tmp; no pre-existing untracked changes.

No actual PDF upload, OCR, recovery, DB/job mutations or valid facility/week binding
tested. Selection bytes are .bin synthetic component data, not PDF acceptance.
skipOcr code remains untouched, not used as automation proof. Conditional historical
rows/candidate/download business flow not executed; their controls have AST parity.
Parent source monitor, integrated-SHA checks and actual valid-PDF user-flow proof
remain pending. This is three-page rendering reuse, not whole H2/rootfix/deploy
acceptance. No commit/merge/push/deploy/main writes.
# Final Theme-Only Correction

This section supersedes earlier queue/results SawaProvider and frozen-hash claims above. Patch 5c90 was withdrawn before integration. Queue/results now use public MUI ThemeProvider with the public @sawa/ui sawaTheme export; no sessionKey or QueryClient boundary is introduced. PDF SawaProvider is unchanged. Earlier provider-composition runs remain historical evidence only.

Final commands (frontend cwd, env -i PATH=/usr/bin:/bin, bundled Node v24.19.0):
- node --test tests/ocr-entry-controls.contract.cjs with the existing read-only detail jsdom binding: exit 0, 4 passed. Original non-rendered source and control handler/option parity retained.
- node node_modules/typescript/bin/tsc --noEmit --incremental false: exit 0.
- Explicit ESLint TS parser and hooks/TypeScript plugins from shift-main, equivalent TS-aware unused rule: exit 1; inherited setFacilityHint unused and loadQueue dependency diagnostics only. No global lint-green claim.
- Own Next dev --webpack --hostname localhost --port 3221 and tests/ocr-entry-controls-next.contract.cjs: exit 0, all three pages at 390/1440, held-read disabled controls, native file selection and blocked command checks, zero API writes. Latest log: tmp/next-theme-proof.log; tmp/browser artifacts were regenerated from this theme-only source. Synthetic identity/API responses are component evidence, not OCR/operator flow.

Owned browser and Next sessions stopped; generated next-env.d.ts restored. Parent monitor approval and integrated-SHA proof remain pending. Counts after correction: queue 619 total/566 nonblank; results 384/340; PDF unchanged 1212/1166. Earlier source hashes above are historical, not final hashes. Browser paths were regenerated; the historical table is not proof of their current contents. This final table identifies the actual current source/tests/artifacts, generated from the stated base plus the final theme-only packet, not an integrated/deployed SHA.

| Final File | SHA256 |
| --- | --- |
| frontend/src/pages/pdf-upload.tsx | `7bf0ca8a1cbfcb736ab5475a841d2147f7c39933feddf353124c66c519c40637` |
| frontend/src/pages/ocr-queue.tsx | `b59b6009c129ffd8bb1ddafa1dead1a10a7eaa2ed4d7a4fff879059fa8843a19` |
| frontend/src/pages/ocr-results.tsx | `f6d1900caabf5d55cba42bd0b9d59c1fd3b6745e52c2caca90f63b63b2db65d9` |
| frontend/tests/ocr-entry-controls.contract.cjs | `191b71a73237189134807b46707581673a0befdc97920c8d9b8b86c6cbe33ab8` |
| frontend/tests/ocr-entry-controls-next.contract.cjs | `a21219c8f44223f2d2f9c8644ee5802d45f20e67eaa4ba032923ff832c10dc8f` |
| tmp/focused-final.log | `7483d0b6d653e042d5f01954f6c4ffcb7911c7a155439931a60441540070b9bc` |
| tmp/types-final.log | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| tmp/lint-theme.json | `18faf1ac1ada92793d4834c17d207290c0e1ec266bba8ab5a5dbe4d153b48ec8` |
| tmp/next-theme-proof.log | `fc94d664b4a4b06baa1a5352b462bd1e1df04b39a38a820289be1fbd306f6932` |
| tmp/browser/pdf-upload-390.png | `f3391787be4991d783b4053bbcfaaa3088a9b9440dfaa6ebf03081178b50fd1d` |
| tmp/browser/pdf-upload-1440.png | `169d29201c5d17155136404b72a030c0a7dccb2c9ce77013ed007baebfe80950` |
| tmp/browser/ocr-queue-390.png | `a29c9a40c2b4fd67e56ca32b9cb6e2f86184c7b6d48eca0f9642763344543fc7` |
| tmp/browser/ocr-queue-1440.png | `34262176dec3a1dc840336d2bf5dda42731830efe7b3e4e4b2e0d8f1846e4bed` |
| tmp/browser/ocr-results-390.png | `5f1f9fb3ce3267a66c504c3b74f625e48f20b4b8bb1de54ae82b130b8d8171e7` |
| tmp/browser/ocr-results-1440.png | `bd4d63fd523a2050dfabd16460b5447b88a5720eee414efbb3d9d5936f86f0c2` |
