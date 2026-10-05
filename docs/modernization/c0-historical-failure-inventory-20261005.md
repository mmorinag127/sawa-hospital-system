# Historical C0 Failure Inventory

Source XML: `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-main/tmp/c0-full-parent-239760d/results.xml`, source commit `239760dc7066c28936d28546d08f52cfac1fd4be`, SHA256 `fb015e128d0c783216e2e7e6df8e86d15bf3e344e33992e1c5ee596070dd2107`.

Method: Python standard-library `xml.etree.ElementTree` parsed every `testcase`; each `failure` or `error` was counted. The parser found exactly 391 failures. Clusters below are deterministic text/test-class classification over all 391 records, not a sample; classifications other than direct 410 and SQLite evidence remain uncertain.

| Cluster | Count | Classification | Evidence |
| --- | ---: | --- | --- |
| Observed 410 assertion mismatch | 87 | Applicability not individually traced | XML contains `assert 410 == 200/202/409/400/404`. |
| OCR/current-sheet provenance expectation | 69 | Suspected product invariant / unresolved | `review_blocked`, `weekly_menu+ocr_payload`, `ocr_evidence`, and `draft_blocked` source-state assertions. |
| Template/schema expectation | 38 | Provisional environment/fixture or unresolved | Field/quantity/template shape mismatches including regular-X versus regular-2F; no source-level conclusion. |
| Observed SQLite lock | 15 | Historical lock symptom; cause unverified | `sqlalchemy.exc.OperationalError: database is locked`. |
| Hakodate fixture/assignment | 14 | Provisional environment/fixture or unresolved | Assignment/PDF/structure test failures; no source-level conclusion. |
| Other assertion/fixture contract | 168 | Unresolved | Remaining exact assertion differences; no source-level conclusion assigned. |
| **Total** | **391** |  |  |

## Complete Record Artifact

Every one of the 391 XML failure/error records is represented exactly once in [/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-baseline-provenance/tmp/c0-historical-inventory/inventory.json](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-baseline-provenance/tmp/c0-historical-inventory/inventory.json): each record has its full testcase node, one exact category assignment, and a concise failure/error excerpt. Artifact SHA256: `f3a92e1637210054156cd3c73080d7214aa7bca88fec682b1712a32c8cb0f74d`.

Deterministic parser/rules: [/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-baseline-provenance/tmp/c0-historical-inventory/classify.py](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-baseline-provenance/tmp/c0-historical-inventory/classify.py), SHA256 `972090a69ea631aff0e68cdf7822d32e0bc981a5c9d99c61a35bfc8e233486b7`. It parses every `testcase`, selects one `failure` or `error`, assigns categories in declared first-match order, sorts by category/node, and writes count `391`. The artifact count map sums `391`: observed 410 mismatch `87`, provisional OCR/current-sheet provenance `69`, provisional template/schema `38`, observed SQLite lock `15`, provisional Hakodate fixture/assignment `14`, provisional other assertion/fixture `168`.

The 87 category is only observed `410` assertion mismatch; current retired-route applicability was not individually traced. The 15 SQLite records are observed historical lock symptoms, not evidence that the application is safe. All remaining categories are provisional. No current failure total is claimed. This historical XML does not establish current product failures. High-risk current-sheet provenance policy remains pending user approval; no root fix is proposed or implemented.
