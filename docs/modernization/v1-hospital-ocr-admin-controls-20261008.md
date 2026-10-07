# Hospital OCR Admin Controls: Source Freeze

- Base: `feae328e2aa7317c603bb0af9970e4cc183bcd87`.
- Branch: `codex/modernization-v1-hospital-ocr-admin-controls-20261008`.
- Scope: three pages, one unexecuted source-contract definition, this document.
- This is rendering reuse, not registry reactivation or a business/root fix. Retired HTTP410 behavior and existing legacy callbacks remain unchanged.

## Census and Lines

Counts are full JSX opening/self-closing control occurrences, including mapped controls as source occurrences, not runtime row instances or matching-line counts. AST census definitions await execution.

| Page | Button | Input | Multiline Input | Before total/nonblank | After total/nonblank |
| --- | ---: | ---: | ---: | --- | --- |
| ocr-facilities.tsx | 5 | 1 | 1 | 438/391 | 472/422 |
| ocr-templates.tsx | 5 | 1 | 1 | 433/387 | 467/418 |
| ocr-training-data.tsx | 6 | 1 | 0 | 470/421 | 505/453 |

Total: 21 ordinary controls, 16 buttons, 3 inputs, 2 textareas. No native selects, checkboxes, file inputs, SVG, image, or canvas elements occur in these pages. All 16 original buttons omitted type; explicit submit preserves native default semantics. Training min/max remain native inputProps. Original raw Number conversion, values, callbacks, disabled conditions, textarea rows and children remain unchanged.

ThemeProvider uses exported sawaTheme around main only. No query/session/auth provider was added. Each page has a distinct marker; paired original/marked selectors retain original declarations. Input-inner resets avoid duplicated native borders/padding. No shared/global CSS file changed.

## Unexecuted Definitions

`frontend/tests/ocr-admin-controls-source.test.cjs` defines exact non-rendered byte comparison, complete normalized prop-key sets and initializer-presence checks, original callbacks/children/options, whole render branches, census, explicit submit, protected-surface census, theme-only boundary, original CSS declaration retention and cross-page marker exclusion. Approved normalization is limited to conversion props, leading-space marker, native min/max mapping and omitted-button submit. Extra handlers/data props are rejected.

## Source SHA256

| Source | SHA256 |
| --- | --- |
| frontend/src/pages/ocr-facilities.tsx | 986b2c1fa73a1018e37691918b96e86fbafb8fe395eb0ea7cd63de6295d6daf6 |
| frontend/src/pages/ocr-templates.tsx | b8efabd0be0d1d20fb5bc7d7c67ce0d7f58b9720afbde3a7938078aaa7c0092f |
| frontend/src/pages/ocr-training-data.tsx | 021c310a1d10fd98e56926343f2e0fc879192aa6e908ea214a44d97cee36dc06 |
| frontend/tests/ocr-admin-controls-source.test.cjs | 22177d8b81e3a86feafe3770830f510818c3eaf2fb5eb41361ca8269bc64e4f4 |

## Commands and Limits

Only read-only source inspection, manual apply_patch, line/hash reporting and Git staging were performed. `git rev-parse HEAD`, `git status --short`, source reads, `git diff --stat`, `wc -l`, and `shasum -a 256` returned 0. Applicable AGENTS and installed Next guidance were read. A read-only search for docs/modernization-plan.md and docs/modernization-progress.md returned 2 because neither path exists in this WT; no test failure is concealed.

No tests, build, typecheck, lint, imports, runtime, server, browser, network, DB, API, installation, save, delete, export, commit or deployment was executed. All technical checks, React/MUI event forwarding, geometry, keyboard, native behavior, HTTP410 regression and live verification remain pending, not waived. Source freeze is not runtime/security/business/whole completion. Parent fresh monitors and integrated-source verification are required. Cached binary patch hash is reported externally to avoid self-reference.
