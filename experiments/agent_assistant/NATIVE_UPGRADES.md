# Packaged native API upgrades

The bundled School and Shift APIs retain agent-specific extensions. Replacing an
entire native tree with an upstream checkout discards those extensions.

Current upstream pins:

| Domain | Source revision |
| --- | --- |
| School | `9ad04a60a7038ad3c751d944f86749dda912ee1e` |
| Shift | `d516f8e9756d1436ea870d264c964b2de9b0efbe` |

Manifests record every bundled file hash, the upstream delta hash, and each
retained extension's upstream/bundled hashes. School's drawing questions,
ingredient placements, saved price outputs and answer provenance remain present.
Shift's planning-period endpoint remains present.

## Updating a pin

Use explicit repository, bundle, manifest, target commit and report paths:

```sh
python -m experiments.agent_assistant.update_native \
  --repo "$SOURCE_REPO" --bundle "$BUNDLE" --manifest "$MANIFEST" \
  --target "$TARGET_SHA" --report "$REPORT"
```

This applies the exact upstream delta by three-way merge against the previous
pin. Conflicts remain visible and prevent sealing. Review conflicting hunks and
all retained extensions against both source revisions. Add/delete ambiguity
requires explicit source integration; the tool never picks a candidate.
After review, repeat the command with `--seal`. Runtime verification rejects any
subsequent source mutation. Keep the report with integration evidence.

## Verification

```sh
SAWA_AGENT_NATIVE_TESTS=1 \
SAWA_AGENT_NATIVE_SHIFT_PYTHON=/path/to/shift/venv/bin/python \
SAWA_AGENT_NATIVE_SCHOOL_LUNCH_PYTHON=/path/to/school/venv/bin/python \
python -m pytest experiments/agent_assistant/tests/test_native_workflows.py -q
python -m pytest experiments/agent_assistant/tests/test_update_native.py -q
```

The opt-in tests start the actual packaged APIs against owned copies of synthetic
fixtures. Portal identity is a token-checking localhost stub; model responses
are mocked, with no paid inference. API service authentication remains enabled.
`SAWA_ENVIRONMENT=test` applies only to these owned SQLite subprocesses; parent
authentication bypass variables are not inherited.

Coverage includes every catalog operation's real OpenAPI declaration; drawing
hashes/options; actual OR-Tools solve, human approval, restart/resume, official
shift XLSX and rollback/download revocation; School's four ingredient/location
answers, process/price/schedule saves, restart at approval, completion and three
official XLSX outputs. Synthetic price inputs calculate to 2,000 yen.

These checks do not prove paid model quality, live DB migration, or the missing
real School D1 input. D1 remains blocked by its absent original source; a
synthetic fixture is never presented as D1 evidence. Source-contract reports
retain explicit additional declarations instead of claiming byte equality with
upstream.
