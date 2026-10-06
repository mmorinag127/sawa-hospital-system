# H1-2 Saved Monthly Menu Import Source Contract

## Source Boundary

### Full Residual Corrections

Normal source fixtures now contain their declared items and entries (12 or 2), actual plan/issue coverage, producer-enriched item keys and full issue patches/candidate DTOs. The editor's own review mock resolves issues into explicit chosen plans; business-negative unresolved cases retain full issue structure rather than malformed dictionaries. Existing assertions and missing/stale negative cases are retained. Two Python normal fixture paths include calls to the actual nested receipt validator as source cases. No production validator was relaxed.

`test_real_workbook_save_review_apply_passes_nested_producer_contract` defines a real XLSX source path without mocking parsing: regular, soft, mixer and unlabelled/null variants, an actual recorded worksheet/full SHA, October 31, five slots for each of breakfast/lunch/dinner, saved-only initial receipt, explicit create resolutions with quantity zero, own review CAS, own apply CAS, and stored row/receipt validation after each boundary. These source definitions are not executed evidence; runtime/DB/browser/type checks remain deferred.

- Exception save adopts the own common snapshot without selecting a facility entry absent from that snapshot. Its receipt is separately bound to month, originating entry and actual facility entries. Explicit sheet selection is required; list editing with multiple positions asks for a sheet position rather than choosing one. Dirty item context retains the explicitly selected entry/item and original item CAS base, including when a refreshed collection omits that target. No effect selects the first entry.
- Both actual scripts use `authored_selection`: `scope_override` and `diet_types` keys are mandatory before upload/resume/review, with explicit null distinct from absence. Lists are passed as the normal API JSON query; authored null leaves that query absent deliberately. `validate_receipt` requires exact recorded food types as well as original SHA, worksheet, scope and month. Historical metadata/manifests missing either authored key stop; no all-food/base supplementation. Read-only baseline collection remains a non-upload path.
- `_index_menu_master_resolutions` rejects duplicate choices and overlapping normalized aliases across choices before any master mutation. Alias spellings within one choice remain valid; no first/last-wins assignment.
- `_validate_import_review_nested`, called by the persisted receipt validator before list/read/preview/review/apply/download, checks actual producer fields: item name/unit/temperature/inferred-field keys; entry date/daypart/name/category/diet/slot keys; plan action plus seed fields and action-specific identity; issue patch/candidate identities; master-diff field/value identities; resolution action/source and create fields. It also checks item count and plan/issue/source coverage. Optional authored nulls, quantity zero, additional meal slots and unknown fields are retained. Numeric malformed/nonfinite values, missing nested fields and ambiguous resolutions stop before archive reparse; preview cannot repair a corrupt saved review.
- Source cases cover normal and absent common targets with a dirty editor, late exception success/error, nullable/list/missing/mismatched script food types and scope, duplicate/normalized/unique resolution aliases, corrupt nested receipt across all five saved-source callers, and authored nested null/zero/unknown fields. Existing master truth, unrelated facility/food type, month crossing, repeated import and four-plus all-meal quantity cases remain unchanged. These definitions have not been executed.

### Persisted Receipt Schema

Persisted source requires all seven keys: `file_uri`, `filename`, `content_sha256`, `sheet_name`, `scope_override`, `diet_types`, `media_type`. URI, basename, actual worksheet and media type are nonempty strings; SHA is the full lowercase 64-character digest. Only `scope_override` and `diet_types` accept authored null: base scope and food types actually represented by the original respectively. Missing keys, empty strings/lists, numeric substitutes and malformed normalized scope/food types stop; they never mean base or all food types.

Persisted review requires `issues`, `plans`, `item_count`, `master_diffs`, `entries`, `items`, `scope_override`, `resolutions`, `input_snapshot`. Arrays contain objects, plans contain object values, count is a nonnegative integer (zero retained), scope equals the recorded source, and input snapshot contains a positive integer revision and full digest. Saved and current review DTOs are validated independently, not replaced by each other. Unknown source fields and review/input-snapshot ownership metadata remain recorded; explicit review recalculates business preview fields without repairing absent persisted input.

