# Shared UI consumer rebind: 2026-10-07

## Source and scope

- Worktree: `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-v1-ui-bind`
- Branch: `codex/modernization-v1-hospital-ui-bind-20261007`
- Consumer base: `1654ffa2ebb8f762dd473516a9e02d91a3bb417f`
- Package generating platform commit: `5cf1420252d9b116742842a237e44c97bc21a2c9`
- Integrated package ancestor: `452db92e2d5ff2adc6fed973b4f482ea25b7cd63`
- Generating source manifest SHA256: `1b6b8250282b0edb2d748abf5f73f201b9a81e0260ba4e3a0930e87373619321`
- Actual source artifact: `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/platform-v1-ui-package/tmp/v1-ui-package-20261007/sawa-ui-0.1.0.tgz`
- New consumer artifact: `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-v1-ui-bind/frontend/vendor/sawa-ui-0.1.0-5cf1420252d9b116742842a237e44c97bc21a2c9.tgz`
- Artifact SHA256: `592f6792d63bae2637e6936acbc4f761c6bb9c0091fd66e090117020e2da5570`
- Artifact integrity: `sha512-7RysgkY9n4Oag07jEiMSM7lvlPvQM+43ZNQBQF2FvufFjnP71pSBoKXN25oJxNF0h2MF0MrAcr/4HrBTxkVCuQ==`

This is an uncommitted consumer pointer change on the exact base above.
The artifact was copied byte-for-byte from the parent-verified platform pack,
not generated from this consumer. Platform source and build evidence remain
in its `tmp/v1-ui-package-20261007/evidence.json` (SHA256
`5f170de707198ebac0ee6dcd021e3422fbce7f34ed2030160fdd7b2bbc6a921b`).

## Commands and comparison results

Commands executed from this worktree root:

```sh
cp -n /Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/platform-v1-ui-package/tmp/v1-ui-package-20261007/sawa-ui-0.1.0.tgz frontend/vendor/sawa-ui-0.1.0-5cf1420252d9b116742842a237e44c97bc21a2c9.tgz
env -i PATH=/usr/bin:/bin /Users/mmorinag/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node /private/tmp/verify-v1-ui-consumer-bind-20261007.mjs 1654ffa2ebb8f762dd473516a9e02d91a3bb417f
git add frontend/package.json frontend/package-lock.json frontend/vendor/sawa-ui-0.1.0-5cf1420252d9b116742842a237e44c97bc21a2c9.tgz docs/modernization/v1-ui-consumer-bind-20261007.md
git diff --cached --check
```

Copy and structural verification exited 0. Manual JSON edits used apply_patch.
No npm lock generation, installation, build, app runtime, or e2e ran.
Git's read-only comparisons emitted sandbox confstr temporary-directory warnings.

The verifier compared the entire parsed package.json and package-lock.json
against the base, permitting only:
- package.json dependencies[`@sawa/ui`] source pointer;
- lock packages[`""`].dependencies[`@sawa/ui`] source pointer;
- lock packages[`node_modules/@sawa/ui`].resolved;
- lock packages[`node_modules/@sawa/ui`].integrity.

Every other parsed value, package entry, version, scope, override, and requirement
remains unchanged. The actual packed package manifest's dependencies,
peerDependencies, engines and version exactly match the lock entry; each packed
dependency requirement matches its resolved lock version. SHA512 integrity was
computed from the actual new tarball, not copied from an old artifact.
The copy's bytes match the source and the same SHA256 is used in all three apps.

Changed package/artifact SHA256 values:

| Path | SHA256 |
| --- | --- |
| `frontend/package.json` | `75ff11cdb106644bcd92d48656c044e2b164a80cf27e52d594161c0a8491966d` |
| `frontend/package-lock.json` | `cdb3076572d082e820b64eaf84afe5ddccd77a2b4725c39bf77955638494c05f` |
| `frontend/vendor/sawa-ui-0.1.0-5cf1420252d9b116742842a237e44c97bc21a2c9.tgz` | `592f6792d63bae2637e6936acbc4f761c6bb9c0091fd66e090117020e2da5570` |

Preserved old vendor evidence, byte-compared against the base:

| Path | SHA256 |
| --- | --- |
| `frontend/vendor/sawa-ui-0.1.0-8004321e0364f54d1294df4bb3143dc091fc5a86.provenance.json` | `4eb3237e4a71348115173c880ea37c80ed3640e4fa100d979b7d47cd356a55c1` |
| `frontend/vendor/sawa-ui-0.1.0-8004321e0364f54d1294df4bb3143dc091fc5a86.tgz` | `eb85dd9a368cbac702840289ff4c1e964de09707a07831b7043755130d82dfcb` |

## Remaining technical checks

Parent source review, commit/integration, and validations at the integrated SHA
remain pending. This pointer/lock verification does not prove installed or
running application behavior. Existing node_modules was not used as evidence.
No local shift runtime was executed. No commit, merge, push, deploy, browser,
live request, or other-chat operation occurred. No background process started.
