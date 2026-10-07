# Hospital V1 Catalog Observation Freeze

Base Git HEAD: `1654ffa2ebb8f762dd473516a9e02d91a3bb417f`.
Generation used this base plus dirty source, not a committed generating SHA. Source hashes are listed below; parent integrated-SHA generation and tests remain required before technical acceptance. Five source files and this record are changed; no commit, merge, push, deploy, browser, network, or live/stg/prod access occurred during observation.

## Observation

Standard Alembic ran on new empty owned SQLite, PG15, and PG16 databases under `tmp/hospital-v1-catalog-20261007`. PG sockets used ports `55482` and `55483`, empty `listen_addresses`, owned data directories, role `mmorinag`, and administrative database `postgres` before creating only `c2_hospital_observe_pg15_20261007` and `c2_hospital_observe_pg16_20261007`.

Each standard run observed `0001` through `0034`, then stopped at the expected standard postflight blocker: `catalog blocked: revision 0034 has no observed <dialect> contract`. The observer never wrote the tracked contract. Raw artifacts remain under the worktree `tmp` directory.

Raw PG15/PG16 JSON differs in native owner-entry order. Existing `table_shapes` normalization compared every `0001` through `0034` equal; no new ignored attribute or normalization was added. Generator verified every historical contract shape before appending raw `0029` through `0034` observations.

## Results

The worker reported 106 initial focused PG15 errors because `SHOW unix_socket_directories` returned the short symlink spelling while the data/socket inode remained under the owned worktree. The ownership assertion now compares resolved paths, retaining data directory, server version, empty listen address, role, and database checks. The worker reported exit 0 for corrected focused PG15 and PG16 runs. Their exact stdout logs and counts are not attached here; these are unverified child reports, not parent verification. Parent integrated-SHA reruns are required before acceptance.

Commands used the trusted runner `/Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python`, `env -i`, owned HOME/TMP/cache, and `PYTHONPATH=backend`:

```sh
python backend/scripts/observe_hospital_catalogs.py --owned-root <worktree>/tmp/hospital-v1-catalog-20261007 --output <worktree>/tmp/hospital-v1-catalog-20261007/observations
python backend/scripts/generate_alembic_catalog_contracts.py --snapshot-dir <worktree>/tmp/hospital-v1-catalog-20261007/observations --base-contract backend/alembic/catalog_contracts.json --output <worktree>/tmp/hospital-v1-catalog-20261007/generated-catalog-contracts.json
python -m pytest backend/tests/integration/test_formal_alembic_fresh_chain.py -q
```

SHA256: observer `ad9258689fe23511c88758aa4f4f34828f054d4a8253df046c2272f5a1bce050`; generator `83e22b465788b5f546ca1aa41a5a34596da3467dbcb76a25a8489acd27e7e781`; schema `9a850d7ded34ffffaaf16cf77579d6217ffda5e8b3813fb0f500d21c91fbd766`; contract `5db21fa077f716466b70cff5e79f9e39ffe1e2b52fbf703e9a0b176b0fc71e9f`; focused test `f4b2ae9fe3181f2825a735738a55a81375d3ef37b69f598c1fb4f3f22458c243`.

This is a stage freeze only. It does not claim provider acceptance, live database coverage, source integration, or deployment readiness.

## Required Role And Database Owner Follow-Up

The observer now requires `--required-role` explicitly. Before migration it checks URI username, `current_user`, `current_database()` and `pg_get_userbyid(datdba)`. Connected role and database owner must equal the required role; the connected database must equal the explicit URI database. Existing physical data directory, socket, port, empty listen address, server major and empty catalog assertions are retained.

New raw observations live in `tmp/hospital-v1-catalog-20261007/role-observations-pg15` and `role-observations-pg16`; prior raw files were not overwritten. Both new databases were explicitly created with role/owner `mmorinag`. Standard Alembic observed all 34 revisions and passed the current generated contract postflight. The existing `table_shapes` comparator verified all 68 new observations equal their prior observations and the current contract, without additional normalization.

Actual reproducible driver: `/Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python tmp/hospital-v1-catalog-20261007/run-role-proof.py`. Its subprocess environment is an explicit allowlist with owned HOME/TMP/cache. Exact subprocess argv, UTC start/end, exit codes, log paths and log hashes are recorded in `tmp/hospital-v1-catalog-20261007/role-proof.json` (SHA256 `1449f0f92a3abb1f4d6c5c8815db45a055e1468e2593f61bc66427abbbf492e0`). Base remains `1654ffa2ebb8f762dd473516a9e02d91a3bb417f`, with the dirty source hashes recorded by that driver.

Focused selection covers role/owner/database mismatch, every revision resume, missing/changed column/index/constraint and unknown/versionless catalog stops. PG15: 117 passed, 125 deselected, exit 0, 53.62s; PG16: 117 passed, 125 deselected, exit 0, 54.75s. Logs: `role-pg15-tests.log` SHA256 `a669c24d469715b13990c842ce16dcff1ad8d6bd4fb2df358981bd9f9af5b81c`; `role-pg16-tests.log` SHA256 `5e26f13045b0076ae3a52211a2e4633f4d1f97c3380df405dbad8897db01f29f`.

Verification driver: `env -i PATH=/usr/bin:/bin HOME=<owned-root>/home PYTHONPATH=backend /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python tmp/hospital-v1-catalog-20261007/verify-role-observations.py`, exit 0. Per-artifact SHA256 manifest: `role-observation-verification.json`, SHA256 `5ec14dbd8aea5ddf4315238e9538559c49eddda6845a0ae3bebef2c1741533ac`.

Current observer SHA256 `15d9157d8e7f34f6b5e1381b2cacb77a1bf5c33ed9b90da369bef55f00e0aedc`; focused test SHA256 `c0099e05b744927a5ffbabab222653aa3846be6dafcb4147cdc7f9ff4fc1c53b`. Other source hashes above remain unchanged. Both owned servers stopped with exit 0; both subsequent status commands exited 3 (no server). No needed command session remains running.

Correction to prior reporting: the earlier failed run actually reported **9 failed / 106 passed / 108 errors**, not 106 errors. Its tool output remains in chat history, but no independent initial log file was saved; a retained-log claim is unsupported. Earlier truncated rerun outputs also did not establish complete-suite exit codes. Only the explicitly logged focused runs above are claimed successful here.
