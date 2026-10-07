# H1 Hospital UI Handoff

Worktree: `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-h1-ui`
Branch: `codex/modernization-h1-hospital-ui-20261006`
Base: `ea7ca6a83a454f72f4b7179beed6820f3d918ca9`

## Implemented

| Page | H1 change | Preserved behavior |
| --- | --- | --- |
| `base-menus.tsx` | Standard `SawaProvider`, session boundary, TanStack Query list, paged `DataTable`, RHF-backed `EditForm`, and leave guard | Existing rows use `PUT /base-menus/{id}`. New and delete use an explicit full-canonical-list `POST /base-menus`; response `items` must be an array, reload errors stop the operation, every replacement payload is compared as a multiset with the re-read result, and a new row must be uniquely re-identified before success is shown. Filtering never supplies a partial replacement list. Empty numeric input remains `null`; `0` remains explicit. |
| `menu-rules.tsx` | Standard session boundary, per-rule-type Query list, paged `DataTable`, `EditForm` CRUD, and leave guard | global/menu/facility rules, facility ID, exact/contains/regex, daypart, category, diet, unit, quantity, priority, active, create/update/delete remain. Create/update/delete require their actual API success response and re-read the saved record. |
| `facility-master.tsx` | Session/provider boundary; normal facility `EditForm`/RHF; removed facility-ID-specific diet defaults; reload/save generation guard | The JSON master and unknown fields remain intact. Normal-form save requires `updated: true` and the returned master must contain the saved facility. FAX-column, aliases, area, and label specialized editing remain on the same full-master save boundary. Missing `daily_label_comparable_diet_types` is unconfigured rather than inferred. |
| `facilities/[id].tsx` | Session/provider boundary; RHF `EditForm` for normal facility/config fields; reload/save generation guard | Existing config/version JSON, resolved-config display, validation, authorization path, and workbook upload remain. Normal config save preserves unknown JSON keys, requires `updated: true`, then reloads `GET /facilities/{id}` before resetting. JSON/workbook dirty remains independent from normal-form success. |
| `menus/index.tsx` | Session/provider boundary | Existing latest-month navigation and permission/error messages are retained. |
| `menus/[monthId].tsx` | Session/provider boundary; upload history is a `DataTable`; month/load/save generation guards | Upload, source file/history, scope, master-check resolution, condiments, matrix sheet, and exception editor remain. |

## Remaining Implementation

- Convert monthly normal item editing and master-difference rows to `EditForm`/`DataTable`; keep the matrix sheet and exception controls specialized and connect their dirty state to the same navigation guard.
- Add page-level dirty/selection generation guards to the remaining facility and monthly legacy editors. Do not alter the separate in-progress GET-root worktree fix.
- Add H1 test code after all page migration is complete. No tests have been run.

## Unverified

The task explicitly prohibits test, build, typecheck, lint, audit, devserver, DB, network, and browser execution. None were run. No stage, commit, merge, push, deploy, package addition, or workbook edit was performed.

## 2026-10-06 Facility Master Freeze

- `GET /facilities/{facility_id}` exposes `resolved_config.facility_template_version` for display. The current backend has no period-version create/update endpoint; the page displays that resolved version and does not invent one or send unconfigured period fields as if they were supported.
- Existing `valid_from` / `valid_to` keys in the facility-master JSON are editable through RHF. When either key is absent, the form explicitly shows that it is unconfigured and does not add a default.
- RHF dirty and specialized FAX/JSON dirty are combined for selection, reload, and SPA leave protection. Failed saves keep the local values; stale save and reload responses are rejected by edit generation.
- `base-menus.tsx` and `menu-rules.tsx` retain their response verification, full-canonical-list replacement protection, error propagation, real pagination, and StrictMode-safe mounted flags.
- `facility-master.tsx` now bridges current RHF values to its parent save boundary. The outer specialized save merges the bridge value with the current FAX/alias/area JSON before `PUT /facility-master`; canceling reload throws and does not reset to an old draft.
