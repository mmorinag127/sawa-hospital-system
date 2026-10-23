#!/usr/bin/env python3
"""Run all preserved adoption cases and retain owned PostgreSQL lifecycle logs."""
from local_hospital_legacy_step import execute

if __name__ == '__main__':
    execute('live-schema-adoption-test.yml', 'live-schema-adoption',
            ['Generate fresh PostgreSQL 0035 contract and run adoption tests'])
