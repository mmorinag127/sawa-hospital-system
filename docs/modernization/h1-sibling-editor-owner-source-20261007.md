# H1 Sibling Mutation Editor Ownership

- WT: hospital-h1-ui; branch codex/modernization-h1-hospital-ui-20261006.
- Base: c8eb78afc083890acbda32d5db8cd7387eccef7a.
- Source only. No runtime/tests/build/lint/install/network/DB/commit/merge/push/deploy.

## Root And Actual Callers

Failure: a collection command for B treated its own response as authority to discard unsaved editor A. Response ownership is distinct from collection revision ownership.

`useH1EditorOwner` captures selection/input/operation/session/route and mounted lifetime. Repeated unchanged form values do not retire a command; changed values, selection, competing operation, route start, auth change and unmount do. Each React Query mutation checks the original lease immediately before HTTP, including resumed mutations. It never supplies a newer revision.

| File / caller | Source behavior |
| --- | --- |
| base-menus.tsx deleteItem -> bulkSave -> replace | Delete B updates collection display only, retaining A's selected object, form mount, dirty flag and original revision. Selected-item deletion requires explicit dirty discard plus delete confirmation; only that own target may clear its editor. |
| base-menus.tsx EditForm onSave -> update / bulkSave | Own selected update/new save adopts only validated own response; input/selection/auth/route/operation changes stop adoption. Original If-Match and full replacement comparison remain. |
| base-menus.tsx choose / onReload / reloadList | Explicit selection/reload discard remains; manual list refresh does not reset editor. Late list-refresh notice is guarded. |
| menu-rules.tsx deleteRule -> remove | B cannot clear A; deleting selected A clears only after original owner still matches, including invalidation await. Errors use original owner. |
| menu-rules.tsx EditForm save / reload / choose | Shared lease, original baseline revision, dirty consent and own response retained. Global/menu/facility sections use the same functions. |
| menus/[monthId].tsx loadMenu / selection effect / saveEntryException | Command-after snapshot cannot replace dirty item or master input; selection effect and exception receipt cannot auto-select another entry while item editor is dirty. |
| menus/[monthId].tsx updateMasterCheckResolution / applyMasterCheck | Track actual per-issue input changes. Own save releases only its unchanged issue; other dirty issues remain protected against snapshot replacement. |

Read scope: actual base/rule page functions and query mutations; useH1LeaveGuard; monthly selectEntry/loadMenu/saveItem/saveEntryException/master-resolution callbacks and EditForm adapters; facilities/[id] loadFacility/saveFacility/saveConfig/reload/workbook paths. Facility save clears only its own form dirty state and does not increment the shared form key on sibling save; loadFacility already refuses dirty snapshot adoption. No facility API/CAS/business changes were made.

The monthly page in this older WT predates the separately assigned saved-import/CAS unit. These narrow protections preserve its existing APIs and business outputs; they are not a replacement for that parent's pending import integration or proof of all monthly auth/route/read paths. Parent pending integration tree was not written. No backend or other WT was edited.

## Source Definitions And Remaining Proof

## Shared Form Rejection Boundary

Read-only actual source: platform-main/packages/ui/src/form.tsx, `EditFormProps.beginOperation`, `submit`, `reload`, `current`, `canReport`, classifier and local finally. Both base/rule EditForms now bind `ownership.beginOperation`. Save/update/new bulk and reload consume that exact captured lease using `formOperation`; they do not call start again or recapture a latest command owner. Missing beginOperation lease explicitly fails rather than emulating an obsolete package. Retired rejection remains a rejection, not a fabricated successful return.

The existing shared form checks that lease before success reset/saved state and before catch classification/conflict assignment. `canReport` is the same exact guard, so old 409 after a sibling command cannot lock the current editor. Active 409 still invokes the real classifier and conflict stop. Shared finally releases only its own form-local lock/busy; locked.current prevents a second operation in that same instance, and mounted.current prevents busy adoption after unmount. It does not change page/sibling busy, draft, revision or conflict. No platform or vendor implementation was patched.

Source definitions read actual shared submit AST from `H1_SHARED_FORM_SOURCE` (explicit final integrated source path), with the inspected platform-main source path as its local definition source. They combine that actual callback with each actual page onSave and the actual owner hook. Cases cover actual B delete retiring A before A's late 409/success; input/selection/auth/route/unmount retirement, unchanged current conflict and own success/reset. React/form/API adapters remain unit doubles; this is not installed-bundle or runtime proof.

Technical binding remains pending: frontend/package.json and package-lock.json still point at vendor/sawa-ui-0.1.0-8004321e0364f54d1294df4bb3143dc091fc5a86.tgz. Parent must pack/rebind the final shared source carrying beginOperation/current/canReport before type/build/browser acceptance. Those files were not changed and no packaging/install/test was executed here. Missing shared support is not claimed fixed by this source-only binding.

`frontend/tests/h1-sibling-editor-owner.test.cjs` extracts actual page functions/mutationFns and executes the actual ownership hook with explicit React/router/auth adapter definitions. Definitions cover dirty A + B delete success/failure/conflict, explicit selected-target discard, own new save, all lease retirement dimensions, pre-dispatch retirement, menu-rule sibling delete and delayed monthly snapshot with disappearing dirty target. API adapters expose pending promises and actual response bodies; no whole-component always-success replacement is used. These definitions have not been executed.

Remaining technical proof: source-case execution, TS/MUI/EditForm callback compatibility against installed bundle, React Query offline resume, StrictMode/remount timing, real auth/route navigation and saved-import integration; browser quantities/null/zero parity and staging. No overall H1 or runtime completion claim.
