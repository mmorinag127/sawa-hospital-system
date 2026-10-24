#!/usr/bin/env python3
"""Run both full PG modules in independently owned local PG16 clusters."""
import os
import traceback

from local_hospital_test_runner import ROOT, reject_hosted_runner
from run_readonly_pg_tests import run


def main():
    reject_hosted_runner()
    failed = False
    for suite in ("output", "formal"):
        try:
            failed = bool(run(ROOT, os.environ["SAWA_LOCAL_RUN_ID"], "1", suite)) or failed
        except Exception:
            traceback.print_exc()
            failed = True
    return int(failed)

if __name__ == '__main__':
    raise SystemExit(main())
