# Shipping/forms ordinary controls: source freeze

Base: 4677ef58dd94c5c99eef228b03691bf6cace8a75.
Branch: codex/modernization-v1-hospital-shipping-controls-20261008.
Exclusive worktree: /Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-v1-shipping-controls.

## Source Packet

Only three page render/import/control-CSS sections changed; new source test and this document complete the five-file packet.

| Page | Native JSX elements before | Replacement | Omitted button type preserved as submit | Total/nonblank before -> after |
| --- | --- | --- | --- | --- |
| shipping.tsx | 5 buttons, 2 inputs, 1 textarea | 5 Button, 3 Input | 3 | 646/598 -> 670/619 |
| shipping-history.tsx | 14 buttons, 9 inputs, 2 selects | 14 Button, 9 Input, 2 NativeSelect | 5 | 2435/2218 -> 2458/2239 |
| order-forms.tsx | 1 button, 1 input, 2 selects | 1 Button, 1 Input, 2 NativeSelect | 1 | 343/306 -> 361/322 |

37 original JSX control elements (mapped runtime lists can render more instances). Counts enumerate complete opening elements, not rg matching lines. Independent TypeScript AST count definitions are created but not executed.

All original button handlers/types/disabled expressions and displayed content retained, including history calendar buttons and manual/archive/delete/download commands. Original values, date/number conversions, filters, grouping, raw null/zero, constants, requests and authentication code are unchanged. This is rendering reuse, not a business/root-fix claim.

File inputs retain original single-file semantics and accept strings. accept and onChange are supplied to Input inputProps; only erased ChangeEvent<HTMLInputElement> parameter annotations added. Callback expressions still select e.target.files?.[0] ?? null without copying/replacing the File. No file selection/upload was executed.
Textarea becomes multiline Input with original rows={4}, raw trackingText, onChange and placeholder. Marked inner textarea retains vertical resize CSS.

Each absent theme boundary gets ThemeProvider with exported sawaTheme only. No SawaProvider/sessionKey/QueryClient or auth/query mechanisms added. Dedicated shipping-ordinary-control marker augments only converted control classes. Former control class selectors now target that marker globally so MUI roots receive them; all new inner-input selectors are marker-gated. TopNav/shared components and unrelated selectors are untouched.

Existing order-forms first-facility selection and DEFAULT_ORDER_FORM_PATTERNS/error fallback remain original non-rendered behavior; no canonical-source or fallback-removal claim is made by this narrowly approved render packet.

## Test Definitions, Not Results

frontend/tests/shipping-controls-source.test.cjs defines nine source tests:
- Three exact non-rendered byte comparisons after removing only approved UI/type imports and render subtrees; original functions/hooks/auth/API/constants must match base.
- Three complete AST control counts and mappings, native props flattened from inputProps, all options/children/handlers/value/disabled/ref parity; only dedicated class marker normalization, explicit native default-submit and presentation-only disableUnderline/multiline additions allowed. Erased file event parameter types are compared via TypeScript emission.
- Three newly global CSS selector checks confined to converted marker, with theme-only provider assertions.

Installed read-only MUI Input/InputBase/NativeSelect implementation/type declarations and @sawa/ui theme export were inspected. Installed Next README was read; donor Next package has no docs directory at frontend/node_modules/next/docs (rg exited 2). No network documentation fetch performed.

## Actual Commands and Limits

Source SHA256:
- shipping.tsx: 61209a61a65c9e6faaaf55af5dfad6928abda11f093f5acfa39862023f36a977.
- shipping-history.tsx: 91161735dc42ca057216aca45a7e30b68cd33887931eb7c51e65b2a590f5793a.
- order-forms.tsx: 8de753a59ec6d3a5ba3853f3e8c62a71b7e056324728f78ac5ac665082b89ba0.
- shipping-controls-source.test.cjs: f0c7d114bf1e77b386d495c775311d36359beaa7e01dca1369ce6910472fa313.

Executed: cat/sed/rg source and installed-document reads; git status --short --branch, git rev-parse HEAD, git diff --stat, git diff -- pages, git diff --check; source counts; shasum -a 256; apply_patch; exact-file git add and staged binary-patch hash. git diff --check exited 0. Other source reads and staging exited 0 except the absent Next docs lookup noted above.

No test/build/typecheck/lint/Node or Python import/runtime/browser/server/DB/network/install was executed. No upload, tracking, download, archive, delete or other API writer ran. No owned processes started; no dependency/config/other-worktree changes or commit/merge/push/deploy.

All technical verification remains pending, not waived: source definitions, actual installed React/MUI raw-input/File callback/disabled/default-submit behavior, global TS, explicit baseline/current TSX lint, integrated Next/WebKit layouts and native file selection. Real PDF/XLSX/tracking/business/auth/live flows are unverified and cannot be inferred from source freeze.

Parent source monitoring/integration and consolidated verification are required. This packet is source-only, not verified UI/business completion.
