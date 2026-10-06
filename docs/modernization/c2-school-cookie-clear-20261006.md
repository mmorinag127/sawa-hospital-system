# C2 school cookie clear

Base: `aed40b6df5314d81ad260bf03d45d46699eea3a0`.

Hospital system navigation and automation use the school issuer's explicit
Bearer response to publish the non-secret school session generation before
full-document navigation. Hospital SSR logout clears the school auth cookie and
generation cookie together with identical `/school-lunch`, HttpOnly, Secure,
SameSite=Strict attributes. No automatic 401 cookie clear is introduced.

## Current verification evidence

Source remains `aed40b6df5314d81ad260bf03d45d46699eea3a0`. Focused
navigation/login/automation/browser-session/logout tests passed. Hospital
typecheck and build passed with isolated HOME/cache. The full config run had
122/123 pass; the remaining `google-auth-deploy` failure is the pre-existing
base workflow count `4 != 3`, outside this auth slice and left unchanged.
Its log is `/private/tmp/sawa-c2-session-hubble/logs/hospital-test-config-all-r2.log`,
SHA256 `d699c034599cee9276c303e3efda32293399189fac7cfa4b5c9ea71594c3b970`.

Final source: `aed40b6df5314d81ad260bf03d45d46699eea3a0`; dirty binary diff SHA256
`65d5f367b84248f7ec9cbe481d3f2e4216521e0a44ecddb048d92f1261ed389d`.

| Command / cwd / environment | UTC mtime | Absolute log / SHA256 | Result |
| --- | --- | --- | --- |
| focused auth neighbors, `frontend`, `HOME=/private/tmp/sawa-c2-session-hubble/hospital-home`, `npm_config_cache=/private/tmp/sawa-c2-session-hubble/hospital-npm-cache` | 2026-10-06T02:46:00.388Z | `/private/tmp/sawa-c2-session-hubble/logs/hospital-auth-neighbor-r3.log` / `d076ede136aeb5b9c9a4e4f7a7756ae9949fb26b5f885a0f6c6125ffdd274f85` | exit 0 |
| navigation/login/automation focused tests, same cwd/env | 2026-10-06T02:45:26.159Z | `/private/tmp/sawa-c2-session-hubble/logs/hospital-auth-navigation-r2.log` / `d9053d92c51969814db4fdc81d410531def79c1756928dc5deeb323995960bc8` | exit 0 |
| `npm run test:config`, same cwd/env | 2026-10-06T02:48:30.909Z | `/private/tmp/sawa-c2-session-hubble/logs/hospital-test-config-all-r2.log` / `d699c034599cee9276c303e3efda32293399189fac7cfa4b5c9ea71594c3b970` | 122 pass, 1 base OIDC count failure |
| `npx tsc --noEmit`; `npm run build`, same cwd/env | 2026-10-06T02:52:57.222Z; 2026-10-06T02:53:07.565Z | `/private/tmp/sawa-c2-session-hubble/logs/hospital-typecheck-current.log` / `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`; `/private/tmp/sawa-c2-session-hubble/logs/hospital-build-current.log` / `9d8efe5cc2c648b75e5f1b115b43dae48c7b337b6c57efc879360f4fd894a3fd` | both exit 0 |

## Final source inventory

Product-source SHA256 at the final command record: `frontend/src/pages/auth/automation.tsx` `267f088765e67ae4442a7c4a755104cbed038eaeb62c650d80205b9cca37c63c`; `frontend/src/pages/logout.tsx` `ca19a710c508a7413c456884b441a059916dce33ee813d5e19a4e26433584436`; `frontend/src/services/browserSession.ts` `332820b0a28f53bd0aebfb6daf0d73886c73cd0a813498665fb13b520e9dea3d`; `frontend/src/services/systemNavigation.ts` `5ca4f370ee82e6373dc7789b3f5b1439921ffff940bf9aecc0f4dda8bca2eef2`. The tracked-diff SHA256 is `65d5f367b84248f7ec9cbe481d3f2e4216521e0a44ecddb048d92f1261ed389d`.

## Freeze inventory

The current changed-file inventory is
`/private/tmp/sawa-c2-session-current-20261006/hospital-changed-source.sha256`,
SHA256 `fa975c8d18ba8c6b711ca45c9ec597ad946a4e41e374e72a9728b49cacbd2d5f`.
The current tracked-diff record is
`/private/tmp/sawa-c2-session-current-20261006/hospital-tracked-diff.sha256`,
SHA256 `74f0bb6cee7e5d378bc0ffc72f07b9e19952e4db6b776c880ef4f0d0d88c3f8e`.
No hospital source was changed during the school current-source rerun. The
single `google-auth-deploy` OIDC count failure remains a test of this old base;
it must be rerun by the main agent after integrating the current main OIDC
contract change. This worktree remains frozen and is not a staging or
production claim.
