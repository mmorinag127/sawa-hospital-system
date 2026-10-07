# Facility Ordinary Controls

Base/generating HEAD: `cd14d8bd36fbddd63fb70cbb1c4088be544cd6b1` plus
this frozen uncommitted packet. Branch:
`codex/modernization-v1-hospital-facility-controls-20261008`.
Absolute own root:
`/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-v1-facility-controls`.

Only production file: frontend/src/pages/facility-master.tsx. All 43 native
ordinary controls replaced by installed MUI Input/NativeSelect/Checkbox/Button.
Existing options, raw value expressions, callbacks, disabled conditions and
original native button submit behavior retained. Existing RHF EditForm,
FormTextField, SawaProvider and NavigationBoundary retained. Table inputs/list
inputs/JSON input receive accessible names. No new fields, casts, coercion,
auto-save, dependency changes or business changes. Domain preview tables and
column-selection callbacks unchanged. Scoped control-container CSS adds minimum
width constraints and mobile title wrapping, not geometry/algorithm changes.

Production page before/after: 2872/2901 total lines, 2638/2663 nonblank;
native controls 43/0, MUI controls 0/43. AST test compares non-rendered
statements/functions/state/effects and every original ordinary control attribute,
handler and option child preservation is also visible in the source diff.
Only control names, explicit equivalent submit type, textarea inputProps and
accessible names are exempted from attribute equality.

Readonly dependency copy into own frontend/node_modules:
`cp -R /Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-main/frontend/node_modules frontend/node_modules`.
No donor writes, install or broad suite execution. Own HOME/cache/tmp used by
dev servers. Verification outputs below are relative to the absolute root.

Final focused commands, from frontend:
```sh
node --test tests/facility-controls.test.cjs > ../tmp/controls-proof/frozen-focused.log 2>&1
node node_modules/typescript/bin/tsc --noEmit --incremental false > ../tmp/controls-proof/frozen-types.log 2>&1
node tests/build-facility-component.cjs > ../tmp/controls-proof/frozen-bundle.log 2>&1
node tests/facility-controls-webkit.cjs > ../tmp/controls-proof/frozen-webkit.log 2>&1
```
Results: 2 focused AST/attribute tests pass, full TS exit 0, bundle exit 0,
WebKit component exit 0 at 390 and 1440. Real ReactDOM/MUI handlers exercise
Input onChange (`raw-header0`), NativeSelect (`soft`), Checkbox checked change,
non-edit disabled state, JSON draft/cancel and special-edit save. Captured PUT
has original CAS `component-r1`, exact authored header/diet and selected checkbox
array; no PUT before explicit save. Synthetic facility and external auth/API
stub only, not customer/operator/live backend acceptance.

## Exact Proof Boundary And Missing Proof

The component bundle uses TypeScript transpile and real React/MUI plus actual
page/provider/boundary; Next router/link adapters are mocked. It does NOT run
Next's styled-jsx compiler. CSS screenshots are component-only; no production
Next scoped-style/layout equivalence is claimed. Console errors/warnings remain
in frozen-webkit.log, including jsx attribute, onNavigate and MUI prop-type
warnings. No warning containment/filtering makes them disappear.

Both control bounds checked: left >= -1, right <= innerWidth+1. Only a control
inside a table with an actual ancestor overflow-x auto/scroll AND scrollWidth
greater than clientWidth is exempt. MUI aria-hidden measuring textarea excluded.
These checks pass for the component, not production Next layout.

Actual Next attempts, unchanged source/config: Next 16.3.8 default Turbopack and
`next dev frontend --webpack --hostname 127.0.0.1 --port 3187` both return 308
from /hospital/facility-master back to itself, with x-middleware-rewrite pointing
to /facility-master. `tmp/controls-proof/next-webpack-headers.log` records the
actual response. Executable missing proof: actual Next/styled-jsx page 390/1440
verification after the separate routing blocker is resolved. This packet does
not change proxy/config or bypass routing/auth guards to manufacture acceptance.
Dedicated servers have stopped; no port remains owned.

Failure history retained in tmp/controls-proof: initial types.log checkbox type
error; webkit.log and webkit-r2.log redirects; bundle.log init misuse;
component-webkit logs for missing NavigationBoundary/non-secure randomUUID;
later logs for hidden MUI textarea locator and component bounds failures.
coherent-webkit.log records whitespace header input expectation failure:
unchanged readString/normalization trims it. No business helper was changed;
final explicitly authored raw-header0 avoids making a new whitespace promise.
No repeated broad suites. Original master readers/fallback/default behavior is
unchanged; no global fallback-absence or modernization completion claim.

