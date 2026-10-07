# Workflow ordinary controls: source-only packet

Base: `7d7e41d2085376013a835f4e6f5904c4031b0c1f`.
Branch: `codex/modernization-v1-hospital-workflow-controls-20261008`.

## Scope And Counts

Only `frontend/src/pages/orders/[id]/workflow-v2.tsx`, `frontend/tests/workflow-controls-source.test.cjs` and this document are changed.

Full source opening elements before: 66 buttons, 16 selects, 8 inputs, 2 textareas. Of these, the single native input carrying data-sheet-row/data-sheet-col is specialized and retained. Ordinary replacements: 91 (66 Button, 16 NativeSelect, 7 Input, 2 multiline Input). All 66 original buttons explicitly specify type button; none acquire a submit default. No ordinary checkbox or file input is present. Counts represent JSX definitions, not mapped runtime instances. Source AST definitions assert these counts; they have not been executed.

Page total/nonblank lines: 7396/7172 -> 7439/7214.

## Preservation And Correction

ThemeProvider with exported sawaTheme wraps the main screen only, without introducing a query/session provider. Existing handler expressions, options, conditional branches, auth/API/state/types and non-rendered source are retained. Number min/max/step and aria-label, and textarea spellCheck, are forwarded through native inputProps. Original classes are augmented only on ordinary converted roots. Existing CSS declarations are retained in paired old/marked selectors, including compound button descendants and preview controls; native sheet selectors are unchanged. Marker-scoped inner input resets prevent duplicated native/MUI borders and padding.

Parent source review identified marker concatenation without a separating space. All eight expression-based className attributes now append exactly `+ " workflow-ordinary-control"`, retaining the original expression inside parentheses. Test definitions require that exact suffix and original expression, and compare original versus appended class tokens across active/inactive and overlay/original/sheet contexts. The prior defective source is not verification evidence.

The protected sheet-cell input, entire enclosing td/table, SVG/image/canvas source and correction events remain source-preserved; definitions assert exact text. No business root fix, source fallback change, quantity normalization or new geometry policy is included.

## Source SHA256

- frontend/src/pages/orders/[id]/workflow-v2.tsx: `212f44dd0492723a8cafc8576310e980966fb90fd801851f167dabac3a18b584`
- frontend/tests/workflow-controls-source.test.cjs: `d91074c4507346b0bdb42ac118ee9ca7302fbad7634a11aa8c999da954bc2660`

The cached binary patch hash is reported separately to avoid self-reference.

## Commands And Limits

Performed source reads via cat/sed/rg, HEAD/status/diff inspection, apply_patch edits, SHA256 hashing and staging. `git diff --check` exited 0. Applicable AGENTS and installed read-only Next README were read. No tests/build/TypeScript/lint, Node/Python application imports, servers, browsers, DB/network/API calls or installs were executed. No OCR, correction, reprocessing, evidence adoption, download or save commands were invoked. No commit/integration/deploy or other-worktree write.

All technical/runtime/live/geometry/keyboard/native-input/image parity verification is pending, not waived. Source definitions are not executed proof. This source-only freeze is not runtime, root-fix, security or whole-modernization completion. Fresh parent monitors and integrated-source verification remain required.