`_saved_import` and `_import_token` enforce this schema before archive access/current-input hashing. Coverage includes list serialization, read, preview, review, apply and download. Initial unpublished creation is an explicit new-row-only boundary; no persisted missing review is initialized by fallback. Frontend list/adoption and both actual scripts reject incomplete source/review material before commands. Source cases cover every missing source key across five callers, malformed values, all missing review keys, explicit null, zero, unrelated scope, and unknown ownership preservation. Runtime proof remains deferred.

- Worktree: `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-h1-menu-import`.
- Branch: `codex/modernization-h1-menu-import-20261007`.
- Start SHA: `79932bb37ec4c081df61fdfc0e2a323638612a7d`.
- Approved H1-2 plan: `docs/maintenance/sawa-codex-implementation-plan-20261004.md`, read from the existing `menu-master-provenance-20261001` tree. Existing H1 editor revision contract and hospital UI handoff were read before changes.
- Next Pages Router local `use-router.md` and `05-custom-app.md` were read from `shift-main/frontend/node_modules/next/dist/docs`. No dependency installation or execution is required for this source handoff.

This is a source-only API/UI unit, not runtime, staging, or overall modernization acceptance. No order, OCR, lineage worker, or sibling worktree source is changed.

## Actual API Flow

| Caller | Source and command contract | Own result |
| --- | --- | --- |
| `POST /monthly-menus` | Explicit month, original workbook, selected worksheet and scope; optional explicit food types. Saves source and review only. | Immutable `MonthlyMenuImport` ID, source URI/full SHA256/original filename/actual worksheet/media type, saved review and revision. No `created` claim. |
| `GET /monthly-menus/{month}?editor=true` | Owned consistent read of actual stored month or uniquely recorded adjacent source covering requested dates. | Complete editor body, explicit `source_month_id`, paired `revision`. Missing authority stops; no runtime repair. |
| `GET /{month}/imports/{id}` | Exact receipt identity, archive digest verification and current diff in one read snapshot. | Saved `review`, `revision`, `input_revision` remain unchanged. Separate `current_review` and `current_input_revision` identify the actually read current input. |
| `POST /{month}/imports/{id}/review` | `If-Match` captured saved receipt; JSON `resolutions` and `input_revision` captured with current diff. Both are compared in the owning write transaction. | Persisted review and next receipt revision, constructed before session commit/close. |
| `POST /{month}/imports/{id}/apply` | Exact saved review `If-Match`, no latest GET or replacement resolutions. Archive verification, stored resolutions and actual current input comparison precede writes. | Own receipt plus complete after-flush snapshot, item count and replacement result. |
| `GET /{month}/imports/{id}/download` | Exact stored source URI, SHA256, filename and media type. | Verified bytes; no latest upload substitution. |
| Item PUT, exception POST, master-check resolve POST | Captured complete editor `If-Match`, explicit target; shared `monthly_editor_command`. | Original business output keys plus own paired `snapshot`. |

Missing preconditions are 428, stale/invalid revisions and known serialization/lock races 409, missing formal schema 503. Source/archive errors stop without applying. Other database errors are not swallowed. Root upload `review_resolutions` is rejected: review and apply are separate explicit commands.

Default monthly GET and facility projections retain their existing business read behavior for dashboard and order-linked rendering. They do not supply the strict editor command precondition. No broader legacy read fallback acceptance is claimed here.

## Transaction And Business Preservation

