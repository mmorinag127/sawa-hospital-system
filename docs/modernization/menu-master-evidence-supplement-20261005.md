# Evidence Provenance Correction

HEAD: `0455a69b37b7b729be808cf89b8c6615c8fd3af9`, WT `hospital-c1-live`. This supplements the preservation claim in `menu-master-live-implementation-20261005.md`; it does not replace either existing manifest or test result.

- The old manifest contains **59 hash references / 58 unique paths** (the candidate diff is listed twice). All were checked: 54 references unchanged, 5 changed, none missing.
- Four changed candidate files are expected DB-target follow-up edits: `scripts/verify_stg_menu_master_ui.py`, `frontend/tests/config/menu-master-live.test.js`, `backend/tests/integration/test_stg_menu_master_live.py`, and the implementation report. Their current hashes match `db-target-handoff-manifest.json`. Each original file was reconstructed in memory from the unchanged `tmp/live-tests/candidate.diff` and matched its old manifest hash exactly. No original source file was overwritten during this correction.
- One evidence helper, `tmp/live-tests/run-pg.py`, **had been overwritten**, so the prior same-path preservation claim was too broad. Removing only the subsequently added `test_staging_db_target.py` argument line reproduces the old SHA256 exactly. The recovered bytes were saved separately with apply_patch at `tmp/live-tests/history/run-pg-57441c41.py`. This is a byte archive, not a rerun or an independently discovered original copy; its relative ROOT calculation means it must not be executed from that archive location.
- All other old evidence references, including logs, images and the original patch, match their recorded hashes. Both existing manifests remain unchanged. All 35 references in the current DB-target manifest also match. Original 170-pass evidence remains historical evidence; current 222-pass evidence is not relabeled as proof of that earlier run. Existing failures/logs were not changed or removed.

## New Records

All paths below are relative to this WT. The supplemental JSON records every old reference, old/current hashes, and the actual retention path or patch-entry mapping.

| Path | SHA256 |
| --- | --- |
| `tmp/live-tests/evidence-provenance-supplement.json` | `1b3bb8663c2499d9c2784e693c4c5cae7dbdf7f3e8b95f0d3124f3901e4f5680` |
| `tmp/live-tests/history/run-pg-57441c41.py` | `57441c41f26fa5fb05eddd1a3b2e255489f526fefa7c272ce667a50c18439ac6` |
| `tmp/live-tests/audit-evidence-provenance.mjs` | `23e7515705a8138694e94b380a9a07c2816743cdd84d9394deeee09539cd79f1` |

Command: `node tmp/live-tests/audit-evidence-provenance.mjs --write`, exit 0. The output is exclusive-create; use the same command without `--write` for a read-only recheck. No application tests were rerun and no server/database/browser process was started.

The current `run-pg.py` remains byte-exact at `d9d1b4372c6f41634acefb2aa485a1f35c780a29f76c0bc42f5ffbc8527c8e67`. The 13-file staged diff remains `5cd1ad4f329f582430420ecba7f776d4e859a0cbf8b72d8c2ffbf8ab9f981910` (SHA256 of `git diff --cached --binary`). No product/workflow/guard edits, staging, commit or deploy were performed. This correction is not STG/C1 completion.
