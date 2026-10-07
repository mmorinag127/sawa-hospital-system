# Hospital Sharp Security Patch

## Current Explicit Verification Inputs

All three scripts now require `--old-sharp`, `--baseline-sha`, `--output-dir`;
there is no hospital-main/default-reference fallback. Output directories must be
fresh physical descendants of this worktree's tmp, mkdir is nonrecursive and
files use wx. Existing reports remain unchanged. Child fixture output has the
same physical ownership restriction. Old module is read only, never an output.

Actual reference inventory: installed package.json in
`/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-v1-frontend/frontend/node_modules/sharp`
records 0.35.4; isolated runtime also checks that version. This proves the component
installation, not branch/source equivalence. Explicit lock baseline remains
`00d0b188f84483fb85250da7edb4090c0a82a17f`; current generating HEAD and dirty
manifest/lock/script hashes are recorded separately in reports.

Same NODE and closed env as below. Each actual command was `$NODE scripts/NAME`
with these literal arguments:
`--old-sharp /Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-v1-frontend/frontend/node_modules/sharp --baseline-sha 00d0b188f84483fb85250da7edb4090c0a82a17f --output-dir OUTPUT`.
Fresh absolute OUTPUT prefix is
`/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-v1-sharp-patch/tmp/sharp-proof-cli-final/`.

- verify_sharp_patch.cjs: OUTPUT suffix audit; exit 0, strict closure check,
  isolated six image comparisons and full/omit-dev audit both total 0.
  audit/verification.json SHA256 b132c12ec07284fb8fa3c9a48dad878233cba96a52173ca2d19fc3cca177d1c9.
- verify_sharp_image_parity.cjs: OUTPUT suffix images; exit 0, six exact comparisons.
  images/independent-parity.json SHA256 90c5069a6eaf6e69efb18a7d29084b5874237b55084fa1407d03c202a12134b4.
- verify_sharp_jpeg_parity.cjs: OUTPUT suffix jpeg-final; exit 0, three exact comparisons.
  jpeg-final/jpeg-parity.json SHA256 6c042dddd50e96d759dc38904b18b6a167db66542a71513e4bcc4f32c00ff3f2.
  Earlier successful jpeg output also retained; final rerun follows child-path guard.

Reports retain full commands, source assets, reference package/native hashes,
patched source hashes and native versions. Main patch verifier now delegates image
processing to isolated children instead of loading both native libraries together.
No production package changes beyond the existing patch, no commit/merge/push/deploy.
All following proof entries are historical checkpoints, not current CLI commands.

Base: `00d0b188f84483fb85250da7edb4090c0a82a17f` plus dirty source hashes below.
Dedicated named branch/worktree: `codex/modernization-v1-hospital-sharp-patch-20261007`,
`/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-v1-sharp-patch`.

Primary advisory https://github.com/advisories/GHSA-wq5f-xc86-pv6w read
2026-10-07: sharp <0.35.5 affected, 0.35.5 patched librsvg. SVG not disabled.
Only override 0.35.4 -> 0.35.5 and npm-generated sharp/@img/sharp closure changed.
Structured package-record comparison rejects any unrelated lock change; native
sharp entries are 0.35.5, libvips package entries 1.3.4 with registry integrity.
Next/MUI/shared UI tarball/backend sources unchanged.

## Commands and Evidence

NODE=/Users/mmorinag/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node
NPM=/Users/mmorinag/.anyenv/envs/nodenv/versions/24.6.0/lib/node_modules/npm/bin/npm-cli.js
ROOT=/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-v1-sharp-patch

Commands were run with env -i, owned HOME=$ROOT/tmp/sharp-proof/home,
TMPDIR=$ROOT/tmp/sharp-proof/temp, npm_config_cache=$ROOT/tmp/sharp-proof/cache,
PATH containing bundled Node and /usr/bin:/bin only. Registry operations used
approved tool escalation. No shared dependency directory was written.

- cwd frontend: `$NODE $NPM install --package-lock-only --ignore-scripts --no-audit --no-fund`, exit 0.
- cwd frontend: `$NODE $NPM ci --no-audit --no-fund`, exit 0, 281 packages installed.
- cwd ROOT: `$NODE scripts/verify_sharp_patch.cjs`, exit 0; exact audit commands
  and complete changed/native records stored in verification.json. Initial image
  checks loaded both dylibs together and emitted duplicate-library warning:
  those initial parity results are NOT accepted as independent proof.
- cwd ROOT: `$NODE scripts/verify_sharp_image_parity.cjs`, exit 0. This runs each
  installed version in separate processes, compares actual full decoded buffers,
  dimensions and encoded outputs. Six cases passed: fixed tracked PNG and SVG,
  each raw decode, resize PNG and resize JPEG. Reference is read-only
  hospital-main/frontend/node_modules/sharp 0.35.4; patched is owned 0.35.5.
  macOS runtime emitted xcode cache/FSEvents warnings; no comparison failure.