`begin_monthly_snapshot` refuses any existing caller transaction or pending objects before SQL, without committing, discarding or rolling back caller edits. Fresh owned PostgreSQL transactions use REPEATABLE READ for reads and SERIALIZABLE for commands; SQLite issues actual BEGIN / BEGIN IMMEDIATE. A monthly authority is locked before receipt selection. Receipt version CAS and monthly CAS participate in the same transaction as parsing plans, actual master writes, monthly rows and audit rows. DTOs are copied before the owning session closes; response snapshots follow own flush, not a latest reload.

The input digest covers actual target-month parent/items/entries, master candidate catalogue used by name resolution, and active rules used by parsing. No historical source is inferred. Formal `0031`, after `0030`, creates saved receipts and authorities for actual existing months; historical imports are not fabricated. A new month authority is created only as part of the explicitly saved new-month receipt transaction. Missing authority for an existing month is an explicit migration blocker.

An original archive failure does not continue as a verified upload. Multi-sheet workbooks require an explicit worksheet unless exactly one actual worksheet exists; that actual name is persisted. Every preview/apply/download uses the saved archive and verifies its full digest. No filename, latest upload, first sheet, seed, or changed file is substituted as the original source.

Existing parser, normalization, master resolution and quantity coercion paths are reused. Base/tag/facility scopes remain explicit. Replacement deletes only the actual imported food types in the selected scope; unrelated food types and facility exceptions remain stored. Null food type and numeric zero are retained. Master differences are reviewed without automatically forcing source values over existing master truth. Exception edits remain separate commands. Repeat/deduplicated imports, requested/source month crossing and all meal slots (including four or more) retain the existing business paths. No facility effective-date policy is introduced.

## UI Ownership And Caller Inventory

`menus/[monthId].tsx` uses `useMenuInputOwner` to bind asynchronous adoption to mounted route, actual browser auth generation, working-input generation and per-operation generation. Native input/change capture and actual button selection changes advance the same input generation. A saved review also retains an editing-start checkpoint; submitting a later command does not silently recapture a changed input as its base.

Actual asynchronous functions checked: `loadUploadHistory`, `loadScopeOptions`, `loadRecentMenus`, `loadSavedImports`, `loadMenu`, `handleUpload`, `resumeImport`, `downloadImport`, `confirmPendingReview`, `applyMasterCheck`, `handleDownloadUpload`, `handleCondimentUpload`, `saveItem`, `saveEntryException`, and EditForm `onSave`/`onReload`. Own successes, errors and resets are guarded. Busy cleanup can clear only the same operation without adopting its stale data. File downloads use exact captured source identity and revoke blob URLs. Item, exception and master commands use editing-start revisions and own result snapshots; they do not GET latest to supplement a command. Metadata-only reads do not reset working input.

`menus/index.tsx` guards the existing latest-month navigation against later typed month, route and auth changes. Explicit target-month navigation is available when no stored latest month exists. `hospital-dashboard.tsx` remains a read-only legacy monthly consumer.

The parent authorized the two actual script upload callers after the initial 16-file freeze. Both now use the saved receipt protocol:

- `scripts/verify_stg_monthly_menus.py::upload/main`: upload multipart contains only the original; explicit sheet/scope query selections are retained. `saved-import.json` and `review-material.json` are written before any approval-dependent command. A subsequent manifest case can provide the exact `saved_import_id` and `import_review`. No resolution is selected from `production_masters`; that catalogue remains a parity assertion for an explicitly selected create resolution. Original replacement authorization hash, URI restriction, source SHA256, month restriction, entry count, quantities, reload stability and unchanged-existing-master assertions remain.
- `scripts/import_prod_menu_upload_to_stg.py::_upload_to_stg/main`: fetches the exact production upload log/month, verifies original bytes against its recorded full SHA256 (or an explicitly verified `--source-sha256` when unrecorded), and preserves recorded sheet and base/tag/facility scope. Missing scope is unresolved, not a base/facility guess. First upload reports `applied: false` and saved receipt/material then stops without review approval. Explicit `--saved-import-id` plus `--review-file` resumes the selected saved receipt. Existing source connection, GCS and authentication mechanisms are unchanged; no provisioning or execution was performed.

