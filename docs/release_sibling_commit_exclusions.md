# Release Sibling Commit Exclusions

This file is the explicit allowlist for `scripts/git_flow_release_preflight.sh`.

Rules:

- Only list commits that were inspected and intentionally excluded from the current release source.
- Do not use this file to hide a candidate fix. If a sibling commit contains required production behavior, merge it into `develop` and re-cut the release branch instead.
- Keep `STRICT=1`. The allowlist is the documented exception path; `STRICT=0` is not the prod release path.

## 2026-05-04 prod release

Release source: `release/prod-20260504` at `9dea6c79e19bb89ac523e92eddcabb7b78aecd63`.

Staging source validated before prod: `develop` at `9dea6c79e19bb89ac523e92eddcabb7b78aecd63`.

Excluded sibling groups:

- `refs/jj/keep/*` and `codex/hakodate-assignment-20260424`: legacy Hakodate investigation/diagnostic work parked outside release. The current release uses the workflow-v2/Hakodate path already validated on staging; this parked branch is not a prod source.
- `codex/prod-release-merged*` and `codex/integration-stg-release`: older release-tooling integration branches. Current `develop` contains the active Git Flow/preflight/deploy tooling used for the staging-validated release; these historical branches are not the current source of truth.

Allowed commits:

- `a98d5e72bf1fca0493de40f9d5caaa3b258f5a7b`
- `61f9c48588706bc169fa7212e2ca60672991edcf`
- `0387e2852e91171723def85bdf21217e56d9403b`
- `486d12407d28da0be33133fd5f5fa4db89b55a07`
- `ae123cf01ffcd9b77b05d5e9f3dbd41c1326a2ee`
- `94e783bbdb5b71d5684f30ee89a0aa97a71f5899`
- `f4b14eead40a8c520fe5f7543b87ab9230eff685`
- `0655fa30d5090c21e69d873ae299925c66376a5a`
- `e0916b69ecd1251e8d5d374b2b0652c458e7ef9c`
- `e72c6b9d32ac2e8598bdf03bd7964ed715d8115e`
- `05c739b5b2138c0c5b41acc641e5718dce4e3c93`
- `a4177cfd06c51d6c0892e9c8b2142d4c7f6b76d4`
- `ed2b0c5a0111f94274af8593c7a5f8b9c0f0cdc6`
- `24a8723f9ee6565c346b17e538c4b9c7498207ac`
- `9c3be942fbe7ca34f2bb242d593dea47181e7b61`
- `df59df30262f8525e3c60cebd8b1268bb093e9d5`
- `b248af07f092ad93d0397f09af6360ac66adc43e`
- `0da17e0bf9c6fb4bfe336ab1bee2d70fc996b181`
- `da316b99f0ca8836cb24584b814d28c9c0f487f5`
- `2511ee7e6ddfc2317188156060d6ed3c1c1cfb56`
- `af85bfb9328a66c57f146ed9db6ec77a282cc12a`
