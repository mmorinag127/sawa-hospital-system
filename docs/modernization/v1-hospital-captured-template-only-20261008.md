# Captured-template-only source packet

Base: 4677ef58dd94c5c99eef228b03691bf6cace8a75.
Branch: codex/modernization-v1-hospital-captured-template-only-20261008.
Worktree: hospital-v1-captured-template-only, exclusively owned.

## Boundary and caller census

Failure class: absent capture permits shared loader/matcher/classifier to discover alternate template sources.
Invariant: receipt-validated full captured JSON and explicit matching template identity precede template DB/image work. Matcher consumes exactly one explicitly authored canonical template, never a multi-facility candidate search.

- rois.load_template_config: existing digest validator now unconditional; absent/wrong identity stops. db/collection signature retained but unused.
- template_match.choose_template_and_warp: singleton explicit selection and singleton authored map with matching internal template_id required before OpenCV/download. Removed DB discovery and empty registry helper.
- main._run_template_classification: loader validation and foreign/duplicate candidate rejection precede disabled-stage return and rendering. Removed unreachable second matcher/discovery decision. Exactly one original preferred matcher call remains, including str(requested_template_id).strip(), matching error, preferred_primary mode, score/diagnostics and context.
- Production loader calls: main classifier entry and accepted matched-template context read.
- Production matcher calls: one main classifier call and page_correction._try_template_warp. Main handler validates full capture and facility/template identity, supplies captured_config/digest to classification and singleton authored map to correct_pdf_for_yomitoku. Page correction forwards that map unchanged. Its rotation/geometry/preprocessing code is untouched.
- Full repository search before retirement found OCR_TEMPLATE_MATCH_ALLOW_DISCOVERY only in its main.py declaration/env read, with no callers or test references. Removed that unused constant. Existing missing-capture stop definitions remain; parity definitions explicitly authorize only this exact constant deletion outside classification.
- Removed default_template_collection had only loader/matcher and obsolete collection tests as callers. Removed _template_sources had only matcher and obsolete test patch references. Empty _template_sources_from_registry had no callers.
- Separate backend.src.services.fax_template_matcher and compare_structure_guided_ocr use a different module, untouched. ocr_pipeline_spec.md contains historical discovery examples, not executable callers. Historical h2-transitive-captured-inputs doc allowed independent diagnostic DB sources; this approved packet retires that exception.
- Source/scripts/tests/config and hidden tracked-source searches found no additional alias/dynamic callers of removed helpers. Existing collection/discovery environment declarations are not altered, and no global fallback-absence claim is made.

## Definitions and preservation

Migrated legacy tests expected env-selected DB reads or empty-DB failure after reading a local template image. Authorized new contract stops before any DB/image work without capture. No live fixture is substituted.
Definitions cover valid full template/deep-copy/zero/null, missing capture, wrong digest/ID, foreign and duplicate candidates, disabled and enabled classification pre-render rejection, actual no-descriptor diagnostics, and actual canonical synthetic-raster match/warp.
Existing test_main valid captured loader/classifier/ROI assertions remain unchanged. New source parity definitions compare base bytes for all ROI geometry code, image download, complete matcher ORB/score/homography/warp tail, main code outside classification, and ROI extraction. No geometry-unit normalization is changed.
Existing page-correction tests use matcher doubles (including signatures lacking authored_templates); they are unchanged adjacent residuals, not real matcher proof.

## Source Counts

Total/nonblank before -> after:
- rois.py: 477/420 -> 460/405.
- template_match.py: 174/149 -> 147/127.
- main.py: 1525/1391 -> 1496/1362.
- test_template_collection_config.py: 73/48 -> 97/85.
- test_template_match_fallback.py: 52/35 -> 75/65.
- new test_captured_template_source_parity.py: 82/72.

## Commands and Pending Evidence

SHA256:
- rois.py: d9f71022e1502a16dfce2384a7414486be1e2c25d24ae143a4290d5245a35d35.
- template_match.py: 50079d7057971430ab87db085ca3ae284c305cfc73930bbd08dac072669c2eae.
- main.py: 03182a3fc1c29bdfce309448aa884b1ff3fd4726b7c2b8d71020fddcbb4ae8f5.
- test_template_collection_config.py: 5ba0de4e2748f3667260bd0c0ba0dba9ea644e404799f59e942af86ba4dccb15.
- test_template_match_fallback.py: ad0359417d37cc2308b6f8e98f525e07b60ffa33c14d98a395ee308c1ab62ad6.
- test_captured_template_source_parity.py: 2efbed447428d84fb12abc706df166dbfa31746fceb7a41227f719fba1bb73cc.

Executed only read-only cat/sed/rg/git status/rev-parse/show/diff, source counts with awk, SHA256 with shasum, apply_patch, git diff --check and exact-file staging. git diff --check exited 0. One rejected apply_patch used duplicate delete/add targets and changed no files; replaced with valid whole-file update patches.
No Python/Node imports, tests, builds, DB, OCR, runtime, servers, browser, installs, network, commit, merge, push or deploy executed. No owned processes started.

Pending consolidated execution: the three template test files, unchanged test_main captured-source case and page-correction siblings; explicit missing/foreign/duplicate stop cases and valid image/ROI parity. Module-stub contamination in existing test_main must not substitute for actual matcher execution. Full valid original PDF/facility/week recognition, provider/live/operator flow and integration SHA checks remain unverified, not waived.
This is a frozen source packet, NOT root completion or live proof.
