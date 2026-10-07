# Shared menu rule ambiguity

Base `be16870ab249ba6dc70ab18e760be634578bb4cb`; branch
`codex/modernization-v1-hospital-rule-ambiguity-20261008`.

Failure class: equally ranked distinct canonical rules previously selected by
input order. Reachability: `menu_rule_service.create_rule` stores a new ID with
the supplied priority without a competing-rule uniqueness check; MenuRule has
no uniqueness constraint on matching scope/priority. `list_active_rules:190`
returns all active rows, captured by `_capture_output_sources` and passed to the
single production `_apply_menu_rules` caller (`output_builder.py:1827`). The new
owned test physically stores both competing active facility rules and uses the
actual reader, not a read double or fake receipt.

Invariant: unchanged additive type weight plus integer priority must resolve one
distinct best source before new receipt persistence. Distinct best payloads now
raise `menu_rule_source_ambiguous`; repeated identical dictionaries are one source.
No ID/first tie breaker. Lower-ranked matches do not cause ambiguity. The existing
global guard remains a no-op for already set quantities, including zero: all
highest-ranked globals leave the line unchanged. Mixed highest-ranked types are
not collapsed into that guard. Existing normalization/value assignment unchanged.

Tests cover reversed ambiguity, unique source, identical copied source, higher
priority, additive type weights, global existing/missing amounts, actual owned
registration/evidence/save/bagging and refusal without a new receipt. Existing
receipt payload/digest and accepted output lines remain unchanged. Positive
owned fixture uses synthetic matching facility/date PDF/JSON and external
recognition double; not customer OCR/operator/live proof. No fault read double
was necessary. No original test/assertion modifications.

## Execution

From this worktree root: `mkdir -p tmp/rule-proof/home tmp/rule-proof/temp`.
From its `backend`:

```sh
env -i PATH=/usr/bin:/bin HOME="$PWD/../tmp/rule-proof/home" TMPDIR="$PWD/../tmp/rule-proof/temp" PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-main/tmp/v1-hospital-task-sdk-20261007:. DB_URI="sqlite:///$PWD/../tmp/rule-proof/bootstrap.sqlite3" NO_GCE_CHECK=true GOOGLE_CLOUD_DISABLE_GRPC=true /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python -m pytest tests/unit/test_h2_menu_rule_ambiguity.py tests/unit/test_h2_derived_accessory_identity.py tests/unit/test_h2_output_source_capture.py tests/unit/test_h2_output_acceptance_receipt.py -q -p no:cacheprovider --basetemp=../tmp/rule-proof/cases --junitxml=../tmp/rule-proof/results.xml > ../tmp/rule-proof/pytest.log 2>&1
```

Result: **115 passed, 149 warnings, exit 0**, 16.73 seconds. Own formal migrated
SQLite per case and owned HOME/temp/output; trusted Python/SDK read-only.

All artifact paths relative to absolute root
`/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-v1-rule-ambiguity`:
- `tmp/rule-proof/results.xml`: SHA256 `4815b79e46c596d52cebbd265e99fb46c3baa27eac333e8bbf07c727a7560df9`
- `tmp/rule-proof/pytest.log`: SHA256 `3280ccfb4a39104ca2091b3b40585c904e535c0fc68ad50b75710fc8098b230e`
- `backend/src/services/output_builder.py`: SHA256 `2d139986725b05a44407e766f557388d923e1df908eedd10b40bfe8316ebbd76`
- `backend/tests/unit/test_h2_menu_rule_ambiguity.py`: SHA256 `8b3ce877812a048e36dcdd600ae823db2af4d19e1da4f9ba322e5b0ff8e60628`

Historical initial production diff: one file, 12 added/4 removed lines. Three-file source
packet only; prior integrated derived packet is not restaged. Proof generated
from named base plus this uncommitted diff, not a parent-integrated SHA. Parent
dual-monitor review and fresh-SHA regression pending. No commit/main writes,
deploy/browser, dependencies, master workbook or configuration schema changes.
Not whole-modernization or all-fallback-absence acceptance.

## Final Guard Simplification

Parent review identified the later selected-global guard as unreachable: the
earlier all-best-global guard already handles the unique-global case. Removed
only that redundant branch; no tests or precedence changed.

Final execution: the exact command above with every `tmp/rule-proof` path
replaced by fresh `tmp/rule-final` (owned directories created first). Same four
focused test files and environment. **115 passed, 149 warnings, exit 0**, 17.61s.
Initial `tmp/rule-proof` artifacts/hashes remain historical and are not final
source proof.

Final SHA256:
- `tmp/rule-final/results.xml`: `760a75db74671c46a7beac3fbdbe9bbf004d2d0773565ac3c63ad1bff8527994`
- `tmp/rule-final/pytest.log`: `3659d5f68926feb705ac2b05d302b8ddd2dd2b1e7de0c14e39dfe7e9d242cea4`
- `backend/src/services/output_builder.py`: `e72957023a947e9cd9224761df4df4eb65c7c4a26a8a8c482260c300f6d2e332`
- Test source remains `8b3ce877812a048e36dcdd600ae823db2af4d19e1da4f9ba322e5b0ff8e60628`.

Final manual production diff: one file, 10 added/5 removed lines versus base.
Three-file packet frozen for parent review; no commit/integration/deploy.
