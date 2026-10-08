# Owned evidence before retired draft rejection

Base: `0d8f342f3b40aa9a97fa897d309fbd20810b4720`.
Branch: `codex/modernization-hospital-owned-draft-rejection-20261008`.

Only `test_legacy_draft_sheet_endpoint_is_disabled_after_evidence_persistence`
and this document change. The old manual order/template fixture and direct
`persist_evidence_run` setup are replaced with the existing passing inline case's
authored source/upload/reservation/registration/delivery/provider helpers.
No shared helper or product code changes. The registration-only fixture is not
used because this case intentionally executes the authored component pipeline.

Original message `msg-draft-endpoint-build`, facility `FAC00001`, March 22 2026,
breakfast, `Menu A`, regular/2F mapping, and evidence quantity 5 are retained.
Received time remains the original seed's `2026-03-22 09:00:00`. Selected week is
the existing inline fixture's `2026-03@2026-03-22~2026-03-28`. The old seed's
quantity-3 registration row is replaced with actual recognized quantity 5 from
the authored provider table, not presented as unchanged row setup.

The external OCR provider double receives rows `03/22`, `朝`, `Menu A`, `5`.
Existing helpers perform real source registration, delivery claim, pipeline
receipt recording, mapping, and evidence persistence under captured ownership.
No owner/guard/finalizer mock or persistence observation wrapper is added.
Added definitions check reservation identity, registration/order identity, actual
stored evidence ID linked to that order, producer identity, and recognized
`OrderLine.quantity_original == 5` before the original endpoint check.

Original GET `/orders/{id}/draft-sheet`, status 410, and exact
`legacy_order_workflow_disabled` assertion remain unchanged. Every other case,
including the old 200 oracle, remains unchanged. No date/digest root repair is
part of this packet.

Source definitions only: no tests, builds, runtime imports, DB execution,
network, browser, staging, commit, push, or deployment. Quantity 5 is a synthetic
component input, not real FAX/operator/recognizer parity proof. Assertions and
owned lifecycle coverage remain unexecuted pending parent review, integration,
and one consolidated seven-module verification. File and tracked-diff hashes
are reported externally; the untracked document is excluded from Git diff.
