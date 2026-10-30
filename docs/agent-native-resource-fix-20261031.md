# Agent Native Execution Follow-up

## Scope and Evidence

- Eight of twelve public business cases have known-answer live proof. The four school cases remain incomplete.
- Daily run `0e4a3a2d754540aab8ffed305bd888e2` failed on deployed `d0a2193c8ab4`. Its infer command is unknown, not safe to replay.
- Cloud Run reported 8791 MiB against the combined 8704 MiB limit at 2026-10-30T15:24:07Z.
- The server-owned completion paths did not stop native processes, unlike Gemini-owned completion. Each native start copied the fixed OCR cache, including non-OCR tasks.
- The agent sidecar omitted the school API's `PROCESS_GEMINI_MODEL`. Native inference requires that setting; the authoritative staging school API uses `gemini-2.5-flash`.

## Implementation

| Item | Status |
| --- | --- |
| Stop owned native children and reclaim disposable model copies on all server completion branches before releasing reservations | Implemented; unverified |
| Reclaim completed resources before starting another request, preserving generation and process ownership guards | Implemented; unverified |
| Copy fixed OCR models only for OCR-capable tasks; preserve weights for import and FAX recovery | Implemented; unverified |
| Forward the existing school API's public runtime settings through the canonical sidecar configuration and fail closed when model is missing | Implemented; unverified |
| Regression fixtures and lifecycle/settings coverage | Focused 44 passed on Mac, 3.26s |
| Serial Linux batch, exact-source receipt, develop integration, manual staging deployment | Pending |
| School daily/month exports/procurement/import, saved reload and actual artifact comparison | Pending |

No CPU, memory, minimum instances, IAM, production or budget changes. No command ledger changes or unknown mutation replay. The old failed run is retained, not counted as a pass. Synthetic source inputs must contain the previously authored three cooking steps before a fresh daily case; legacy process_steps are not the canonical process recipe.

## Verification Boundary

- The broader Mac batch produced 491 passed, 35 skipped and one environment failure (`src` unavailable to the packaged migration test). JUnit: `/private/tmp/sawa-agent-27fe80bb-mac.xml`; log: `/private/tmp/sawa-agent-27fe80bb-mac.log`. This is not a successful formal receipt or Linux proof.
- Docker launch was rejected because macOS was locked again. Docker CLI could not reach its daemon; no Docker backend/VM remains running. Unlock was requested. No formal batch or deployment was launched under this condition.
- The failed daily request is stopped through the ordinary administrator UI, with its unknown infer command and business history preserved.
- Dedicated menu 357, schedule 80 and route 81 now have version 3. The same three preauthored steps are supplied to canonical recipe inputs; expected quantities/outputs are unchanged. The next attempt must use a fresh request copy.
