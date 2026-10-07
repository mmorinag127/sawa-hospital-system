# Hospital shell controls: source-only packet

Base: `d55763365c3d2159dc976b130e0cc7a6de5f162f`.
Branch: `codex/modernization-v1-hospital-shell-controls-20261008`.
Exactly five files: UnifiedShell.tsx, hospital-dashboard.tsx, auth/automation.tsx,
new shell-controls-source.test.cjs and this document. No commit/integration.

Complete opening-element inventory, not matching-line count or executed AST:
- UnifiedShell: native button 1 -> Button 1 (logout).
- Dashboard: native button 1 -> Button 1 (reload).
- Automation: native select 1/input 1/button 1 -> NativeSelect 1/Input 1/Button 1.
Total 5 controls: 3 Button, 1 Input, 1 NativeSelect. No checkbox/textarea.
Original button types explicitly button/button/submit, retained; omitted types zero.
Password type, required bare boolean, disabled, raw token/system value, handlers and
options retained. Input autoComplete="off" maps to actual native input via inputProps;
form autoComplete="off"/onSubmit retained. No auth/session/token/fetch/navigation/
logout/cancellation or other business/type/hook source changes. No provider added.

Dedicated hospital-unified-shell-control / hospital-dashboard-control /
hospital-automation-control markers confine new CSS. UnifiedShell uses existing
sawa-template.css logout dimensions/declarations/hover; dashboard pairs original
retry selectors/declarations with marker globals. Automation previously has no local
control style; marked native inners/button revert MUI presentation to native styling,
retaining existing font/outline declarations. No global CSS or sibling restyle.
Actual React/MUI native styling and layout equivalence remains unverified.

Total/nonblank lines:
- UnifiedShell: 56/52 -> 57/53.
- Dashboard: 1282/1170 -> 1286/1174.
- Automation: 75/72 -> 76/73.
- New source test: 90/90.

SHA256:
- UnifiedShell: `b5b8d229e97c329394cdeb04ef47dec263b040bb5df945de7efa3fa937116a4f`.
- Dashboard: `c6a9bf4f3c8fc1c82c0ab2f7ad675545fbe1d9356fffb7872445214978a8c712`.
- Automation: `efda832cc73f289cfdd947706d7f9576ec79ed34f4defb509fe380cd6d390e8e`.
- Test: `df9b9ef038e819db370bee11ff97de59eb7494409eda40462aad93bc44a08a17`.
Document and staged binary patch hashes reported externally to avoid self-reference.

Unexecuted definitions cover parse/counts, exact nonrendered bytes, full recursive
syntax-tree branches/forms/nested callbacks, complete normalized prop key sets and
initializer presence, original props/handlers/children/options/password/required,
provider presence and component-only CSS/original declaration retention. Only exact
approved MUI tags/imports/marker/presentation/native prop mapping/added CSS normalize.
No whole-render body removal in recursive comparison. Additional nonrendered byte
check remains. No successful test execution is claimed.

Source commands: cat AGENTS/components; git status/rev-parse; rg native controls and
CSS; git show | awk baseline counts; awk current counts; shasum; git diff --stat/check
(check exit 0); explicit five-file git add. Installed Next AGENTS.md and Pages CSS-in-JS
guidance read from hospital-main/frontend/node_modules/next read-only. Combined first
source output was truncated; patches used exact bounded source context, no truncated
full-source overwrite. rg _app style search found no matching generic field/btn rules
(exit 1); actual sawa-template.css rules read separately.

All technical/live verification pending, not waived: source definition execution,
TS/build, actual React/MUI native password/required/autoComplete/disabled values,
submit and keyboard/pointer behavior, styled-jsx/native dimensions/layout, inherited
theme, logout broadcast/flushSync, auth token secrecy/session generation/fetch and
navigation/cancellation/reload flows. No tests/build/TS/lint/imports/runtime/server/
browser/network/DB/API/install/login/logout/save/deploy executed. No technical,
original-flow, live or whole modernization acceptance. Parent fresh review/monitors,
source integration and tests remain required.
