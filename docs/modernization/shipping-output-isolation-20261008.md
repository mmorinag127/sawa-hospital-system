# Shipping output isolation

Base: a91b4c745e6d99ffd33ac9d1a3b97ac81435507c.
Branch: codex/modernization-shipping-output-isolation-20261008.

Failure class: hardcoded shared export directory prevents isolated process ownership. Shared decision point: shipping_service.OUTPUT_DIR. Invariant: explicitly configured owned output directory is used; unset behavior stays /tmp/shipping-outputs.

Only production change:
`OUTPUT_DIR = Path(os.getenv("SHIPPING_OUTPUT_DIR", "/tmp/shipping-outputs"))`.
Existing mkdir, all function bodies, template selection, filenames, workbook contents, quantities, API/auth and imports are unchanged. No artifact lookup/fallback added. No environment or deployment configuration modified.

New unit definitions (not executed) evaluate only the parsed directory assignment and two actual output_path expressions, without importing shipping_service or executing mkdir/workbook save. Default/explicit destination, exact environment key/default, absence of writes, shipping_20261008_123456.xlsx and shipping_enriched_20261008_123456.xlsx are independently asserted. Full source equality against base permits only this one assignment substitution. Recorded timestamp is synthetic component input, not live output evidence.

Source SHA256:
- backend/src/services/shipping_service.py: de2da8ce2ab9013805967ea36620e614fcb03af6a0bbf2df60e2923305479d83
- backend/tests/unit/test_shipping_output_isolation.py: 9b89fd31cdf8fcdfd731370e535a502d8c284ec784a5e392cf709fe19aa1b57d

Commands: git status --short; git rev-parse HEAD; git branch --show-current; cat AGENTS.md; sed/rg production output definitions/usages; rg --files -g AGENTS.md; git diff -- backend/src/services/shipping_service.py; shasum -a 256; git add exact three files; git diff --cached --binary | shasum -a 256; git diff --name-only. All source/Git/hash commands exit0. Manual changes via apply_patch.

No tests/imports/exports/DB/network/API/browser/server/install/commit/deploy executed; no shared output or locked master workbook written. Source diff shows exact one-line production change. Unit execution and consolidated real owned-output artifact/content verification remain pending; source proof is not runtime or full modernization acceptance. All owned sessions finished. Binary patch hash is returned externally to avoid self-reference.
