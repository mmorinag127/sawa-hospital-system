# Signed Hospital Local Test Receipts

Run sequentially on the exact clean commit that will be deployed:

Prepare dependencies once before starting the runner. Keep the hospital backend
environment and the gateway lock separate; they do not share a FastAPI pin.
Install the pinned test-only tracker in the backend test interpreter with
`python -m pip install -r infra/ci/requirements-local-tests.txt`.
Set `SAWA_LOCAL_TEST_IMAGE_AGENT_RUNTIME` to the prepared Linux/amd64 serving
agent image labelled with this exact clean commit. The native test runner checks
the image source, derives an explicitly recorded test-only gateway image, and
creates an owned local PostgreSQL16 container named for this run. The database
has no host port and no external network; the test container alone joins its
network namespace. Native interpreters remain those packaged in the serving image.

The manifest enables native subprocess tests and includes all agent tests,
including packaged baseline migration. Missing PostgreSQL/native preparation is
not a successful skipped verification. Commands run serially. The test-only image
is prepared once per command, not rebuilt per case; its explicit test dependencies
do not change the serving image or isolated native application environments.

Ordinary command failures are collected across the independent command groups
in the same serial batch, so one failure does not hide the other results.
After each group, a changed HEAD, manifest or dirty source stops the batch.
An observed source change remains a failure even if HEAD later returns to its
starting value. Any failed group or source change prevents signing a receipt.

```sh
python3 scripts/local_hospital_test_runner.py --output /tmp/sawa-hospital-receipt
```

Only complete, clean, all-exit-zero execution writes `receipt.json`, containing
`{payload, signature}`. Failed or incomplete execution writes `failed-run.json`
and does not produce a deploy receipt. The payload binds start/end HEAD, clean
flags, manifest SHA256, each command SHA256, log SHA256 and integer exit zero.
Changed HEAD/definition, false or malformed flags/hashes and boolean exit values
are rejected.

`scripts/local_receipt_signing.py` is identical to the parent platform module.
The runner signs the canonical payload with OpenSSL ECDSA. Verification requires
`SAWA_LOCAL_TEST_PUBLIC_KEY` from the same-named repository variable on all
dev/stg/prod receipt gates. Unsigned envelopes, signature tampering and an absent
public key fail closed before OIDC or deployment.

The local record key defaults to `~/.codex/local-test-signing/key.pem` and is
owner-only (600). It signs test records only and supplies no GitHub/GCP deploy
credentials. The parent owns actual key creation and repository-variable
registration; this work used only disposable test keys.

The owner submits the signed envelope verbatim as `local_test_receipt` and its
`payload.source_sha_start` as `expected_source_sha`. Actor and triggering actor
must both be the repository owner. Existing named remote SHA, release ancestry,
OIDC and production confirmation/approval checks remain mandatory.

Legacy test definitions and the local command mapping are documented in
[local_hospital_test_mapping.md](local_hospital_test_mapping.md).
After deployment, separately run `python3 scripts/run_local_stg_ocr_ui_check.py`
with the documented exact deployed source and original artifact inputs. This
post-deploy check is not included in the pre-deploy receipt or marked successful
by creating that receipt.
The native sibling harness is absent from approved develop and is inventory
only, not a release prerequisite. No whole-suite receipt is claimed.

Backend tests use the pre-provisioned `python3` selected by `PATH`,
running general `backend/tests` cases with this checkout's `backend` and
`backend/tests/unit` on `PYTHONPATH`. Provision the runtime and install its
dependencies in a separate preparation step; the test command never recreates
a venv or installs packages. Missing dependencies are failures, not skips.
The runner creates a new SQLite database in the run's evidence directory and
removes inherited database connection settings. Dedicated PG/adoption modules
are collected only by their explicitly mapped full-module commands, not again
without their owned PG context. All modules remain required by the full receipt.

The assistant command runs the complete packaged assistant suite once, including
the baseline migration checks previously run separately. It supplies only its
own disposable `agent_trial_<run>` PostgreSQL database, never a host-supplied
shared/business DSN. Its persistence fixture replaces the `sawa_agent_trial` schema.
The command enables `SAWA_AGENT_NATIVE_TESTS=1`; missing native dependencies or
an unavailable local database are not a complete release verification.
Node contract files and Playwright workers run one at a time on the local PC.
Playwright copies tracked frontend source and installed dependencies into an
independent evidence workspace. Private ignored `.env.local` files and inherited
cloud credentials are excluded; Next-generated source stays in that workspace.

## Backend impact selection

The local batch defaults to dependency-tracked backend selection. `--full` and
`release/prod-*` execute every case. Unknown inputs, missing or corrupt tracking,
and failed tracking runs do not authorize reuse. Only the runner's built-in
testmon/input tracker may select cases; inherited pytest selectors are removed.

Each backend run retains `backend-tests/selection.json`, including executed and
reused node IDs, reasons, context, baseline identity and tracking hashes. Its hash,
source SHA and counts are included in the version 2 signed receipt accepted by
the staging source gate. Version 1 full-run receipts remain readable.

The first tracking run is full. A reported all-test run is not evidence of a
speed improvement: real backend reuse and timing must be measured separately.
