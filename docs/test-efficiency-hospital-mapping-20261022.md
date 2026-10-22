# Hospital Test Efficiency Mapping

Scope: `backend/tests/integration/test_delivery_admission_hardening.py` (602
cases) and `test_portal_admin_write_authority.py` (84 cases). The SQLite
fixture now creates the fully migrated, empty formal schema once per test
session and copies it for every case. Each case still has a distinct database
file and runs its original setup, HTTP calls, transactions, assertions, and
cleanup. Copy destinations must be new, and every SQLite fixture verifies the
template hash did not change. PostgreSQL remains the existing CI-only isolated
database fixture.

| Delivery-admission guarantee | Cases | Boundary | Fixture treatment |
| --- | ---: | --- | --- |
| Recovery ownership, successor, rollback, stale-owner/revision, terminal, original preservation | 106 | SQLite and PostgreSQL DB/service boundary | Per-case copied SQLite schema; PG remains per-case isolated database |
| Native capture/admission and output backoff preservation | 92 | SQLite and PostgreSQL DB/workflow boundary | Per-case copied SQLite schema; PG remains per-case isolated database |
| HTTP authorization, identity ownership, limit guard, missing config, dispatch boundary | 97 | HTTP/auth boundary before DB/dispatch | No database fixture is used |
| Google signature/certificate verification, malformed headers, certificate faults | 307 | HTTP/certificate boundary | Module signing material; no database fixture is used |
| **Delivery total** | **602** |  |  |

| Portal authority guarantee | Cases | Boundary | Fixture treatment |
| --- | ---: | --- | --- |
| Cached/current actor authority, committed retirement, concurrent exclusion, audit and rollback | 24 | SQLite HTTP/transaction boundary | Per-case copied SQLite schema |
| PostgreSQL actor locking, concurrent demotion, ownership and cleanup | 11 | PostgreSQL-only isolated database | Existing CI-only per-case database creation remains unchanged |
| CI profile validation and invalid-profile stop behavior | 19 | Configuration/connection boundary | No PostgreSQL connection for rejected profiles |
| PG ownership receipt, OID/owner validation, cleanup failure handling | 30 | Unit ownership/rollback boundary | Unit doubles only; not PostgreSQL proof |
| **Portal total** | **84** |  |  |

Assertions retained: authentication signatures and certificate outcomes;
concurrent exclusion; transaction rollback and audit state; actor ownership;
SQLite versus PostgreSQL boundary assertions; and original-record preservation.
Local PostgreSQL skips remain unverified, not passes.

## Delivery Nodeid Accounting

The four delivery rows are disjoint. In particular, the 84
`test_actual_native_capture_input_blocker_and_admission_agree` nodeids belong
only to the native row, not the recovery row.

| Boundary | Nodeid base and cases | Total |
| --- | --- | ---: |
| DB recovery | `test_service_delivery_admission_matches_actual_recovery_owner` 36; `test_multiple_service_delivery_rejections_advance_same_time_pages` 4; `test_real_recovery_loop_skips_unacknowledged_delivery` 2; `test_service_authored_successor_admission` 16; `test_successor_parent_insert_and_receipt_are_one_rollback` 4; `test_bad_source_and_nonrecoverable_pages_do_not_consume_limit` 4; `test_all_invalid_is_finite_and_retains_warning_and_original` 2; `test_terminal_jobs_never_displace_live_capture` 6; `test_formal_schema_refuses_unknown_timestamp_without_date_guess` 2; `test_uncaptured_or_mismatched_owner_does_not_displace_good_job` 14; `test_stale_ingest_owner_excluded_and_write_cas_stays_strict` 2; `test_stale_or_invalid_workflow_revision_is_not_admitted` 10; `test_recorded_workflow_pending_receipt_remains_admissible` 2; `test_real_recovery_loop_preserves_missing_source_and_visits_valid_job` 2 | 106 |
| Native DB | `test_actual_native_capture_input_blocker_and_admission_agree` 84; `test_actual_native_invalid_capture_pages_finish_without_mutating_original` 4; `test_actual_backoff_without_pollable_output_does_not_take_admission` 4 | 92 |
| HTTP non-certificate | `test_reader_invalid_limit_stops_before_session` 6; `test_actual_http_verification_classifies_failures_without_auth_override` 40; `test_foreign_identity_remains_403` 25; `test_ingest_limit_guard_after_auth_before_any_database_or_dispatch` 8; `test_valid_limit_does_not_mask_dispatch_failure_as_422` 2; `test_malformed_verifier_result_is_unavailable` 4; `test_missing_bearer_still_401_with_configured_authority` 5; `test_missing_config_stops_503_before_verification` 5; `test_valid_principal_reaches_only_its_original_business_boundary` 2 | 97 |
| HTTP certificate | `test_actual_google_verifier_with_isolated_certificate_http` 180; `test_certificate_boundary_returns_exact_original_response_once` 2; `test_real_malformed_header_types_are_401` 40; `test_unknown_type_error_is_503_with_well_typed_header` 45; `test_actual_valid_token_preserves_missing_and_valid_key_id` 15; `test_certificate_fault_remains_503_with_malformed_header` 20; `test_unknown_verifier_type_error_without_decodable_header_remains_503` 5 | 307 |
| **Total** |  | **602** |