- full `$NODE $NPM audit --json`, exit 0; omit-dev audit exit 0; both reports
  have info/low/moderate/high/critical/total all 0. This is this registry snapshot,
  not a claim of universal or live security acceptance.

Artifact absolute prefix: `$ROOT/tmp/sharp-proof/` (ROOT above is literal path).
`verification.json` SHA256 9c46bf3bfef8b78f0fd53aa8b983d1f88e0c770c4f16075a006f5c8790e01e28.
`independent-parity.json` SHA256 f6c3875721db0eaa48640f91f0f51428e7e4fb9edf9ff57c125e8e9c652f2237.
`audit-full.json` and `audit-production.json` each SHA256
7163d44d8967ddfb3d845d8fc109d912b5ba8effd4978ab52bdcf9c94db956ed.
Reports retain input hashes, commands, old/new native versions and output hashes.

Source SHA256 package.json:
40e0270dfb5618916395fd1f13fd34abe3b1d46471365a07a1ec049f02d1f741.
Source SHA256 package-lock.json:
8393cdabe929546a8317872087ef9900141512d82567979ca0ca58413fc82290.
Proof scripts' source hashes recorded in their reports. Proof is dirty-base
lineage, not a committed generating SHA or integrated acceptance.

## Historical Missing JPEG Checkpoint

`git ls-files '*.jpg' '*.jpeg'` and `find . -iname '*.jpg' -o -iname '*.jpeg'`
found no JPEG input in this worktree. JPEG encoding from PNG/SVG is NOT a
substitute for required fixed-repository JPEG input parity. That required input
was missing at that checkpoint; completion was not claimed. The following explicit
authorization closes component codec QA with a synthetic derived fixture, not an
original customer JPEG.
Linux packages are verified lock metadata, not locally executed Linux binaries.
Runtime parity is macOS arm64 only. Whole frontend compilation has known baseline
MUI blockers; no build success is claimed or checks suppressed. No broad suite,
backend recognizer, stg/live, commit/merge/push/deploy execution performed.

## Authorized Derived JPEG Component QA

Source PNG is the same verified tracked asset:
`backend/src/data/templates/fax_fac00002_v1.png`. Its absolute path and SHA256 are
retained in `$ROOT/tmp/sharp-proof/jpeg-parity.json`.
Read-only baseline sharp 0.35.4 encoded a fixed JPEG with quality=80,
chromaSubsampling=4:2:0, progressive=false, mozjpeg=false. The fixture is ownedtmp
only: `$ROOT/tmp/sharp-proof/derived-baseline-quality80-420-final.jpg`.
Fixture SHA256: 6f070817dbf1bd4a1c0af43bdc77fba98a55a36814b4378d73ad4055b94ab48d.
Encoder package SHA256: 27b5a6c6e1e1f27c527b09a963c932979403837d0f5f013ab5bdfb4a0ef2269a.
Encoder output.cjs SHA256: 2ef21e22c65653900ee5cf6ef9e40d7fd8a432c3b0a29bed970ed50326638f88.
Encoder native SHA256: 389121ae6516bd89e92fa81b68d94b09e0b084cc53d57919e2140a73d9e3ca4c.

Command: same isolated environment as above, cwd ROOT,
`$NODE scripts/verify_sharp_jpeg_parity.cjs`, exit 0.
The report records the exact baseline fixture child command and separate-process
old/new commands. Three JPEG-input cases passed: raw decode, resize-to-PNG and
resize-to-JPEG using the same operations as prior checks. Full raw decoded buffers,
dimensions and encoded outputs match exactly. Source/lock/script hashes retained.
Report `$ROOT/tmp/sharp-proof/jpeg-parity.json` SHA256:
a759910cf3907af8cf51daba4d25e7dee3049c0aaf27a26ff4bd20cc0231e1b0.

First fixture attempt exited 1 while reading an incorrect encoder source path
after creating an initial owned JPEG. Installed dist/output.cjs and versioned
native path were then inspected and recorded correctly; final create-only fixture
has a separate name. Initial fixture is not used as proof.
Previous six PNG/SVG passes and both audit-zero reports remain unchanged; not rerun.
This is synthetic PNG-derived JPEG codec component QA only, not original customer
JPEG, FAX/PDF/order flow, facility/week, business quantity, Linux runtime or live
proof. No upload/DB/recognizer change. Bounded dependency and component parity
preparation is frozen for parent review; no commit/merge/push/deploy performed.
