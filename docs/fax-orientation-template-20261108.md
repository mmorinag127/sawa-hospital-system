# FAX Orientation And Template Recovery

## Scope

Reported production order: ORD8b3b2ede, FAC00014, 2026-11-15 through
2026-11-21. Base develop/production source:
419046eb02d6c7e758da897fd28bec3656809ca0.

## Findings

The live workflow reports template_version_mismatch. Its saved-facility read
returns 409 canonical_lineage_provenance_missing. The explicit canonical
facility read for FAC00014 and 2026-11-15 succeeds and contains an authored
template. Thus the UI's unregistered-template claim was unsupported.

## Changes

- A mismatched workflow is not context-confirmed. Step1 reads the explicitly
  selected facility/week from canonical JSON and requires the existing guarded
  context-save command before OCR. Saved-order reads for valid confirmed
  contexts retain their exact version contract. No retry with a guessed source.
- Failed reads are distinguished from an actual missing template.
- The four-point review includes an optional 180-degree rotation checkbox.
  The PUT validates 0/180 and retains that choice with the source receipt.
  For 180 degrees the source corners become bottom-right, bottom-left,
  top-left, top-right before the existing perspective transform maps them to
  template top-left, top-right, bottom-right, bottom-left. Both axes turn;
  this is not mirroring or merely rotating browser pixels.
- Reopening the review restores raw-image corner coordinates separately from
  the saved reading orientation, preventing a second unintended half-turn.
- Original PDF bytes, selection receipt, saved quantities and history are not
  overwritten. Existing source/revision checks and explicit OCR adoption remain.

## Verification

- Backend related batch: 64 passed, 1.81 seconds. Includes real save/read/normalizer
  roundtrip, rotation rejection without mutation, and actual OpenCV pixel checks
  for all four quadrants with input image unchanged.
- Frontend related batch: 314 passed, 20.93 seconds. Includes production command
  closures for selected orientation, exact source/CAS, reload, late responses,
  dirty editors and template read selection.
- Logs: /tmp/sawa-fax-orientation-backend-20261108.log and
  /tmp/sawa-fax-orientation-frontend-20261108.log.
- TypeScript: `tsc --noEmit --incremental false` succeeded; log
  /tmp/sawa-fax-orientation-types-20261108.log.

## Release Status

Source 03a44554 was fast-forward pushed to develop after the user approved
staging verification followed by production deployment. Remote develop had
been 419046eb, exactly the reviewed base; no newer source was discarded.

These source changes are not yet deployed. No Actions, budget changes,
production context save, production OCR execution or business mutation was
performed. A successful local test is not live-production acceptance.

The production gate requires full backend execution. The previous specifically
approved corrective receipt covers deployment/read-only corrections, not this
new rotation API/UI feature. It must not be silently extended to assert the
new feature passed the old full run. A concrete question about signed
baseline-plus-difference verification versus executing the existing full run
was sent to the user. The user then explicitly approved signed difference-based
verification. Receipt version6 now verifies the signed source419 baseline,
successful owner production run42460685431, ancestry, an exact reviewed path
allowlist, and fixed backend/frontend/typecheck commands on a clean source.
It records full_rerun=false. Dependency/migration/unreviewed paths, failed or
missing commands, substituted sources, and recursive change baselines fail.
Existing owner dispatch, production approval, authentication and rollback gates
remain unchanged. No unexecuted tests are asserted as passed.

Pre-deploy staging baseline: worker-stg-00930-7tk, image
sha256:e4cd9807911f9bc52de6420acb38383417fe916d0edce02077363d355e6c7092,
100 percent traffic; web-stg-00485-zjs.
