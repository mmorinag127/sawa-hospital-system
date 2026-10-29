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

The staging default selects reviewed behavior groups from
`infra/ci/staging_backend_scope.json` before collection. It always includes the
authentication and quantity-preservation groups, changed test modules and retained
failed or interrupted modules. Changed backend inputs without a reviewed mapping
stop the run rather than silently omitting coverage. Dedicated PostgreSQL modules
are excluded only because the same receipt maps them to their owned PostgreSQL
command. Tests outside the selected staging groups are not executed or claimed
as passed. `--full` and `release/prod-*` execute every general case; the explicit
impact mode retains the testmon/input tracker. Inherited pytest selectors are
removed.

Each backend run retains `backend-tests/selection.json`, including executed and
reused node IDs, reasons, context, baseline identity and tracking hashes. Its hash,
source SHA and counts are included in the signed receipt accepted by the staging
source gate. Version 3 additionally records command reuse and its original signed
execution proof. Version 1 full-run receipts remain readable.

The staging default does not require a warm tracking database or collect the
entire general suite. The first explicit impact run does execute every collected
general case. A reported all-test run is not evidence of a speed improvement:
selection counts, omissions and actual timing must be reported separately.

Focused frontend discovery includes all standard config and browser test names,
in addition to the explicit boundary modules in
`infra/ci/staging_frontend_scope.json`. Fixed lists cannot hide new tests. The
five PostgreSQL groups share one owned server startup and run serially in separate
pytest processes. The output group performs its fresh-head migration probe;
historical per-revision resume matrices remain full-mode coverage. A focused PG
batch can reuse an earlier real success only with its signature, ancestor source,
unchanged source/runtime/environment and intact input-observation proof. Unknown
inputs, subprocesses, skips or failures prohibit reuse. Full/production runs
never reuse that command. No hosted CI execution is introduced.

## Cold-run preparation reduction

The requested ceiling is approximately 15 minutes, including a cold/full run.
This is an acceptance target, not a measured result or a timeout that makes an
unfinished run successful. Do not run timing measurements without authorization.

The formal SQLite fixture validates its migrated empty template against the
complete catalog once per session. Every case checks the migration source digest,
the verified template digest and the copied database digest before connecting.
PostgreSQL still performs its complete per-case migration/catalog checks. Tests
of migrations, schema mutation and runtime schema validation are not replaced.

Delivery and workflow reservation fixtures build their empty metadata schema
once per session and give every case an independently verified database copy.
Their SQL behavior, assertions and case combinations remain unchanged. Neither
business data nor engines/connections are shared between cases. The general
suite still runs every required case on a cold/full run; these changes do not
prove that the complete run fits the requested ceiling.

The preparation change was functionally checked with the three complete modules
`backend/tests/integration/test_sqlite_fixture_template.py`,
`backend/tests/unit/test_ingest_delivery_contract.py`, and
`backend/tests/integration/test_workflow_ocr_enqueue_transaction.py`: 114 cases
passed in one serial invocation, using a new temporary bootstrap SQLite database,
the existing local Python runtime and `pytest -qq --tb=short`. No duration
comparison or full-suite timing run was performed. This is not a full release
receipt or proof of the 15-minute ceiling.
Review then added an actual migration-source mutation refusal case. Its first
run exposed a duplicate short-name helper import in the test; the import was
aligned with the fixture's canonical package import. All 10 helper-module cases
then passed. Neither the failed probe nor this functional rerun measured speed.

## Collection input tracking correction

Path resolution, existence/type predicates and direct repository stat results are
tracked separately. Changes to symlink targets, file presence, permissions or
other stat attributes invalidate the corresponding input. An absent external
read is reusable only while that path remains absent; real external reads retain
their conservative execution requirement. Package discovery uses the installed
package catalog, and tqdm's fixed TQDM_ settings remain in the environment context.

Only the runner's initially empty, regular, non-symlink backend-runtime.sqlite
may be identified as collection bootstrap, from backend/conftest.py itself.
The collected database hash is retained as provenance, not as an input for
non-database cases. Bootstrap schema sources are part of the context. Pool checkout events carry the owning
connection record's database identity; unknown, foreign and case-time checkouts
remain non-reusable. Module-level test schema writes run in their own preparation
fixtures instead of contaminating collection for unrelated tests.

The correction has selection contract regressions and 27 receipt contract tests.
It does not establish the 15-minute ceiling. A complete successful exact-source
receipt, Linux image checks and staging verification are still required before
claiming release completion. Interrupted or failed batches are not receipts.

Runner-generated SAWA_LOCAL_* identifiers, image selectors and PostgreSQL tool
locations do not invalidate tests that never read them. Python records actual
reads; Node keeps readers non-reusable. Both implementations use the same
identity regression fixtures. Runtime options such as NODE_OPTIONS remain part
of the Node context rather than being mistaken for an unrelated run identifier.
