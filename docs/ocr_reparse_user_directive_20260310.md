# OCR Reparse User Directive

Updated: 2026-03-10

## Purpose

This document records the user's explicit, authoritative requirements for OCR reparse behavior.
Any future OCR/reparse implementation, debugging, or design discussion must follow this document first.

## Mandatory Rules

### 1. Default

- Default source of truth is `yomitoku`.
- Do not switch the default to `LLM-first`.
- If `yomitoku` is acceptable, keep `yomitoku` as the adopted result.

### 2. Explicit Reparse Requested By User

When the user explicitly requests reparse, the flow must be:

#### Case A: `yomitoku` result exists

1. Keep the existing `yomitoku` result as the baseline candidate.
2. Pass `yomitoku result + current sheet/baseline` to an evaluator LLM.
3. Use the evaluator result as input/context for the LLM inference pass.
4. The LLM inference pass must be based on that evaluation, not a free rewrite with no evaluator context.

#### Case B: `yomitoku` result does not exist

1. Run an LLM inference pass to fill the sheet.
2. Pass `LLM inference result + current sheet/baseline` to a separate evaluator LLM.
3. Run a second LLM inference pass using the evaluator result as guidance.

### 3. What Must Not Happen

- Do not reinterpret the requested flow as "single LLM candidate + audit only".
- Do not treat `yomitoku` only as rescue/reference when the user explicitly asked for `yomitoku` result evaluation first.
- Do not silently replace the above flow with an implementation-convenient alternative.

### 4. Review Principle

- `yomitoku` and `LLM` should be treated as distinct candidates/process stages when the user requests reparse.
- Evaluation must be explicit.
- Re-inference must explicitly use evaluator output.

### 5. Adoption Principle

- Default adoption remains `yomitoku`.
- `LLM` is for improvement/repair when justified by the reparse flow above.

## Current Problem That Triggered This Rule

The prior implementation drifted into:

- `yomitoku/pipeline` as baseline or rescue only
- one main `LLM` candidate
- separate audit on that `LLM` output only

This is not the user-requested design and must not be repeated.

## Required Working Style

For any future OCR/reparse task:

1. Read this document first.
2. State that the implementation is being checked against this directive.
3. If the code differs from this directive, treat the code as wrong and the directive as authoritative.

