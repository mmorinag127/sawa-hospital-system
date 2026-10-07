# C2-1 Base Menu Response Types

Source-first packet only. Technical and live acceptance, parent review and integration remain pending. No self-approval.

Base: `95d832efe803a0373249a8f6a53abcd185f64e88`.
Branch: `codex/modernization-c2-base-menu-types-20261008`.
Worktree: `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c2-base-menu-types`.

## Actual Response Boundary

`backend/src/main.py` registers `base_menus.router` with the explicit empty prefix. Three HTTP 200 JSON operations:

| Method | Path | Handler | Documentation model |
|---|---|---|---|
| GET | /base-menus | list_base_menus | BaseMenuListResponse |
| POST | /base-menus | replace_base_menus | BaseMenuReplaceResponse |
| PUT | /base-menus/{item_id} | update_base_menu | BaseMenuUpdateResponse |

There is no DELETE operation. No registered base-menu operation is excluded. Request schemas, auth/error documentation and all other routers are excluded from this response-only OpenAPI slice, not removed from the runtime API.

Canonical producer lineage is `base_menu_service.serialize_item` (fourteen item fields), `_snapshot` (master merging plus source-aware revision), `replace_items` (created count plus snapshot) and `update_item` (unmerged item plus merged full snapshot). The four schema classes preserve that difference. No enums or ranges are added. Quantity/bag quantities and slot index remain nullable, zero remains zero; condiments is list[Any], not string[] or coerced entries. Extra keys are allowed. No date field is invented; existing serializer/extra datetime handling is untouched.

Route changes are only a schema import and documentation metadata. Existing implicit response_model=None is made explicit; documentation models live in `responses`, not response validation. Function bodies/signatures, input list validation and coercion, auth dependencies, HTTP 400/404/409/428 handling, source revision/CAS, services, quantities and runtime serialization are unchanged.
Frontend adds type-only schema import/three aliases and GET/POST/PUT generics. The editor's clientKey, string inputs, original casts, callback expressions, guards and normalizers remain unchanged. Emitted JS byte identity is defined for later execution, not claimed verified.

## Shared Exporter

Existing menu-rule AST parser is extracted, not independently copied: `scripts/registered_response_contract.py` holds the one shared discovery/schema path. Wrappers supply exact module binding, registration prefix, operation inventory, allowed documentation models and title. The existing menu-rule response_contract keyword signature and main command are retained. Its schema_builder is passed explicitly so original fault assertions patching exporter.models_json_schema remain effective before schema construction. Existing menu-rule test assertions are untouched. No staff exporter or contract_schema signature was edited.

The common path retains fail-closed async discovery, hidden/unresolved include-in-schema, unknown/conditional/duplicate/missing registration, dynamic paths/methods/prefixes, imperative router references and extra/duplicate/missing operation refusal. api_route lists are expanded; no route/schema first-candidate or default recovery. Parsing reads source text only. Neither wrapper imports auth/services/DB/app startup.

Existing menu-rule generated files were not written. Their current and base source SHA256 match exactly:

```text
9d8979b1dd9a27331ed68ef822634e0619050786f42935ff016569a1b8da8be6  frontend/src/generated/menu-rule-openapi.json
cb88be5536c9c93d309146f54f611a075ab71422a84169a6acd981bb037660aa  frontend/src/generated/menu-rule-api.ts
```

The deferred test also asserts the extracted wrapper's produced contract equals the integrated menu-rule JSON; that assertion has NOT run.

## Source Census

Exactly twelve changed/added allowed files. Counts are total/nonblank source lines, not test results.

| File | Before | After |
|---|---:|---:|
| backend/src/api/base_menus.py | 37/29 | 38/30 |
| backend/src/api/base_menu_schemas.py | absent | 39/28 |
| scripts/export_base_menu_openapi.py | absent | 47/38 |
| scripts/registered_response_contract.py | absent | 122/113 |
| scripts/export_menu_rule_openapi.py | 150/136 | 46/38 |
| frontend/src/generated/base-menu-openapi.json | absent | 285/285 |
| frontend/src/generated/base-menu-api.ts | absent | 177/176 |
| frontend/src/pages/base-menus.tsx | 59/57 | 64/61 |
| frontend/package.json | 40/40 | 42/42 |
| backend/tests/contract/test_base_menu_openapi_contract.py | absent | 169/146 |
| frontend/tests/base-menu-generated-contract.test.cjs | absent | 38/36 |
| docs/modernization/c2-base-menu-types-20261008.md | absent | this document |

