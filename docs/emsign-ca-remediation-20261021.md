# emSign CA remediation (2026-10-21)

## Scope and status

This change updates trust stores without disabling TLS verification or changing
business results. Source changes and local base-image checks are not deployment
evidence. Staging and production remain unverified until rebuilt images are
deployed through the existing Actions gates and their digests are recorded.

- Hospital source base: `6b3ee830`.
- Shift source base: `e45a0bb`; separate security worktree.
- School lunch source base: `f9cba86`; separate security worktree.
- Platform source base: `e992dd0`; separate security worktree.
- Cloud Run inventory: 15 services in `sawahospitalsystem/asia-northeast2`.
- No production changes executed.

## Required versions

- Debian 13 CA bundle: `20250419+deb13u1` or newer.
- Alpine CA bundle: `20261020-r0` or newer.
- Python certifi: `2026.10.19` or newer; pip: `26.2.2` or newer.
- uv: `0.13.4` or newer.
- Node uses `--use-openssl-ca` with the updated OS bundle. Adding system CAs
  alongside bundled CAs does not remove the compromised roots.
- Both emSign G3 and the AAA cross-signing path must be rejected.

## Completed local checks

- Homebrew CA bundle updated from `2026-07-16` to `2026-10-20`.
- Active development Python updated to pip `26.2.2`, certifi `2026.10.19`.
- Active uv updated from `0.4.20` to `0.13.4`.
- New Mac zsh sessions select the Homebrew CA file for Node/OpenSSL and curl.
  Node and `/usr/bin/curl` pass the positive control and reject both affected
  chains. Existing processes, GUI applications, and explicit per-client CA
  overrides are not covered by this shell configuration.
- Google Cloud SDK's bundled certifi still contained both blocked roots.
  Mac shells and CI now set `CLOUDSDK_CORE_CUSTOM_CA_CERTS_FILE` to the patched
  OS bundle. The SDK's unauthenticated HTTP session passes the positive control
  and rejects both affected chains; no credentials were sent to probe hosts.
- Mac OpenSSL, certifi and pip vendored certifi: positive HTTPS control passes;
  both compromised test chains fail certificate verification.
- Node 24 Debian and Alpine base layers: positive HTTPS control passes and both
  compromised test chains fail certificate verification.
- Python 3.11 and 3.12 Debian base layers: same checks pass for OpenSSL, certifi,
  and pip vendored certifi. Debian package version is `20250419+deb13u1`.
- Alpine package version is `20261020-r0`.
- Five verifier unit tests pass, including rejection of DNS errors as proof of
  certificate blocking. Hospital workflow YAML parses; 34 network jobs have CA
  bootstrap. The local status-only gate does not need an external trust store.
- Four workflow security guards and 42 recovery/artifact tests pass. Historical
  comparisons use the starting develop SHA and isolate the exact reviewed CA delta.
- Hospital lock check passes; unrelated dependency versions were retained.
- Embedded Shift and school-lunch source manifests match the updated files;
  their original source revisions remain recorded with the security patch SHA.
- Read-only local inventory found 240 virtual environments. No environment is
  considered active merely because it exists, and none was bulk-modified.
- Platform: 329 local tests pass, including TLS probe failure semantics.
  Actual images are checked before push/deployment, including promoted images.

## Remaining verification

- Review, commit, CI, integration and staging rebuild of all affected images.
- Full application-image checks (base-layer checks alone are insufficient).
- Inspect active local virtual environments and non-Homebrew/system tools.
- No cloudflared installation, running process, or matching launch-agent entry
  was found in the inspected Mac inventory. Go/Bun were not installed through
  the inspected Homebrew environment; other hosts remain outside that evidence.
- Production rollout requires the existing release and approval gates.
- Record serving digests and actual image package versions after deployment.

## Reproduce

`python scripts/test_tls_trust.py -v` checks verifier failure semantics.
`python scripts/verify_tls_trust.py --online` checks Python's active stores.
`node scripts/verify_tls_trust.mjs` checks Node's effective TLS configuration.
Run these inside the actual image; do not override its CA settings for the test.
No credentials are sent to the public probe hosts. Network/DNS errors are errors,
not evidence that a compromised certificate was rejected.

## Sources

- https://emudhra.we-have-a-problem.com/
- https://discuss.python.org/t/information-for-python-users-regarding-emudhra-emsign-ca-certificate-security-incident/109576
- https://ubuntu.com/security/notices/USN-8944-1
- https://lists.debian.org/debian-security-announce/2026/msg00487.html
- https://nodejs.org/api/cli.html#--use-bundled-ca---use-openssl-ca
- https://github.blog/changelog/2023-10-05-github-actions-node_options-is-now-restricted-from-github_env/
- https://docs.cloud.google.com/sdk/docs/configurations
