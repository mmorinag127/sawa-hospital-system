#!/usr/bin/env python3
import json
import sys


def main() -> int:
    raw = sys.argv[1]
    strict_quality = sys.argv[2] == "1"
    allow_basic_only_auth = sys.argv[3] == "1"
    try:
        data = json.loads(raw)
    except Exception:
        print("[FAIL] system_status JSON parse failed")
        return 1

    oauth = data.get("oauth_config", {})
    intake = data.get("intake", {})
    intake_mode = str(intake.get("mode") or "").strip().lower()
    if intake_mode != "manual_upload":
        print(f"[FAIL] intake.mode is invalid: {intake_mode or 'missing'}")
        return 1
    if not intake.get("manual_upload_enabled"):
        print("[FAIL] intake.manual_upload_enabled is false")
        return 1
    upload_storage = intake.get("manual_upload_storage") or {}
    if not upload_storage.get("configured"):
        print("[FAIL] manual_upload_storage.configured is false")
        return 1
    if not oauth.get("configured") and not allow_basic_only_auth:
        print("[FAIL] oauth_config.configured is false")
        return 1
    if not oauth.get("configured") and allow_basic_only_auth:
        print("[WARN] oauth_config.configured is false (basic-auth staging mode allowed)")
    print(f"[OK]   intake mode: {intake_mode or 'manual_upload'}")

    quality = data.get("ocr_reparse_quality")
    gate_status = ""
    scope_mode = ""
    included_jobs = 0
    if isinstance(quality, dict):
        gate = quality.get("gate")
        if isinstance(gate, dict):
            gate_status = str(gate.get("status") or "").strip().lower()
        scope = quality.get("scope")
        if isinstance(scope, dict):
            scope_mode = str(scope.get("mode") or "").strip().lower()
            try:
                included_jobs = int(scope.get("included_jobs") or 0)
            except Exception:
                included_jobs = 0

    if strict_quality:
        allow_insufficient_data = gate_status == "insufficient_data" and included_jobs == 0
        if gate_status not in {"pass"} and not allow_insufficient_data:
            fail_detail = ""
            if isinstance(quality, dict):
                gate = quality.get("gate")
                if isinstance(gate, dict):
                    fail_detail = (
                        f" fail_providers={gate.get('fail_providers')}"
                        f" warming_up={gate.get('warming_up_providers')}"
                        f" scope_mode={scope_mode or 'missing'} included_jobs={included_jobs}"
                    )
            print(f"[FAIL] ocr_reparse_quality.gate.status is {gate_status or 'missing'}{fail_detail}")
            return 1
        if allow_insufficient_data:
            print(
                "[WARN] ocr_reparse_quality.gate.status is insufficient_data "
                f"(no included jobs; scope_mode={scope_mode or 'missing'})"
            )
    else:
        if gate_status and gate_status != "pass":
            print(f"[WARN] ocr_reparse_quality.gate.status is {gate_status} (non-blocking)")
        if not gate_status:
            print("[WARN] ocr_reparse_quality.gate.status is missing (non-blocking)")
    print("[OK]   system_status checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