## Executed Source Generation

From the exclusive worktree, both commands exit 0:

```text
env PYTHONDONTWRITEBYTECODE=1 /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python scripts/export_base_menu_openapi.py --output frontend/src/generated/base-menu-openapi.json
/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-main/frontend/node_modules/.bin/openapi-typescript frontend/src/generated/base-menu-openapi.json -o frontend/src/generated/base-menu-api.ts
```

Only isolated schemas, shared source parser, standard library and Pydantic are imported by generation. Dependencies are read-only, no installation or application startup. Source generation is not test proof. Other commands are source reads, git status/rev-parse/show, awk counts, shasum and explicit twelve-file staging/patch hashing. No tests/build/TS/lint/runtime server/DB/API/browser/network/solver/OCR/commit/merge/push/deploy/other-chat action. No other worktree writes.

## Unexecuted Definitions And Remaining Proof

Backend definitions compare actual registered router response metadata and schemas, None runtime models, visible operator dependency, exact inventory and generated source freshness; full API module AST parity after only approved docs removal; unchanged service bytes; three HTTP response/request serialization cases with zero/null/raw mixed condiments/unknown keys/datetime/CAS header; actual serializer/snapshot merge with a clearly labeled source double; hidden before schema generation; unknown/dynamic/async/imperative/missing/duplicate/extra inventory refusals; and menu-rule output/base-file identity.
Frontend definitions compare emitted JS including comments and all four existing editor types, three actual generated-alias consumption sites, complete inventory with no DELETE, raw condiments and nullable quantity/slot schemas.
No definition was executed. HTTP auth/service doubles and snapshot source double are component checks, not formal DB or live producer acceptance. Consolidated definition execution, full TS, emitted-JS/source parity, generated check, existing auth/CAS/source revision regressions and required integrated/live verification remain pending. No original assertion is changed or weakened to obtain a pass.

## Frozen Source SHA256

```text
843f42d881432806b158ff4c7e9ac3db53caceecf660b881beffc4600d797f51  backend/src/api/base_menus.py
c070b55588676b6fce722f9b066c1d068a31f4b7b71490f084c5ba6c8feb0bc6  backend/src/api/base_menu_schemas.py
84e57ae60267b41127b980da77336791d2044ad4234bbc1493b17a99721f0a8a  scripts/export_base_menu_openapi.py
a7540cb51e93dfb88fa602e487320fc83381b7f3a3584771ec2e8137dcd0304a  scripts/registered_response_contract.py
7b00dd037275ce9459f771ee9b10baa800b541b3228333286a2a67fc5ff823ac  scripts/export_menu_rule_openapi.py
bfd65045b554fb704e73799c4b6753f0f15cdfff0ee6542f86d0bbc7479dff3b  frontend/src/generated/base-menu-openapi.json
21ec6050b83bc3069a75b141aa2e9b09e6b8318bc6d80136e48478f318655872  frontend/src/generated/base-menu-api.ts
91c81986afc4cf23399d72117119b1180dbff38a3d9e116de32c76c5776347b7  frontend/src/pages/base-menus.tsx
d367aabf211d0ba82531133f4b5d227e94cef7faaff56ffc7bddd6c9b3563487  frontend/package.json
2429f079cb5d8430546e00ec2afc6a7823506917bb9704c70b49823ae6e83ac7  backend/tests/contract/test_base_menu_openapi_contract.py
eb6a8edff1780757d8139444bf9a67e6bdccbf286bfcb1f9ef5eb1923bc0d60d  frontend/tests/base-menu-generated-contract.test.cjs
```

Document SHA256 and cached binary patch SHA256 are reported externally to avoid self-reference. Parent monitors/reviews/integrates; source freeze is not technical completion.
