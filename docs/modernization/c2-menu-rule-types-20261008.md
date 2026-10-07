# C2-1 Menu Rule Response Types

Source-first packet; no technical, live or whole-unit acceptance is claimed.
Base: `9e94528ad972862bb5f3cf4f5c409cec360bf1e1`.
Branch: `codex/modernization-c2-menu-rule-types-20261008`.
Worktree: `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c2-menu-rule-types`.

## Registered Boundary

`backend/src/main.py` registers `menu_rules.router` once at `/menu-rules`.
Four operations, all existing HTTP 200 JSON success responses:

| Method | Path | Handler | Documentation schema |
|---|---|---|---|
| GET | /menu-rules | list_menu_rules | MenuRuleListResponse |
| POST | /menu-rules | create_menu_rule | MenuRuleSaveResponse |
| PUT | /menu-rules/{rule_id} | update_menu_rule | MenuRuleUpdateResponse |
| DELETE | /menu-rules/{rule_id} | delete_menu_rule | MenuRuleDeleteResponse |

No menu-rule operation is excluded. Other routers, request bodies, request validation and auth/error response documentation are outside this generated response-only slice. It is not a complete replacement for application OpenAPI.
Exporter reads the actual registration/decorator AST, including FunctionDef and AsyncFunctionDef. Literal api_route method lists are expanded before checking the exact four-operation inventory. It refuses hidden include_in_schema=False, non-boolean/unresolved visibility, dynamic path/method/prefix metadata, duplicate metadata, extra/missing/duplicate operations, unknown module binding, conditional/nested registration, imperative router references and unsupported router construction/registration metadata before schema creation or output writes. Explicit include_in_schema=True is accepted. It imports only response schemas, Pydantic and the standard library, not actual auth/services/database, and does not start FastAPI. The deferred contract test compares the response slice against an isolated app including the real router.

`_serialize_rule` supplies thirteen fields. The five docs-only models permit additional properties; all existing route response models remain effectively None, explicitly spelled `response_model=None`. Models are attached only through OpenAPI `responses` metadata. No function signature/body, auth dependency, error handling, serializer, revision service, request coercion or database source was edited.
List revision is nullable because an unfiltered list returns null. Saved revision is a string. Quantity is nullable number, priority nullable integer; zero and null are not defaults. Created/updated dates are not serializer fields; extra runtime keys (including dates) remain unfiltered. Rule type remains a wire string rather than inventing a response enum from editor choices.

Four response aliases now parameterize actual GET/POST/PUT/DELETE consumption. Existing `MenuRule` editor strings, `RuleSnapshot`, guards, casts, bodies, callbacks and normalizers are retained. The additional import is type-only. Emitted-JS byte parity including comments is defined but NOT executed.

## Source Counts

Counts are total/nonblank lines (not executed AST counts).

| File | Base | Current |
|---|---:|---:|
| backend/src/api/menu_rules.py | 50/40 | 51/41 |
| backend/src/api/menu_rule_schemas.py | absent | 46/31 |
| scripts/export_menu_rule_openapi.py | absent | 150/136 |
| frontend/src/generated/menu-rule-openapi.json | absent | 303/303 |
| frontend/src/generated/menu-rule-api.ts | absent | 199/198 |
| frontend/src/pages/menu-rules.tsx | 74/70 | 80/75 |
| frontend/package.json | 38/38 | 40/40 |
| backend/tests/contract/test_menu_rule_openapi_contract.py | absent | 164/135 |
| frontend/tests/menu-rule-generated-contract.test.cjs | absent | 39/36 |

## Historical Generation Commands

Executed from this worktree before the monitor correction, exit 0 each. These exits are historical, not verification of the corrected exporter:

```text
env PYTHONDONTWRITEBYTECODE=1 /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python scripts/export_menu_rule_openapi.py --output frontend/src/generated/menu-rule-openapi.json
/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-main/frontend/node_modules/.bin/openapi-typescript frontend/src/generated/menu-rule-openapi.json -o frontend/src/generated/menu-rule-api.ts
```

