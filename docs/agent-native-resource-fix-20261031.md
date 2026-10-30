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
| Regression fixtures and lifecycle/settings coverage | Updated; not run |
| Serial Linux batch, exact-source receipt, develop integration, manual staging deployment | Pending |
| School daily/month exports/procurement/import, saved reload and actual artifact comparison | Pending |

No CPU, memory, minimum instances, IAM, production or budget changes. No command ledger changes or unknown mutation replay. The old failed run is retained, not counted as a pass. Synthetic source inputs must contain the previously authored three cooking steps before a fresh daily case; legacy process_steps are not the canonical process recipe.
