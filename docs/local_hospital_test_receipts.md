# Signed Hospital Local Test Receipts

Run sequentially on the exact clean commit that will be deployed:

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