The runtime/dependencies were used read-only. No dependencies installed. Generation is source production, NOT test proof. The correction did not regenerate or execute/import the exporter or any tests. Existing generated files, schemas, route handlers, frontend page and scripts are unchanged. No tests/build/tsc/lint/server/browser/network/API/DB/OCR/solver or application startup ran. No commit/merge/push/deploy or other-chat action.
One manual patch attempt failed package.json context matching before application; the corrected patch was applied. No failed verification was converted to a pass.

## Deferred Verification

Backend definitions: exact registry/model metadata versus real router, generated JSON freshness, full module AST parity after removing only docs import/metadata, response_model=None, existing auth dependency, serializer field census, nullable number/integer contracts and four HTTP serialization cases retaining zero/null/unknown nested keys/datetime. HTTP cases use explicitly labeled service/auth component doubles, not live behavior proof.
Frontend definitions: byte-identical transpiled JS with comments, unchanged editor type, all four real consumption aliases, exact operation census and nullable/extra-key contracts.
Additional unexecuted source-level definitions cover hidden/dynamic visibility stopping before schema generation; existing async route preservation; extra async/duplicate/imperative registration rejection; api_route extra/duplicate/unresolved methods; unresolved paths; explicit visible api_route preservation; hidden/dynamic/expanded/conditional app registration refusal; and missing-operation refusal. Source text is supplied through explicit main_source/route_source parameters. No builtins.compile replacement or recursive AST/source-reader mocks are used. All previous schema, runtime-null/zero/extra-key, module-AST and emitted-JS assertions remain.
Still pending: execute these definitions, generator check, full TS and existing auth/CAS/revision regressions, actual HTTP producer checks and any required integrated/live acceptance. No source-only review substitutes for those proofs. Parent review/integration remains required; this worker provides no self-approval.

## Frozen Source SHA256

```text
bb44d93eb9f82da440a49ed0dda5de6ba1b503bd286d6dfababcdb2ea45d7612  backend/src/api/menu_rules.py
6ddd05658414467ff90b1d6e0f89327554be71d5b6372bcdb5c26e701fb04c19  backend/src/api/menu_rule_schemas.py
f84ec7b1caa792a11ee24c6acc6157512ea6405715a78a419388d453f7239f4b  scripts/export_menu_rule_openapi.py
9d8979b1dd9a27331ed68ef822634e0619050786f42935ff016569a1b8da8be6  frontend/src/generated/menu-rule-openapi.json
cb88be5536c9c93d309146f54f611a075ab71422a84169a6acd981bb037660aa  frontend/src/generated/menu-rule-api.ts
fd41f2e2a8cc8afaaf1dbfeb55f812212916771e72a6d86e78fedd10a195c2d1  frontend/src/pages/menu-rules.tsx
c181aedbc400363e6019703784a05ab0c671b92eb75dfc71fc107a0a475f59fc  frontend/package.json
2ea2d3a29012c3f584b8a7ca0f3563e883e7e2b6e7892b951f5944c856b1727c  backend/tests/contract/test_menu_rule_openapi_contract.py
ab8ad483d7d168779cfaf2d980214f1f23ecee4fa219c3c83942d0440c33db21  frontend/tests/menu-rule-generated-contract.test.cjs
```

Document hash and cached binary patch hash are reported externally to avoid self-referential hashes. Freeze uses explicit ten-file git add, git status, git diff --cached --binary | shasum -a 256 and file hashing only. No technical checks are implied by these source bookkeeping commands.

## Monitor Correction History

Packet `0477c862233656e84f714f5090ffe7f2704c7d52aa9fef2c9a99423121678b25` was NOT OK: source discovery could silently omit async or hidden registered routes. It is withdrawn, not an accepted packet. Its exporter hash `0df23e245bf2347b94be57ffc7759a0c5271b0a2868c8008882613ce51e94519`, backend test hash `38a73764801c0f120c91766a2aa191d562194eb5a0487864c4935dc8140cf57a` and document hash `fa6fd21accda2c7158dd2b538d9a528b62d3edf13e07c30c1f20442b97d2316f` are historical only. This correction changes only exporter, backend test definitions and this document within the same ten-file packet. Source-only review, execution of all definitions and technical/live acceptance remain pending; no self-approval is supplied.
