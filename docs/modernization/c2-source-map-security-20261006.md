# C2 source-map-js security lock evidence (2026-10-06)

## Scope and source

- Worktree: `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c2-source-map-security`
- Branch: `codex/modernization-c2-source-map-security-20261006`
- Source/base commit: `bb5218402a8cf3d0602202abc47c71f6273d151f`
- CI source: GitHub CI `37400060006`, which reported GHSA-68fv-2mgg-jv7q for `source-map-js@1.2.1`.
- Official advisory: `https://github.com/advisories/GHSA-68fv-2mgg-jv7q` (affected `>=1.0.0 <1.2.2`, patched `1.2.2`).
- Compatibility evidence: `npm explain source-map-js` reports root `postcss@8.5.26` requires `source-map-js@^1.2.1`; `1.2.2` satisfies that range. Log: `/private/tmp/sawa-c2-source-map-security/logs/source-map-explain.txt`, SHA256 `a05580cc1a77d51ee006887361c075882fe473e963ed72cbd2e066ec0762cd3d`.

## Minimal lock update

The registry mechanic could not make the existing lock move only this package, so the unique `node_modules/source-map-js` entry in `frontend/package-lock.json` was manually updated from official registry metadata. No override, `npm audit fix`, force option, runtime-code change, or unrelated package change was used.

- Registry command: `npm view source-map-js@1.2.2 dist --json`
- Registry result: tarball `https://registry.npmjs.org/source-map-js/-/source-map-js-1.2.2.tgz`; integrity `sha512-KGj/8Y43x35aZVDtt+J4mK1hoLGHULMYfSkODJNQjNDC3oW1PqPoxMwo0pLUsWM/UEGzON/NxeHywEfNXNP3Vw==`.
- Registry log: `/private/tmp/sawa-c2-source-map-security/logs/source-map-1.2.2-dist.json`, SHA256 `54368630b8e882058f08d368164ad8c7b86996af2f41081545aa414c34aa38d7`.
- Lock diff: exactly one package entry, changing only `version`, `resolved`, and `integrity` from `1.2.1` to `1.2.2`.
- Lock diff SHA256: `b4bc66252c599ec43854b678a0db0d3a2230277cd9c5714f4b7601454907327c`.
- Resulting lock SHA256: `7e0d55c49204f6d458108e924214f3120747bbb272498998a195e4deb4e62902`.

## Baseline and verification

All commands below ran from `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c2-source-map-security/frontend` with owned output under `/private/tmp/sawa-c2-source-map-security`.

