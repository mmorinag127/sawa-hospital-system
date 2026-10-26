#!/usr/bin/env python3
"""Run both full PG modules in independently owned local PG16 clusters."""
import os
import traceback
import json

from local_hospital_test_runner import ROOT, focused_staging, reject_hosted_runner
from run_readonly_pg_tests import run


def main():
    reject_hosted_runner()
    failed = False
    suites = ('output',) if focused_staging() else ('output', 'formal')
    print(json.dumps({'pg_output_suites': suites,
                      'outside_scope': 'Historical revision resume matrix not run in focused staging' if len(suites) == 1 else None}), flush=True)
    for suite in suites:
        try:
            failed = bool(run(ROOT, os.environ["SAWA_LOCAL_RUN_ID"], "1", suite)) or failed
        except Exception:
            traceback.print_exc()
            failed = True
    return int(failed)

if __name__ == '__main__':
    raise SystemExit(main())
