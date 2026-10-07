# Legacy Registry Source Retirement

Base/HEAD: 4677ef58dd94c5c99eef228b03691bf6cace8a75.
Branch: codex/modernization-v1-hospital-registry-retirement-20261008.
WT: /Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-v1-registry-retirement.

Failure class: unused alternate Firestore facility/template storage and resolver helpers remained callable after their HTTP routes became 410. Shared boundary is ocr_registry_service.py. Invariant: this module retains only the original unclassified reads and client/project environment resolution, not facility/template writers or noncanonical resolver publication.

## Caller Census Before Deletion

Searches covered repository source/scripts/tests/config, hidden tracked text (excluding Git metadata, tmp and node_modules), module/name references, import aliases, dynamic-import forms and collection constants:

- backend/src/api/ocr_registry.py:5 imports the module without alias.
- Same file:69 calls list_unclassified(status=status, limit=limit).
- Same file:75 calls get_unclassified(job_id).
- No direct member import, aliased module consumer, legacy method call, registry dynamic-import string or registry constant import was found.
- backend/src/main.py:18/69 imports/registers the API router, not the service helpers. Registration remains unchanged.
- API same-name legacy functions call only _legacy_ocr_registry_disabled. Eight template operations, four facility operations and resolve remain registered 410 paths. Their complete API file, auth dependencies, signatures, docs and training operations are untouched.
- docs/strict_atomic_code_db_review_20260506.md:394 and docs/ocr_order_extension_implementation_plan.md:203 mention the service historically, not executable consumers.
- infra/terraform/envs/stg/main.tf:95,97,98,100; dev/main.tf:89; prod/main.tf:105 configure collection environment names. They do not call service helpers and remain unchanged.
- ocr_pipeline/app/rois.py:13-14 independently reads OCR_TEMPLATE_COLLECTION/TEMPLATE_COLLECTION; ocr_pipeline/tests/test_template_collection_config.py exercises that path. These are NOT imports/callers of the retired module and are NOT retired here. No repository-wide canonical/fallback absence claim.
- Dynamic-import search found backend/scripts/backup_and_rerun_week_ocr.py:203 importing os only. Generic getattr usages found elsewhere do not reference this registry module. Searches do not prove arbitrary runtime-computed strings absent in external deployments.

Removed nine unused helpers: list_templates/get_template/save_template/delete_template; list_facilities/get_facility/save_facility/delete_facility; resolve_unclassified. Also removed TEMPLATE_COLLECTION/FACILITY_COLLECTION/JOBS_COLLECTION, _utc_now and datetime/timezone import. Kept list_unclassified/get_unclassified/_client/_project_id/UNCLASSIFIED_COLLECTION and _CLIENT source unchanged. No project env precedence/client cache change. No legacy data was accessed, deleted or reassigned.

Service total/nonblank lines: 118/86 before, 39/28 after.

## Evidence And Pending Verification

Read-only shell commands used cat, rg --files -g AGENTS.md, rg module/symbol/constants across the repository, rg __import__/import_module/import-star, git rev-parse, git show with awk counts, and shasum. Applicable root AGENTS and existing h1-json-canonical-common-entry-20261007.md registry boundary notes were read.

Created four source-AST test definitions: removed names/no Firestore writes, byte-identical reads/client/constants, entire unchanged API plus 13 disabled bodies/410 literal, tracked Python import/alias/call census. They read source and Git blobs only, never import the Firestore service. No tests/build/Python or Node imports/server/browser/DB commands were executed. No processes launched; no commit/push/merge/deploy or other-WT writes.

SHA256:
- Service: c994c255aacd0659642814194a9462aacbd7979be5d925261a6120a7e9ce9d8e.
- Test definition: f6ad2f839f8242b3d53558fb726719978be2e7c29d8ffabac0bac3f48c3ba198.

Pending, not waived: parent source monitor/integration, execution of the AST contracts, formal HTTP authorization/410/unclassified/training regressions in the permitted consolidated runtime. This packet is source retirement only, not proof of complete facility single-source behavior or production acceptance.
