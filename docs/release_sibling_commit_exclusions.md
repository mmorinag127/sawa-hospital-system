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

## 2026-06-15 facility editor prod release

Release source: `release/prod-20260615-facility-editor` at `4ce4decab1a81e553d3167f2159b50535bf9435e`.

Staging source validated before prod: `develop` at `4ce4decab1a81e553d3167f2159b50535bf9435e`.

Excluded sibling groups:

- Older order/output/workflow branches from May 2026 that are not the current release source. They were parked in separate worktrees and are not required for the facility editor UI/manual release.
- Older facility-master UI attempts from May 2026. The current release supersedes them with the `a2960b8`, `01585e6`, and `4ce4dec` commits already validated on staging.
- Quarantined `JJ_EMPTY_STRING` mixed WIP commits from May 2026. These are not deploy sources and are excluded from this production release.

Allowed commits:

- `4c5ecc7c12d41f94749d3a266bfdfcd0fbb24e7e`
- `62fefb2b0d6d7f18efd17881b64cd4c3f9f3e367`
- `29229c997bc694288a680798752f5d4daebe4c5a`
- `772da7a474fde63588a30de3ca6ca9e234124842`
- `f165ab212c7db68bf660534e68cee19287d7e0a2`
- `e7979ecdce19e9c483d9268873e47af4359343d4`
- `c6a3329e923e0489c2b42547c6ea0f13ef60e93a`
- `5f1ee503ec87350493c77348f0a38c5fdf26e9fb`
- `510117a482799f44f1e5883e0143ec1101daa732`
- `90748e55f1c32c933eddb77ed77b85a9d21b82c0`
- `c78026d8bf0a0ba76e1869ff9a7f2d1a1fbe609a`
- `e0cc5f47016ab6ae1541b4d060e1caeaef4185c7`
- `abe917bff4b5c835b12461a5aaae04b314ab95e2`
- `049ab2d249d5c12b2aed31eb45e2b40daf26cb71`
- `7f2afad54578fde9663a972645494e13aa0e09af`
- `82f274fa1bec9116f921d996dbaf43887bc145d2`
- `1a433ec63abb639880a64aad7137e940391b30fb`
- `ca8af1e9d04207748e1c9410dcd130e16c0f4000`
- `728e759bbee7b598249dd8b82822079a4ac0a23b`
- `3b7709c5eb42e27a62e1e94f4f357021944f0eec`
- `2583a06c79eb27fd6d5239d835ba4d3ea862ba46`
- `bfa116b11ccb436f9bfe9d426932255bbb15ff45`
- `e536a1b484c2db021eb420e05a0170194b66c8a6`
- `20bbab551d5651d10530de98f7be470b081586f6`
- `77987c00493692c6a0d133e0325f3c570868db90`
- `9f596d1fb27c5aa28a01d680c2ee818cd1e49584`
- `63bbe62b61cabfa989a8d1c2ed47738ea6ff9e4d`
- `a363dc6550e4a52be3fc09bbfc4dcb0b20593199`
- `065005e85a451137895c54885b15f1cf1967ff19`
