# Verified UI Artifact Binding (Source Only)

Consumer: hospital-ui-bind; base a91b4c745e6d99ffd33ac9d1a3b97ac81435507c.
Branch: codex/modernization-ui-bind-20261008.
Supplier: platform-main a0be8f8797f0eda48a44e60e9e0ec35e939c4eca.
Supplier artifact: /Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/platform-main/packages/ui/tmp/consolidated-parent-20261008/sawa-ui-0.1.0.tgz.
SHA256: 592f6792d63bae2637e6936acbc4f761c6bb9c0091fd66e090117020e2da5570.
Integrity: sha512-7RysgkY9n4Oag07jEiMSM7lvlPvQM+43ZNQBQF2FvufFjnP71pSBoKXN25oJxNF0h2MF0MrAcr/4HrBTxkVCuQ==.

Exactly four files: frontend/package.json, frontend/package-lock.json,
frontend/vendor/sawa-ui-0.1.0-a0be8f8797f0eda48a44e60e9e0ec35e939c4eca.tgz, and this document.
Root dependency, lock root dependency and installed-package resolved path now reference
the exact copied artifact. Existing integrity already equals the supplied and computed
SHA512; retained unchanged rather than inventing an integrity difference.
All other lock data (including peers, dependencies, engines and version) retained.
Old vendor artifact retained; no removal or rebuild.

Read tar package declarations: @sawa/ui 0.1.0, ESM, dist/index.js and dist/index.d.ts.
Supplier ancestor 5cf1420252d9b116742842a237e44c97bc21a2c9 is ancestor of
a0be8f8797f0eda48a44e60e9e0ec35e939c4eca (git merge-base --is-ancestor exit0).
An initial abbreviated 5cf lookup was invalid; full SHA was then checked.
Supplier build/full40/pack0 is parent-provided evidence, not rerun consumer proof.

Commands: git status/rev-parse/branch, AGENTS reads, jq package declarations,
tar -xOf, shasum, openssl SHA512, git merge-base, exact cp, manual apply_patch,
structured JSON diff-isolation comparison, cmp, scoped git add/cached hash.
No install/network/test/build/source or business changes. Consumer technical proof
(dependency installation, compilation, runtime/UI and integrated regression) pending.
Packet frozen for parent review; no commit/push/deploy or self acceptance.