Both share `validate_receipt`, `review_material` and `apply_saved_import` in the verification script. The approval JSON must exactly match the read material fields `receipt_id`, `month_id`, `source`, `revision`, `input_revision`, `current_review`, and include an explicit `resolutions` list. It is supplied by the operator, never generated as approval. Each unresolved issue or master difference needs one explicit source-name resolution; no first candidate, quantity supplementation or automatic approval is permitted. No approval means no review/apply requests. Changed source/target/read versions stop.

Review POST carries the captured receipt `If-Match` and captured input revision. Only the own saved-review response supplies the apply token; no intervening latest GET is used. Apply is a real POST with zero body, not an accidental GET. Review and apply responses are recorded as responses; only validated own `created`, applied receipt/source identity and paired target snapshot produce an applied result. Missing/stale/unresolved/HTTP failures are not automatically retried. Previously saved receipts can be explicitly reopened, reviewed and submitted with their actual captured versions.

Script source search (`rg` over all `scripts`) found exactly these two HTTP monthly upload producers. `verify_monthly_menu_source.py` is a read-only archived-input replay; `sync_monthly_menus_prod_to_stg.py` is a separate direct database export/import utility, not an HTTP upload caller or proof of this protocol. Neither was edited or executed. No DB-sync compatibility or runtime acceptance is claimed. Normal UI upload/review/apply and ordinary item/exception/master callers are listed above; read-only dashboard/history consumers remain unchanged.

## Source Cases And Deferred Proof

- `backend/tests/unit/test_monthly_menu_saved_import.py`: saved upload without apply, closed-session own DTO, missing/invalid/stale preconditions, retained saved review versus current diff, archive loss/change, regular/soft/mixer/null food types and unrelated scope preservation, zero quantity, repeat import, post-CAS rollback, unresolved master review, adjacent-month explicit source, wrong target, competing apply, missing migration authority, caller pending/flushed transaction preservation, actual workbook five-slot/all-meal/final-week parsing, formal migration ownership, exact source download and review input preconditions.
- `backend/tests/contract/test_monthly_menus_api.py`: six normal API caller cases moved to saved receipt/review/apply and explicit editor reads, retaining count, quantity, resolution, exception and operator assertions.
- `frontend/tests/e2e/monthly_menu_saved_import.spec.ts`: actual page upload/review/apply tokens, no command-following latest GET, delayed upload success/error with newer input, delayed receipt read with newer item input, auth generation change, delayed review success/error and route change. Definitions only; none executed.
- Existing `frontend/tests/e2e/monthly_menu_editor.spec.ts` normal item/exception/master callers now provide captured read preconditions and own snapshots. Its upload review case uses one saved upload, explicit review and apply while preserving candidate choice, unit, quantity and incomplete-review assertions. `monthly_menu.spec.ts` read fixtures now provide complete editor revisions and checks; rendering assertions remain unchanged. These source migrations have not been executed.
- `backend/tests/unit/test_stg_monthly_menu_verification.py`: prior quantity/diff/target/replacement assertions retained; captured-to-own token chain, missing/stale approval, exact original/facility/receipt, unresolved own review, explicit candidate and zero preservation, original-only multipart and zero-body POST source cases added. Actual verification `main` cases exercise explicit approval versus missing/stale approval while retaining receipt output and original parity assertions.
- `backend/tests/unit/test_prod_monthly_menu_saved_import.py`: actual upload query shape, full original digest verification, missing or truncated digest rejection, actual `main` saved-only stop, explicit resume/apply, stale approval and unresolved own review with no further command. Network/DB/source dependencies are replaced only inside these test definitions; no test was run.

