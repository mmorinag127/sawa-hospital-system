# Staging OCR producer contract deployment

## Persisted Unselected Workflow After Ingest Failure

The read-only API can serialize an existing row without a
`workflow_not_initialized` marker. The resume identity check also accepts the
explicit `template_unresolved` blocker with `state=uploaded`, all four context
fields present and null, and all six result fields present and null:
`selected_ocr_result_id`, `saved_sheet_id`, `confirmed_snapshot_id`, `ocr_job`,
`output_bundle_id`, `bagging_result_id`. Missing fields, mixed context, nonnull
results, or unknown markerless states still stop. All owner/hash/canonical checks
remain mandatory. `context_suggestion` is never adopted as evidence.

On identity failure, `resume-identity-failure.json` records only match predicates,
field presence/null flags, and the two recognized marker predicates. It does not
contain raw API values, lease credentials, suggestions, or exception messages.
`resume-failure.json` also records a fixed stop phase/reason for other resume
failures. These files are diagnostics, not permission to retry or bypass the API.

Failure class: worker and producer from different source contracts. A legacy
`output/<input>.json` cannot satisfy the current generation/owner-specific output
name and lineage validation. No legacy output adoption or recognition tuning is added.

## Actions ordering

Keep the existing job graph and develop/source gate. `build-backend` additionally
builds `ocr_pipeline/Dockerfile` from the same checkout and pushes the producer
with `sawa.git_sha` and `sawa.release_branch` labels. Its immutable digest output
is consumed in `deploy-backend`, using the existing staging WIF, before worker
deployment. Producer failure stops the worker step. CI additions are parent-owned.

The deploy script accepts only the exact staging project, region, service image
repository, develop Actions context and matching checkout/image SHA. It pulls the
immutable image to verify its source labels. No alternate identity or IAM repair
is attempted. The known missing deployer actAs permission remains an external
approval prerequisite; this implementation does not grant it.

## Configuration preservation

Capture the live service, its full exported specification and IAM policy in memory.
Require Ready and 100% latest-revision traffic. Verify target namespace and runtime SA.
Before replace, reread the ready revision and export; any change stops deployment.
Change only the container image; remove the old template metadata name so Cloud Run
can allocate a revision. No other metadata normalization is performed.

Serialize the complete replacement spec into a mode-0600 file in a private temporary
directory, call `gcloud run services replace`, and remove the temporary directory.
Never upload or print that spec, environment values, secret references, raw command
output or exception data. After replacement, require Ready/100% latest traffic,
the exact requested digest, equal exported settings (except generated revision name)
and unchanged IAM. Unexpected server metadata changes are blockers, not silently
discarded differences. Safe output contains only source SHA, image digest and revision
names, or fixed phase/reason/returncode on failure.

Preserved settings include all existing env and secret references, SA, resources,
concurrency, timeout, probes, port, CPU throttling, scale limits, Cloud SQL, ingress
and traffic configuration. No Eventarc creation, Terraform apply, recognition model
settings, production changes or partial environment update is included.

## Explicit existing-upload manifest

Default cases continue to POST a new upload; duplicate rejection remains a stop.
Only a manifest containing all three nonempty fields selects resume:

```json
{
  "existing_uploaded_pdf_id": "UPL2f6c937d",
  "expected_order_id": "ORD581f55d6",
  "expected_document_id": "DOC0d4cb340"
}
```

These supplement the existing visually verified PDF URI/hash, facility, explicit
week, effective date and expected menu-row fields. No automatic duplicate fallback
exists. Before retry, compare current upload, order and current-version IDs, hash,
facility/week, message/storage linkage, skip_ocr=false and canonical selection.
Download the actual registered PDF and compare its hash too. Re-resolve the dated
canonical selection before POST and stop if it changes.
Downloads use the shared pure `ocr_pipeline/app/storage_uri.py` parser. Application
`?generation=N` becomes the exact gcloud `#N` version selector; unversioned objects
remain unversioned. Other buckets, unknown queries/fragments, invalid generations,
wildcards and ambiguous decoded object characters stop before any CLI invocation.

The order's CFV is compared with the internal canonical projection identity derived
from the complete authored version returned by the dated facility API. This is an
internal ID contract, not a public receipt API. The regression test explicitly reads
the reported artifacts and verifies the actual CFV
`CFV571556a9848db8406264cb50d45eeabb36b4fbd92da28ba2df3c9a05618cebf8`.
Any future change to canonical projection identity requires coordinated verification.
No context_suggestion is used as canonical evidence.

The observed uninitialized workflow has `state=uploaded`, blocker
`workflow_not_initialized`, and all four selection fields explicitly null. That
combination remains accepted; persisted unselected rows must instead satisfy the
strict `template_unresolved` contract documented above. Otherwise all four fields
must match; missing fields, mixed nulls and inconsistent markers stop execution.
Ownership is rechecked while polling after retry. Already-completed uploads skip
retry after ownership checks and continue the existing Hakodate verification flow.

The operator `POST /ingest/uploads/{id}/retry` owns lease/restart guards. HTTP failures
propagate without deletion, direct DB access, skip-OCR, or a replacement upload.
No lease-token values are stored in the new resume-before artifact or test fixture.

## Verification boundary

Unit tests mock all cloud/API operations. The opt-in real-artifact test uses
`STG_OCR_RECOVERY_ARTIFACT_DIR` read-only; it does not replay a request. Configuration
preservation, source/target rejection, denied authority, revision/traffic/digest
checks, identity mismatches and null-marker states are covered. Parent must still
integrate, run authorized Actions, and inspect actual staging OCR and STEP2. Local
tests are not evidence of deployment or user-visible OCR completion.
