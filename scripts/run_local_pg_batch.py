#!/usr/bin/env python3
"""Run the existing PG suites serially under one exclusively owned PG16 server."""
import os

from local_hospital_test_runner import ROOT, focused_staging, reject_hosted_runner
from run_readonly_pg_tests import run


def main():
    reject_hosted_runner()
    suites = ['readonly', 'c1', 'c1-guards', 'menu-identity', 'output']
    if not focused_staging():
        suites.append('formal')
    return run(ROOT, os.environ['SAWA_LOCAL_RUN_ID'], '1', 'batch', suites=suites)


if __name__ == '__main__':
    raise SystemExit(main())
