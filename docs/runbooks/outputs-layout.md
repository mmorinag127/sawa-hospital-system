# Outputs Layout (templates/exports)

- Templates bucket (`*-templates`):
  - Store label/納品書テンプレート files.
  - Organize by version or facility if needed.

- Exports bucket (`*-exports`):
  - Suggested path: `week/{WEK}/facility/{FAC}/` and under that `labels/`, `delivery/`, `aggregate/`.
  - Ensure label CSV fields match現行シール例の項目セット.

- Raw bucket (`*-raw`):
  - Holds incoming PDFs; lifecycle deletes after 1–2 months (configurable).