SHA256:
- page: `4a10f8fe84bbb7b228e68a85f24bd8b69caa8ea2d2722a5cc2bc52a1596ed278`
- tests/facility-controls.test.cjs: `28c90b6d23dc5c51f969d39cb904dee6e91c9dd1358dd859b7b41a3cc9e788cf`
- tests/facility-controls-webkit.cjs: `081ab1528a5581b5db21c125c58424cc8040cbc6e1347840da64e89b47230c5f`
- tests/build-facility-component.cjs: `a7996645e18d6b202abcf3a86a3bb8de644ccebe0353c5d16e77c23788ccb6b5`
- tmp/controls-proof/frozen-focused.log: `4f4c8e3c7c5ac991a210c18c833a49381e3e200af0aac3be07fe4d579374e125`
- tmp/controls-proof/frozen-types.log: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
- tmp/controls-proof/frozen-webkit.log: `9682d964c71e69139efb14892172357be42126a6fe76f4e5fdf48c6e9e784964`
- tmp/controls-proof/controls-390.png: `981aae676c582daab4d34e2b2b2e9b1ce171865660ba8706b87545047e12fab0`
- tmp/controls-proof/controls-1440.png: `2d911ca88b84495a3e23a3162f924f79607db6cff5b05eaed0c8043f9c51b1a9`

Frozen for parent source review; full requested layout acceptance remains unmet.
No main/develop/release writes, commit, push, deploy, user browser or live access.

## Parent Actual Next Verification

This section supersedes the missing-layout status above; earlier failures and
component-only screenshots remain historical evidence. The same frozen source
and unchanged proxy/config were served by Next webpack with `--hostname localhost
--port 3192`. `http://localhost:3192/hospital/facility-master` returns 200 and a
relative `/facility-master` rewrite. Contrasting 127.0.0.1 produced an absolute
localhost rewrite and self-request redirect. The owned verification hostname
mismatch is resolved without a production routing change. Read-only investigation
agrees that the evidence does not establish a production routing defect.

Parent proof directory: `tmp/controls-proof/parent-next-host-20261008` in this
worktree. `actual-next.cjs` copies the frozen interaction checks but removes the
synthetic HTML/bundle route and uses the actual Next URL. Next router, links,
page compilation, styled-jsx and app shell are real; external auth and API
responses remain clearly synthetic. This is not operator/live API acceptance.
Both 390/1440 viewport checks passed: draft cancel, disabled checkbox, exact
Input value, NativeSelect value, Checkbox state, explicit save only, one PUT
with original expected revision and exact authored fields, no page errors,
and both horizontal control bounds with only real table scrollers exempted.
The parent viewed both full-page screenshots. No global warning-filter was
introduced. The owned browser exited and Next session 23379 exited 0 after
Ctrl-C; no proof server was left running.

Commands: trusted Node `frontend/node_modules/next/dist/bin/next dev frontend
--webpack --hostname localhost --port 3192`; curl saved headers and full HTML;
trusted Node `tmp/controls-proof/parent-next-host-20261008/actual-next.cjs` saved
`webkit.log`, exit 0. This is still base cd14d8b plus the frozen staged packet;
parent integrated-SHA verification remains required after commit.

SHA256 in that proof directory:
- `actual-next.cjs`: `5817c19b13b8ac455d8c24ee547ff56567b4a3398f4db5c4b4fda10738498913`
- `headers.txt`: `9a838c56065124e86a380ce0e5bc9383675d773ea676c8fd33cc53b5ddf7deed`
- `webkit.log`: `7c93d215707a9eb6101ada02a7fca5dc02c8b826f35c2dc0e9f292ed768fa875`
- `controls-390.png`: `9b36dbe17aa66f9c9a96744397832f819894f544c5895be332faa10fc3261d68`
- `controls-1440.png`: `fb46367b8b5b008ac74d610d750b39a79c2ddd0f844339b4433a99294fcb021e`

Generated Next AGENTS/CLAUDE files remain untracked and are not release inputs.
The owned generated next-env change was restored; only the five packet files
are staged. No commit, push or deployment is claimed by this pre-commit proof.