## Portal Nodeid Accounting

| Boundary | Nodeid base and cases | Total |
| --- | --- | ---: |
| SQLite | `test_cached_admin_cannot_write_after_committed_retirement` 4; `test_current_admin_preserves_response_access_and_audit` 2; `test_actor_must_be_unique_and_present` 6; `test_authority_read_failure_is_503_and_no_writes` 2; `test_self_demotion_succeeds_then_same_cached_context_is_denied` 1; `test_same_cached_context_cannot_bypass_shared_save` 4; `test_explicit_disabled_auth_keeps_existing_behavior` 1; `test_real_concurrent_retirement_commits_before_actor_write` 2; `test_two_admins_cross_demotion_is_serialized` 1; `test_busy_reservation_stops_409_without_writes` 1 | 24 |
| PostgreSQL | `test_pg_cached_admin_after_independent_committed_retirement` 4; `test_pg_current_admin_response_access_and_audit` 2; `test_pg_retirement_row_lock_nowait_then_same_cached_actor_denied` 2; `test_pg_two_admins_cross_demotion_serializes_real_connections` 1; `test_pg_unknown_or_ambiguous_actor_stops_shared_save` 2 | 11 |
| Profile validation | `test_pg_profile_validator_absent_local_is_unverified_not_proof` 1; `test_pg_profile_validator_synthetic_metadata_only_never_connects` 1; `test_pg_profile_validator_rejects_invalid_metadata_before_connect` 15; `test_pg_fixture_invalid_provided_local_profile_stops_before_engine` 2 | 19 |
| Ownership unit | `test_unit_unverified_creation_receipt_denies_before_cleanup_connect` 17; `test_unit_current_database_oid_owner_mismatch_denies_before_drop` 5; `test_unit_matching_recorded_oid_owner_binding_allows_only_exact_drop` 1; `test_unit_creation_oid_capture_failure_never_adopts_cleanup_lookup` 1; `test_unit_invalid_creation_oid_is_not_recorded_or_recovered` 4; `test_unit_memory_oid_captured_before_artifact_failure_can_cleanup_but_error_remains` 1; `test_unit_uncaptured_oid_and_broken_artifacts_log_failure_without_drop` 1 | 30 |
| **Total** |  | **84** |

## Measurement Evidence

Python used for all successful test measurements:

```text
/Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python
```

The initially suggested candidate,
`/private/tmp/sawa-ca-parent-py311-20261022/bin/python`, was checked but lacks
`cryptography` and `fastapi`; it was not used for measurements.

All commands ran from
`/Users/mmorinag/Sawa/2025.12/worktrees/test-efficiency-hospital-20261022/backend`
with `NO_GCE_CHECK=true`, `GOOGLE_CLOUD_DISABLE_GRPC=true`, and the explicit
`PYTHONPATH=.:tests/unit:tests/integration`.