Formal migration `0031` continues directly after `0030`, unchanged in this caller extension. Per parent scope, H2 uses `0029`; no H2 source/history was altered or renumbered. Deferred technical proof: run focused Python/API/script and actual UI cases after integration; PostgreSQL and SQLite concurrency/serialization/rollback and formal migration verification; configured actual archive storage persistence/download; delayed editing, logout and route behavior; full business parity/regression and staging verification. Operator approvals and actual original/target references must be supplied before any later script execution. No runtime, tests, build, lint, install, DB, network, server, commit, merge, push or deploy was performed for this source unit. Agent monitoring tools were unavailable; no independent monitor approval is claimed.

## Frozen File Inventory

### Dirty Master Context And Own Apply Rows

The captured item also owns the displayed facility scope: losing A's current item row cannot relabel its working editor as base. Missing captured target context stops save rather than deriving a replacement scope. The delayed disappearance case removes A's item as well as its issue and asserts that its original Facility A label remains visible.

`adoptSnapshot` retains each dirty master issue's original source context, explicit selection, resolution and captured CAS base even when an item/exception/master/import own response removes or changes that issue. New display revisions never rebase those edits. A mismatched captured base remains visibly blocked and `applyMasterCheck` stops before sending a command. `loadMenu` uses the same adoption path; only explicit discard/reload clears captured bases, and it does not select a first issue. Own successful master save clears only that command's base. Item selection retains dirty item/exception contexts independently; facility exception receipts remain separately labeled rather than replacing the common editor target.

Source cases in `monthly_menu_saved_import.spec.ts` delay a real other-item B save while A has a dirty master resolution: A disappearing, A changing, and B failing preserve A's visible editor and quantity 7. Changed own snapshots stop stale A commands without a latest GET; failed B retains the original CAS header and input. Existing item/exception late-result cases are retained. Normal base apply fixtures now return all 12 and 2 accepted items/entries, with explicit base scope, source positions, master identities and original zero/null semantics. Assertions cover all adopted sheet rows and quantities 0/1/2; master actions explicitly select their issue rather than relying on first selection.

This correction changes only the page, those two existing E2E source files and this document. Cases are definitions, not executed evidence. Type/syntax, real shared-package behavior, delayed UI behavior, backend parity and integrated runtime proof remain unperformed under the source-only restriction.

All paths below are relative to the worktree stated above; the original 16 staged files are retained and these 20 source files form the extended staged unit:

1. `backend/src/api/menus.py`
2. `backend/src/models/__init__.py`
3. `backend/src/models/menu.py`
4. `backend/src/services/menu_edit_revision_service.py`
5. `backend/src/services/menu_service.py`
6. `backend/src/services/menu_upload_archive_service.py`
7. `backend/migrations/0031_monthly_menu_imports.py`
8. `backend/tests/contract/test_monthly_menus_api.py`
9. `backend/tests/unit/test_monthly_menu_saved_import.py`
10. `frontend/src/hooks/useMenuInputOwner.ts`
11. `frontend/src/pages/menus/index.tsx`
12. `frontend/src/pages/menus/[monthId].tsx`
13. `frontend/tests/e2e/monthly_menu.spec.ts`
14. `frontend/tests/e2e/monthly_menu_editor.spec.ts`
15. `frontend/tests/e2e/monthly_menu_saved_import.spec.ts`
16. `docs/modernization/h1-menu-import-source-20261007.md`
17. `scripts/verify_stg_monthly_menus.py`
18. `scripts/import_prod_menu_upload_to_stg.py`
19. `backend/tests/unit/test_stg_monthly_menu_verification.py`
20. `backend/tests/unit/test_prod_monthly_menu_saved_import.py`

Source commands used: `git log -1 --format='%H %D'`, `git status --short`, `rg --files -g AGENTS.md`, `rg` caller/function searches, `cat`/`sed` source and documentation reads, `git diff`/`git diff --stat`/`git diff --numstat`, `apply_patch`, explicit-path `git add`, `git diff --check`, and `git diff --cached --check`. Diff checks are whitespace checks, not syntax, type, behavioral, database or runtime validation.
