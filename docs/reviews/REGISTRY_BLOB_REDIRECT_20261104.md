# Registry Blob Redirect

## Failure And Root Cause

Hospital staging run 41848596350 stopped before migration/deployment with
`staging-db-registry-blob-redirect-target-rejected`. The shared target resolver
accepted absolute Google Storage locations only. Artifact Registry returned a
relative `/artifacts-downloads/namespaces/sawahospitalsystem/repositories/backend/downloads/[redacted]`
location. Read-only inspection confirmed that anonymous same-origin retrieval
returned the config bytes matching the original manifest's SHA256.

[RFC 9110 section 10.2.2](https://www.rfc-editor.org/rfc/rfc9110.html#section-10.2.2)
defines Location as a URI reference and permits relative references. The observed
Google download path is a measured service response, not a claim that its internal
path format is a documented stable public API.

## Bounded Fix

- Accept the observed relative download path form only from the pinned HTTPS
  registry and this project's backend repository request prefix.
- Validate the raw path before URL resolution, then require it to remain unchanged.
- Reject other projects/repositories, traversal, encoded separators, whitespace,
  userinfo, fragments, unexpected ports, protocol-relative URLs and unknown hosts.
- Never forward Authorization on the redirected request. Permit one hop only.
- Preserve manifest/config SHA256, full response-size, runtime identity and DB
  target checks. Local CLI execution still refuses non-Actions identities.
- Do not change IAM, TLS trust, the database, source bindings or production.

## Verification

141 related cases passed in 8.10 seconds, including the original 103 target-gate
cases and new redirect/security regressions. An independent review detected a raw
path-normalization gap in the first draft; it was fixed before integration and
covered by traversal/dot/control-character rejection cases. No expected values
were changed to hide the issue. Real registry probes never store signed URLs or
tokens; diagnostics retain static path components only.

The source-bound normal release checks and successful Actions migration remain
required before staging completion is claimed.