| Run | Exact pytest arguments after environment setup | Result | Wall time | Log | JUnit |
| --- | --- | --- | ---: | --- | --- |
| Baseline | `-m pytest tests/integration/test_delivery_admission_hardening.py tests/integration/test_portal_admin_write_authority.py -q -p no:cacheprovider --basetemp="$RUN/cases" --junitxml="$RUN/results.xml"` | 576 passed, 110 skipped | 68s | `/private/tmp/sawa-test-efficiency-hospital-20261022-baseline-run/pytest.log` | `/private/tmp/sawa-test-efficiency-hospital-20261022-baseline-run/results.xml` |
| After | `-m pytest tests/integration/test_delivery_admission_hardening.py tests/integration/test_portal_admin_write_authority.py -q -p no:cacheprovider --basetemp="$RUN/cases" --junitxml="$RUN/results.xml"` | 576 passed, 110 skipped | 41s | `/private/tmp/sawa-test-efficiency-hospital-20261022-after-run-5/pytest.log` | `/private/tmp/sawa-test-efficiency-hospital-20261022-after-run-5/results.xml` |
| Related combined | `-m pytest tests/integration/test_delivery_admission_hardening.py tests/integration/test_portal_admin_write_authority.py tests/integration/test_current_actor_authority.py -q -p no:cacheprovider --basetemp="$RUN/cases" --junitxml="$RUN/results.xml"` | 659 passed, 154 skipped | 50s | `/private/tmp/sawa-test-efficiency-hospital-20261022-combined-run/pytest.log` | `/private/tmp/sawa-test-efficiency-hospital-20261022-combined-run/results.xml` |

For each row, the environment setup was:

```text
RUN=/private/tmp/sawa-test-efficiency-hospital-20261022-<run>
DB_URI="sqlite:///$RUN/bootstrap.sqlite3" \
PYTHONPATH=.:tests/unit:tests/integration \
NO_GCE_CHECK=true GOOGLE_CLOUD_DISABLE_GRPC=true \
/Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python <pytest arguments above>
```

The `summary.txt` files adjacent to each log record the process exit code and
wall time. JUnit comparison confirms both baseline and after have 686 tests,
zero failures, zero errors, and 110 skips.

`formal_sqlite_template` is an untracked new integration `conftest.py` fixture;
`sqlite_fixture_template.py` is its untracked new stateless helper. The 686-case
after run and the 659-case related combined run exercise normal migration,
engine disposal, per-case copied databases, original assertions, and template
hash immutability. A failure-injection probe additionally exercised helper
exception cleanup, environment restoration, copy isolation, and refusal to
overwrite an existing destination:

```text
PROBE_ROOT=/private/tmp/sawa-test-efficiency-hospital-20261022-template-probe-run \
PYTHONPATH=tests/integration \
/Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python \
/private/tmp/sawa-test-efficiency-hospital-20261022-template-probe.py
```

Probe source: `/private/tmp/sawa-test-efficiency-hospital-20261022-template-probe.py`.
Probe log: `/private/tmp/sawa-test-efficiency-hospital-20261022-template-probe.log`.

## Accounting Correction

Parent verification on the final fixture implementation:
`576 passed, 110 skipped` in `37.62s`; JUnit:
`/private/tmp/sawa-hospital-efficiency-parent-20261022.xml`.
The three helper failure/isolation checks are now persistent pytest cases in
`backend/tests/integration/test_sqlite_fixture_template.py`, included in the
existing US1-4 CI job. Parent ran all three: `3 passed` in `0.01s`.
No extra workflow or job was added.

An initial draft grouped the delivery rows as 130 DB, 143 HTTP, 311
certificate, and 18 native. That grouping was not retained: nodeid recount
showed it double-counted the native boundary and misclassified HTTP and
certificate cases. The tables above are the final authoritative accounting:
106 DB recovery, 92 native DB, 97 HTTP non-certificate, and 307 certificate,
for exactly 602 disjoint nodeids.
