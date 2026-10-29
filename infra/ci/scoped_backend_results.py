"""Record actual scoped outcomes without process-wide input instrumentation."""
import json
import os
from pathlib import Path

import pytest

selected = []
started = []
outcomes = {}


def pytest_collection_finish(session):
    selected.extend(item.nodeid for item in session.items)


def pytest_runtest_logstart(nodeid, location):
    started.append(nodeid)


def pytest_runtest_logreport(report):
    outcomes.setdefault(report.nodeid, {})[report.when] = report.outcome


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    completed = [node for node in started if 'teardown' in outcomes.get(node, {})
                 and ('call' in outcomes[node] or outcomes[node].get('setup') in ('skipped', 'failed'))]
    passed = [node for node in completed if all(outcomes[node].get(phase) == 'passed'
                                              for phase in ('setup', 'call', 'teardown'))]
    failed = [node for node in started if 'failed' in outcomes.get(node, {}).values()]
    completed_set, started_set = set(completed), set(started)
    report = {'source_sha': os.environ['SAWA_LOCAL_SOURCE_SHA'], 'selected': selected,
              'reused': [], 'executed': started, 'completed': completed, 'passed': passed,
              'failed': failed, 'skipped': [node for node in completed if 'skipped' in outcomes[node].values()],
              'incomplete': [node for node in started if node not in completed_set],
              'not_started': [node for node in selected if node not in started_set],
              'exitstatus': int(exitstatus), 'baseline': None, 'full': False,
              'normal_completion': int(exitstatus) in (0, 1) and len(completed) == len(selected),
              'scope': 'reviewed-staging-behaviors'}
    Path(os.environ['SAWA_SCOPED_BACKEND_REPORT']).write_text(json.dumps(report, indent=2) + '\n')
