# Restart recovery: local verification, 2026-10-22

Base: `cbc4ca7c0d531b82876b6c9ba487ec737ce71fd4`.
Branch: `codex/assistant-restart-recovery-20261022`.

## Implemented

`POST /runs/{id}/resume` authenticates the owner and refreshes the SAWA bearer
authorization, then uses the run's pinned provider. The screen offers
「保存済みの結果を確認して再開」only for `reconciliation_required`.

Recovery compares Gemini interaction GET/checkpoints or Claude session/event
history with durable command receipts. Completed tool calls reuse the same
receipt. Existing questions retain their IDs, choices and drawings; a missing
conversation event is repaired from the receipt. Read-only approval/job waits
can rebind to the new generation. Unknown dispatches, unfinished mutations,
missing working copies, mismatched receipts, terminal/stopped runs, unknown costs
and reached spending caps remain blocked. A provider response with no pending
call is not invented into a question or declared business completion.

Working business data previously disappeared on instance replacement: only
before-images were persisted and hydration copied them back into `data`.
Working SQLite databases now use online backup (including committed WAL), and
working files use content-addressed checkpoints. Their manifest and command
receipts commit in the same PostgreSQL transaction. Hydration restores that
manifest; it never substitutes a before-image. Old runs without checkpoints
remain blocked. Original before-images and sealed artifacts stay immutable.
`home` model caches are excluded and are rebuilt from configured runtime caches.

## Native API comparison

Remote `develop` heads were checked with `git ls-remote`; exact objects were
fetched without modifying branches/worktrees. The checker examines all backend
Python files except tests/migrations, including flat `main.py`, route mounts,
router declarations, handler signatures and class fields. This source comparison
does **not** establish business behavior, response-body or migration parity.

| Domain | Packaged revision | Current develop | Coverage | Differences |
| --- | --- | --- | --- | --- |
| Shift | `d4d9a6b9` | `d516f8e9` | 41 files, 81 handlers, 190 classes | 10 declarations; `staging_data.py` absent in bundle |
| School | `f7613fb2` | `5c93cff4` | 124 files, 100 handlers, 254 classes | 19 declarations; `staging_data.py` absent in bundle |

Shift's packaged `/planning/period` extension is absent from develop; position
preference/priority/rule handlers also differ. School's packaged question-drawing,
saved-price preview/export and daily-work provenance extensions are absent from
develop. Develop adds process-default preparation, kitchen diagram fields and
changes procurement export inputs. Therefore replacing the bundle with develop
as-is is **not verified compatible**. Neither native source was replaced here.

Reproduce with `python -m experiments.agent_assistant.check_native_contracts`
and explicit `--bundle`, `--repo`, `--revision`, `--output` arguments. Full
machine-readable reports and local test logs are in `tmp/recovery-proof/`.

## Verification and limits

Local tests cover both providers' question recovery, receipt/event crash windows,
unknown sends/mutations, authorization, spending limits, duplicate resume, read-only
waits and stop fences. Actual disposable PostgreSQL tests cover instance epoch
replacement, working quantity 21 versus original quantity 7, committed WAL,
same-question recovery, immutable originals, reservations and legacy-copy blocking.
React/proxy tests exercise the recovery button and unchanged question display.

No paid model request, existing stg ledger write, push, merge, Actions run,
deployment or production operation was performed. Live recovery and latest-native
business-flow verification remain unperformed; the earlier stg business-flow
screenshots do not prove this new recovery implementation.
