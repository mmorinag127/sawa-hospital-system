# Hospital Independent Menu UI Source Unit

- Base: `9dd29e659e6406b45db7e7cd902bafec2f331a5d`; branch: `codex/modernization-v1-hospital-menu-ui-20261007`.
- Production scope: move OrdersListScreen outside Next pages (four relative imports), update its sole facility-orders caller, public MUI input/input-label/Stack compatibility, and public monthly DataTable paging.
- Monthly paging retains all items and total, starts at 25 rows, resets on snapshot adoption/page-size change, and does not edit quantities or raw input. A 61-item test covers all pages, zero, null, and raw text.
- Menu-rule correction: an Axios-marker restriction was rejected during source review. The final callback uses only a compile-time structural cast and the original strict `error?.response?.status === 409` predicate. Actual extracted callback tests cover Axios409, plain-object409, null and 500, with identical error input forwarded to unchanged errorText.
- Existing owner assertions remain. Tests load actual installed `@sawa/ui/dist/form.js`; packed-vendor byte identity was checked. Form SHA256: `86c0279bf2b8659d829acbbff31d681a1874fe9411d1f1f45a12c91f3e478147`.
- No held orders/[id] geometry, backend, configuration, dependency, quantity, or auth-source changes.

## Owned Verification

All paths below are under this worktree. Node executable: `/Users/mmorinag/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node`.

Fresh locked installation used that Node with `/Users/mmorinag/.anyenv/envs/nodenv/versions/24.6.0/lib/node_modules/npm/bin/npm-cli.js ci --no-audit --no-fund`, cwd frontend, isolated HOME/TMPDIR/npm cache under tmp/menu-ui-proof, authorized registry access; exit 0, 281 packages. Manifest and lock unchanged.

Final command, cwd worktree:

```sh
/Users/mmorinag/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node tmp/menu-ui-proof/corrected.cjs
```

The owned runner executes Node `--test tests/h1-sibling-editor-owner.test.cjs tests/monthly-item-form-owner.test.cjs` from frontend with isolated environment: exit 0, 98 passed, 0 failed, 0 skipped. It checks the six affected production files using installed TypeScript and actual tsconfig plus next-env.d.ts: 0 diagnostics. It records exact source hashes and commands.

- Report: `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-v1-menu-ui/tmp/menu-ui-proof/report-corrected.json`, SHA256 `ee58171273cfc42f60ece39994017e2f28b457598d0fe890f91ad933296d8f1c`.
- Focused log: `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-v1-menu-ui/tmp/menu-ui-proof/focused-corrected.log`, SHA256 `810463d656cd4112810300d55d74369279decff85ea31b60af1f2a99cfcab2ac`.
- Runner SHA256: `f8c6a16fd0b18528a0fadf6972f678d27e42af6296023a27deac70bf1b258e01`.

Historical global command from frontend: Node `node_modules/typescript/bin/tsc --noEmit --incremental false`, exit 2: untouched held `src/pages/orders/[id].tsx(6651,26)`, TS2698. Retained log `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-v1-menu-ui/tmp/menu-ui-proof/typecheck.log`, SHA256 `828786cdf5b09b3b89db49b976d0afda70765de92a6f1d83ed8d9a4c92a4cc2a`. Not rerun as unchanged broad proof. Initial scoped runner omitted next-env.d.ts and reported two style-jsx diagnostics; corrected verification includes the real declaration, with historical outputs retained.

This is component/source-unit evidence, not integrated-SHA, whole frontend build, operator/browser, stg/prod, or modernization completion. No full prebuild/build success claimed. Parent review/integration and integrated regressions remain required. No commit, merge, push, or deploy performed.