| Command | Start/end (UTC) | Exit | Result | Log SHA256 |
| --- | --- | ---: | --- | --- |
| `npm audit --omit=dev --package-lock-only --json` (base) | 2026-10-06T01:42:00Z / 2026-10-06T01:42:01Z | 1 | 1 high vulnerability, GHSA-68fv-2mgg-jv7q / `source-map-js@1.2.1` | `34bdbc9b156e0afa206e250811f1a29b50f2bc33e1694eb2feddf28822e9a82b` |
| `npm ci --ignore-scripts` | 2026-10-06T01:46:12Z / 2026-10-06T01:46:15Z | 0 | installed lock resolution; installed `source-map-js` is `1.2.2` | `129310de366deb5d9ad7a81fe088355449784bf1ccbe73b14950266b18adf575` |
| `npm audit --json` | 2026-10-06T01:46:30Z / 2026-10-06T01:46:30Z | 0 | 0 vulnerabilities; independently hashed full-audit log | `7163d44d8967ddfb3d845d8fc109d912b5ba8effd4978ab52bdcf9c94db956ed` |
| `npm audit --omit=dev --json` | 2026-10-06T01:46:30Z / 2026-10-06T01:46:30Z | 0 | 0 vulnerabilities; independently hashed omit-dev log | `7163d44d8967ddfb3d845d8fc109d912b5ba8effd4978ab52bdcf9c94db956ed` |
| indexed-map bad-offset focused check, offset line `10000000` | 2026-10-06T01:49:00Z / 2026-10-06T01:49:00Z | 0 | `SourceNode.fromStringWithSourceMap` returned `x` in 1 ms using `1.2.2`; installed source has the exhausted-code guard at `lib/source-node.js:115-124` | `8aa474e2431d4bc66d321f18d2b49eff897ceca5be90ed09906d129eb61fb923` |
| `npm run test:config` | 2026-10-06T01:46:41Z / 2026-10-06T01:46:44Z | 0 | 123 passed, 0 failed | `bc1ec4f08e3b64eaed7130b817bfe7d8b8760226462c8830d3622ed115e65390` |
| `npx tsc --noEmit` | 2026-10-06T01:46:53Z / 2026-10-06T01:46:57Z | 0 | passed | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `npm run lint` | 2026-10-06T01:46:53Z / 2026-10-06T01:46:57Z | 0 | passed; no lint warning emitted in this run | `a21d579058280fc409860fb3659ba80fbadb05fc041162e54b56f77215f28f9c` |
| `npm run build` | 2026-10-06T01:47:04Z / 2026-10-06T01:47:11Z | 0 | Next 16.3.8 production build passed | `c20d54fda1bf99019324b26a271804b08af75e7c411a10c7b77844303446d43d` |
| `node --test tests/menu-master-options.test.cjs tests/menu-masters-contract.test.cjs tests/config/menu-master-live.test.js tests/config/menu-masters-playwright.test.js` | 2026-10-06T01:47:20Z / 2026-10-06T01:47:23Z | 0 | Menu Master contract suite: 40 passed, 0 failed | `cded38ffa8e700e02c6f0c93ce2adb901e4b0f9e11c59fb3f4cb7bfef80f1b88` |
| `npx playwright test tests/e2e/menu_masters.spec.ts` | 2026-10-06T01:47:59Z / 2026-10-06T01:48:36Z | 1 | 30 failed before assertions due to `net::ERR_TOO_MANY_REDIRECTS` at `http://127.0.0.1:3100/menu-masters` and `/hospital/menu-masters`; this used the general config and is not a Menu Master product-regression result | `f13b2c2d11c589370ce9857a7fc4dbe36f048df375c02c37a1f329284e00f38d` |
| `E2E_PORT=31318 npx playwright test -c playwright.menu-masters.config.js --grep ': /menu-masters$'` | 2026-10-06T01:54:26Z / 2026-10-06T01:54:32Z | test 0; wrapper 1 | first dedicated WebKit case passed; the wrapper used zsh reserved variable `status` after the test and therefore returned 1 | `/private/tmp/sawa-c2-source-map-security/logs/menu-master-dedicated-first.log`, SHA256 `20da40f84bde5f1f7b50e7888c273239815a982e998bbdf8db9824b11bd7a8b8` |
| `E2E_PORT=31318 npx playwright test -c playwright.menu-masters.config.js` | 2026-10-06T01:54:51Z / 2026-10-06T01:55:33Z | 0 | dedicated Menu Master WebKit harness: 30 passed, 0 failed | `/private/tmp/sawa-c2-source-map-security/logs/menu-master-dedicated-all.log`, SHA256 `796ba53947bcb5afad4424493139c8bfee18aa7c0a6123e4bc6713c92e9c2519` |

The focused check first confirmed the consumer's explicit maximum section offset guard; its final test uses the maximum accepted indexed-map offset. It does not claim an old vulnerable package execution result.

## Generated files and final frozen diff

`npx playwright test` starts `next dev`. It generated untracked `frontend/AGENTS.md` and `frontend/CLAUDE.md`, and changed tracked `frontend/next-env.d.ts` at 2026-10-06 10:47:59 JST. These were not present in the initial clean worktree, are not lock changes, and were removed/restored after the test. The frozen worktree diff is only:

- `frontend/package-lock.json`: the unique `node_modules/source-map-js` `version`/`resolved`/`integrity` entry.
- `docs/modernization/c2-source-map-security-20261006.md`: this evidence.

The general-config redirect loop is an environment mismatch: it used `playwright.config.js` on port 3100, while this suite requires the unchanged base-equivalent `playwright.menu-masters.config.js` on isolated port 31318 with WebKit and `next start`. The first dedicated case passed, then the full dedicated run passed 30/30. These results establish the correct harness result; they do not independently prove a causal relationship between the redirect loop and the lock update. No redirect code was changed. GitHub full CI is not claimed here.

## Preserved post-integration PostgreSQL evidence

For source `bb5218402a8cf3d0602202abc47c71f6273d151f`, the prior post-integration owned PostgreSQL run used only `/private/tmp/sawa-c2-ci-postintegration-bb52184/pg-escalated`, its own socket, and unused port `55485`. `initdb`, `pg_ctl start`, and pre-test `pg_isready` succeeded; `pytest backend/tests/contract/test_automation_bootstrap_postgres.py -q` completed `17 passed` (2026-10-06T01:36:50Z to 01:36:52Z, exit 0). Cleanup was verified: `pg_ctl stop -m fast` exit 0, then `pg_ctl status` exit 3 and `pg_isready` exit 2. Log `/private/tmp/sawa-c2-ci-postintegration-bb52184/logs/postgres-owned-escalated.log`, SHA256 `53c4c94ca6df3e616665b9425ab916726499cd3e8d3cf4c4ad903b9258b7d164`.
