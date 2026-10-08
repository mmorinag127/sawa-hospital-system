# Unused cache evidence entry retirement

Base: `23b33fb419dd7d9efab3b3cda53fabf6bf166440`.
Branch: `codex/modernization-hospital-unused-cache-entry-20261008`.

## Boundary and invariant

Failure class: dormant legacy-cache-adoption entry. The shared entry
`backfill_evidence_run_from_cached_payload` supplied legacy producer/source
defaults to normal persistence. Remove only that unused function and its two
following blank lines. Normal producers, `persist_evidence_run`,
`build_evidence_run_record`, imports, and all other source bytes remain untouched.
This is not a global fallback-absence or user-visible bug-fix claim.

## Read-only caller census

Commands executed before editing:

```sh
git rev-parse HEAD
git status --short
git grep -n backfill_evidence_run_from_cached_payload
git grep -n -E 'getattr\(|import_module\(|__import__\(|ocr_evidence_service|legacy-cache-backfill' -- backend ocr_pipeline scripts
git grep -n -E '(getattr|globals|locals|import_module|__import__|eval|exec).*ocr_evidence|ocr_evidence.*(getattr|globals|locals|import_module|__import__|eval|exec)|backfill.*cached|legacy-cache-backfill' -- ':!docs' ':!frontend/package-lock.json'
```

Whole tracked repository exact identifier results: service definition at original
line 684 and historical documentation
`docs/strict_atomic_remediation_detail_plan_20260506.md:101`. No literal caller,
import alias, or registration was found. The broader module/dynamic search was
large; focused dynamic-dispatch/legacy search returned only the definition and
legacy label handling. Remaining legacy-label references are the historical
migration, persistence refusal, selection refusal, workflow refusal, and a
workflow negative fixture; none dispatches this entry. No module-targeting
dynamic caller was identified. Arbitrary externally supplied code or future
computed names are not covered by this static census.

## Unexecuted definitions

`backend/tests/unit/test_unused_cache_evidence_entry.py` uses standard-library
AST and fixed-base read-only Git source. It defines exact text parity after
removing the function and following separator, whole remaining module AST
parity, individual remaining function AST parity, and a tracked-source literal/
AST reference census. It does not import or invoke production services or native
runtime operations. Historical docs and this test's identifier construction are
excluded from the caller assertion, not production source. The existing six
negative cache cases in integrated main are not edited in this packet.

## Freeze limits

Only the service, new unit definition, and this document are changed. No stage,
commit, test, build, runtime import, DB, network, browser, push, or deployment.
No master, date, quantity, recognizer, or default changes. Technical execution
and integration remain pending parent frozen-diff review and monitors. File and
tracked binary-diff SHA256 values are reported externally to avoid a
self-referential document hash; the tracked diff excludes these untracked new
files until the parent stages them.
