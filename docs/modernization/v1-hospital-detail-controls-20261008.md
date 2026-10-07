# Hospital detail ordinary controls: source-only packet

Base: `d55763365c3d2159dc976b130e0cc7a6de5f162f`.
Branch: `codex/modernization-v1-hospital-detail-controls-20261008`.
Exactly three files: orders/[id].tsx, new order-detail-controls-source.test.cjs,
this document. No commit or integration performed.

Scope clarification: entire OCR sheet table subtrees are byte-preserved, including
internal adoption/duplicate/delete buttons. This includes readonly OCR table,
editable OCR sheet table and facility-template OCR sheet table. Source comparison
of each complete subtree found it unchanged; unexecuted AST definitions assert this.
Protected inventory: readonly 0 controls; editable input 1/button 3;
facility-template select 5/input 4/button 3. Total 16 native controls retained.

Complete ordinary opening-element inventory (source enumeration, not executed AST):
97 button, 12 select, 13 input, 2 textarea = 124 converted roots.
After: 97 Button, 12 NativeSelect, 15 Input. Ordinary checkbox count is zero.
All native + converted opening elements total 140, including protected 16.
Three omitted ordinary native button types become explicit submit; all other
original type values retained. Button children/options/conditions and callbacks
remain original. Computed classes append SPACE + hospital-detail-control to
the original expression; static classes retain original tokens plus that marker.

ocrSwapLeftColumnRef / ocrSwapRightColumnRef: ref -> NativeSelect inputRef,
retaining each existing HTMLSelectElement ref object and callback/selected value.
Native aria/inputMode props and the native input onKeyDown use inputProps.
Textarea -> multiline Input with original rows/value/handler. No provider added.
Original CSS declarations are paired with converted-only marker globals; native
selectors remain for protected/unchanged surfaces. Inner padding/borders reset
under marked Input roots to avoid wrapper + native-inner duplication. NativeSelect
icon/underline presentation adjusted only for converted controls. No geometry,
SVG/image/canvas/pointer, source, quantity/null/zero or business function change.

Page total/nonblank lines: 16207/15422 -> 16246/15447.
New source test: 213/213 (historical definitions: 135/135, 116/116 and 76/76). Test definitions, not execution, cover full AST counts,
original props/callbacks/children/options, nonrendered bytes, computed classes,
default submit, native select ref forwarding, protected subtrees, geometry,
provider presence and scoped CSS/original declaration retention.

SHA256:
- Page: `0bdd4bd536cd25b489d791c270b671b4cb2b123e63240a6754e9485eee07113e`.
- Test: `6c92ce781080ce1f01b23b8815b91d21395ada5dca021906f3cae63ea5b50a56`.
Document and cached binary patch hashes reported externally (no self-reference).

Correction after rejected 555d07 packet: the definition now compares the complete
normalized prop key set and initializer presence, including bare boolean attributes.
Only actual bare disableUnderline on Input/NativeSelect, bare multiline on original
textarea, the class marker, original native keys in explicit inputProps assignments,
submit for originally omitted button type, and exact select ref -> inputRef mapping
are normalized. Duplicate/spread props, additional handlers/data/state props and
initializer-presence changes are rejected. All previous counts, protected tables,
ref/native values/options/default-type/CSS assertions remain. Existing value
comparison now handles a genuinely bare attribute without mistaking its missing
initializer for a missing prop. At that historical correction, page hash/counts were unchanged.
These strengthened definitions have not been executed; fresh parent review is pending.

Subsequent parent CSS NOT OK: input width 72px and select min-width 128px
both targeted MuiInput-root, applying both rules to each converted control.
Corrected only seven ocr-shift-field roots: three Input roots append
hospital-detail-shift-input; four NativeSelect roots append hospital-detail-shift-select.
Their paired CSS selectors now require distinct corresponding markers. Original
native selectors and declarations retained; rest of page/ref/handlers/protected tables
unchanged. Full prop normalization remains; definitions additionally check ancestor
scope, exact marker/tag counts and distinct correct CSS/declaration pairing.
No runtime/tests executed for this correction. Prior page/test hashes above were
superseded by the current hashes; historical 555d07 patch is not the current packet.
Source commands for correction: rg/sed/cat, apply_patch, awk/shasum,
git diff --check (exit 0), explicit three-file git add and staged binary hash.

Final definition correction after rejected 0fdbea packet: prior nonrendered masking
and individual control checks did not prove complete render branches/markup.
Added independent recursive whole syntax-tree comparison, retaining every original
render condition, markup child and nested callback expression/branch. It normalizes
only approved MUI tag mapping, native inputProps/ref forwarding, exact markers,
bare presentation props, native default submit, the exact added MUI import and
added CSS selector alternatives/reset declarations. Original CSS declarations and
all earlier assertions remain. No JSX body/branch/expression is discarded by this
new comparison; earlier nonrendered byte comparison remains as an additional check.
Production page unchanged from 0fdbea: SHA256 remains
`0bdd4bd536cd25b489d791c270b671b4cb2b123e63240a6754e9485eee07113e`.
graph-controls-source.test.cjs reference was absent here (read exit 1); no other WT
was modified. Source checks only: cat/rg, shasum, awk, git diff --check exit 0.
The new recursive definition has NOT been executed; technical/live checks pending.
0fdbea is historical/rejected, not the newly frozen binary patch.

Read-only/source commands: git status/rev-parse; rg native elements/table/ref/CSS;
cat AGENTS; bounded sed source reads; awk counts; shasum; git diff --stat/check.
Installed Next guidance read from hospital-main/frontend/node_modules/next/AGENTS.md
and dist/docs/02-pages/02-guides/css-in-js.md. Own node_modules absent; no install.
An initial guidance .mdx path was absent (exit 1); actual .md subsequently read.
Initial full-source read truncated and first apply_patch failed verification without
editing. Complete bounded reads were used before successful patch. Source diff
checks initially exit 2 for removed-attribute blank whitespace; corrected and final
git diff --check exit 0. No failed runtime tests exist because none were executed.

All technical/runtime/live verification pending, not waived: AST/source definition
execution, TypeScript/build, actual MUI inputRef/native attrs/DOM events, keyboard,
pointer/disabled behavior, styled-jsx layout/accessibility and inherited theme/session,
protected grid operation, auth/readguard/CAS/source flows and actual OCR/evidence/
reprocess/save/download. No tests/build/TS/lint/imports/runtime/browser/server/network/
DB/API/install or actual business operation executed. Source packet is not technical,
live, original operator-flow or whole modernization acceptance. Parent fresh commit
monitors/review and mechanical integration remain required.
