# Bounded main fixture repair (source only)

Target: hospital-h2-revisions HEAD e4788eb83e43a08e1ef74963131fde5d856a5a6c.
Only ocr_pipeline/tests/test_main.py and this document are changed.

## Actual wiring

- Required-stage failures derive Stores.output from the verified original source
  through _original_pipeline_source, _owned_output_name and _terminal_source.
  Each selected failing computation must actually be called. Output filtering
  uses that owned path, not the old basename-only Stores default.
- Recognizer SystemExit uses Stores input/configured/selected-PDF/execution
  receipts and real handler creation, Firestore version checks, intermediate
  generation checks and terminal persistence. Assertions require actual
  recognizer dispatch, FAILED/error_type SystemExit, full original source,
  unchanged claim, exact result digest, immutable intent and dual-store ACK.
  No output write may be DONE. The obsolete outward SystemExit assertion is
  replaced with the handler's acknowledged FAILED contract.
- Corrected PDF publication uses the actual captured source, explicit original
  claim and _OUTPUT_OWNER, with its owned assets namespace. The upload spy
  delegates to Blob's actual conditional-storage fake, requiring
  if_generation_match=0. Bytes, generation 42, source metadata, corrected digest
  and original digest are asserted. Generation 42 is an external storage fixture,
  not a fabricated ownership receipt.

No ownership validator, terminal persistence function or source producer is
mocked. Classification/ROI/correction computations and recognizer failure are
external computation adapters; storage uses terminal_store_fakes without edits.
Captured configuration retains zero and null.

## Read Scope And Remaining

Read the affected test definitions, terminal_store_fakes, local main ownership,
upload and recognizer dispatch paths, and rois.validate_captured_config.
Read parent 42b36b27d8584a1100e0c758806568360adc0692 combined URI test source
and baf222d6ddd3d6822c6d3866b007fb69d28cfb83 main source read-only.
This is not a whole-repository audit or execution proof.

The repaired definitions target the parent integration's original-source helper
and URI codec. This WT does not yet contain that production integration:
_original_pipeline_source must arrive through parent integration, not a test
fallback or empty output URI. Parent combined URI definitions and all 193
deferred legacy cases are outside this repair and remain unverified.
No runtime, test, build, network or DB operation was performed. Only diff hygiene
is checked; integration/technical verification and overall H2 completion are
not claimed.
