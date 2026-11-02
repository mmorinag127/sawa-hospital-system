# Authentication And Quality Reads

Baseline: `7dd91287979a16b34f5861f23ed82c0deb4f5367`.

- Human session authentication passes its existing read Session into the complete active-account/role/system-access lookup instead of holding a second connection. Expiry, revocation, CSRF and conditional last-seen update remain unchanged.
- PostgreSQL quality aggregation projects selected evidence JSON keys through one json_each scan. Keep presence of explicit null audit keys, non-object behavior and evidence/progress ownership. SQLite path remains unchanged.
- Adopt the exact School route optimization in the retained-extension native bundle through the existing synchronization/sealing mechanism.

Real local PostgreSQL 16 regression: 67 passed, 0.79s (pytest time, excluding interpreter/migration startup).
Same 100 jobs with 200KB audit bodies each: 0.059724s -> 0.011240s; complete business result equal.
The synthetic workload measures the query transformation, not a live service speedup or all future data sizes.

Exact commands/logs/comparison scripts:
`/Users/mmorinag/Sawa/2025.12/docs/reviews/worktree-cleanup-20261102/` and
`/private/tmp/sawa-remaining-20261102/`.
No Docker, paid AI, Actions, deployment, production or billing changes.
