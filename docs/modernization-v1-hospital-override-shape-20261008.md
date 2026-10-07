# Canonical override shape boundary

Base: `de49f34c625a3ee82684e778f16c2c9d90053a69`.
Branch: `codex/modernization-v1-hospital-override-shape-20261008`.
Own worktree: `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-v1-override-shape`.

Failure class: unchecked nested canonical configuration shape. Shared accessors
reject absent/null/scalar/array outer config and scalar/array override. An absent
or null override retains that exact absence value; neither is geometry evidence.
Backend config_validator.py `_ensure_dict` and config PUT validation establish
the optional override object contract. Normal JSON object keys/values are copied
without coercion, inferred units or defaults. Snapshot validation now checks this
nested boundary. Active geometry save/readback use the accessor. The retired
columns handler is exactly restored to its base AST; it is not migrated by this
packet. The outer helper remains used by the shared override accessor.
CAS/ownership and unit/coordinate behavior are untouched.

Read-only downstream classification: columns response still uses
`res.data?.resolved_config || null` and `resolvedConfig?.fax_template?.columns ?? columns`.
These are unchanged legacy response alternatives: orders.py:2119-2122 invokes
_raise_legacy_order_workflow_gone, whose definition at 1362 raises HTTP 410.
There is no current write producer or successful response through that route.
The unreachable old response fallbacks remain; no whole-fallback absence claim
is made. No business contract was invented.

Dependencies copied read-only from hospital-main frontend/node_modules using
`cp -R /Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-main/frontend/node_modules frontend/node_modules`.
No install or donor writes. All logs below are own worktree tmp artifacts.
From frontend, final commands:

```sh
node --test tests/facility-override-shape.test.cjs > ../tmp/override-proof/outer-final.log 2>&1
node node_modules/typescript/bin/tsc --noEmit --incremental false > ../tmp/override-proof/outer-types.log 2>&1
```

Historical outer correction: 6 tests passed, zero skips, exit 0; full TS exit 0. Tests execute actual
shared helper and snapshot guards, compare normal serialization, cover invalid
outer/nested siblings and distinct null/absence, and parse actual TSX/inspect
pre-PUT accessor placement, CAS and stale response checks. Handler source checks
are static, not browser/live flow proof.

Historical checks retained: initial types.log contained two TS errors corrected
before final verification. `final-neighbors.log` (before outer correction) reports
299 passed/1 failed, exit 1 for unchanged h2-quad-owner normal columns/copy case
at line 202: deepStrictEqual objects of the same structure not reference-equal.
No baseline reproduction establishes its cause. h2-quad-owner.test.cjs:3 reads
frontend/src/pages/orders/[id]/workflow-v2.tsx, not the modified orders/[id].tsx.
That source and test remain unchanged. The earlier claim about newly imported
accessors missing from this harness was unsupported and is withdrawn: these
accessors are unrelated to that test's source. The exact separate failure remains
unresolved; no cross-realm cause or neighbor acceptance is claimed.

SHA256, relative to own worktree:
- frontend/src/services/facilityData.ts: `185ab4bd60ad09a6df8bc9d512578b8a67b4b68f52461623b339996f80f16fe0`
- frontend/src/pages/orders/[id].tsx: `77eb60d1c89f5c45feaa900708f71d2514eb13f7ef975e5ccdc59bdc05488a1a`
- frontend/tests/facility-override-shape.test.cjs: `d4e94e568c99a45a889d96a9ca9fa1b5975a1f36727f650f3677a189e7e6b186`
- tmp/override-proof/outer-final.log: `d08edbd7eb1e72add68bf8e5c1a19122546cf5d68c3276d38ffdf09770408174`
- tmp/override-proof/outer-types.log: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
- tmp/override-proof/final-neighbors.log: `a5890395907fc7246daf252eb4e8fc9176c44f6028be7fe3d3187c2e946f4104`

Proof lineage is base plus frozen uncommitted source, not parent-integrated SHA.
No commit, main write, deploy, live access or geometry units policy resolution.

## Retired Sibling Scope Correction

Only this packet's changes inside saveFacilityTemplateColumns were removed.
The added AST test compares its complete declaration against base via git show
and TypeScript printer; it passes. No user source or held files were reverted.
Earlier source/artifact hashes above are historical.

From frontend:
```sh
node --test tests/facility-override-shape.test.cjs tests/h2-quad-owner.test.cjs > ../tmp/override-proof/retired-final.log 2>&1
node node_modules/typescript/bin/tsc --noEmit --incremental false > ../tmp/override-proof/retired-types.log 2>&1
```

Current combined focused result: 303 tests, 302 passed, 1 failed, exit 1.
All 7 shape/source/AST tests pass. The unchanged H2 normal columns/copy
deepStrictEqual failure at line 202 remains. It tests unchanged workflow-v2.tsx;
the orders/[id].tsx retired function restoration does not establish its cause.
It is not attributed to the shape patch or claimed resolved.
Full TS exit 0; source parse covered by AST tests. Parent integrated acceptance
is pending; failed historical evidence is retained.

Current SHA256:
- frontend/src/services/facilityData.ts: `185ab4bd60ad09a6df8bc9d512578b8a67b4b68f52461623b339996f80f16fe0`
- frontend/src/pages/orders/[id].tsx: `4e8e8075e5bef61625610ecc447163bf63d5b71d66616a8aa95dfbb461e9d8c0`
- frontend/tests/facility-override-shape.test.cjs: `47e88cab995c8773ca09ad24eb7b96faf9467854286de522c369f6319b6dde13`
- tmp/override-proof/retired-final.log: `01a977c796f63c51ef9b3c36704b1bd35437770051ea34375a14963f2978acc3`
- tmp/override-proof/retired-types.log: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
