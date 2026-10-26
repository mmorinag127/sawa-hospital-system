"""Connection reuse cannot retain transaction settings or leak capacity."""
from types import SimpleNamespace
import threading

import pytest

from experiments.agent_assistant.durable import _ConnectionLease, _PooledConnections


@pytest.mark.parametrize("closed,broken", [(False, False), (False, True), (True, False)])
def test_lease_resets_or_discards_and_releases_exactly_once(closed, broken):
    returned, resets = [], []
    def reset():
        resets.append(True)
        if broken:
            raise RuntimeError("broken reset")
    connection = SimpleNamespace(closed=closed, reset=reset)
    slots = threading.BoundedSemaphore(1)
    slots.acquire()
    owner = SimpleNamespace(slots=slots, pool=SimpleNamespace(
        putconn=lambda connection, close: returned.append((connection, close))))
    lease = _ConnectionLease(connection, owner)
    if broken:
        with pytest.raises(RuntimeError, match="broken reset"):
            lease.close()
    else:
        lease.close()
    lease.close()
    assert resets == ([] if closed else [True])
    assert returned == [(connection, closed or broken)]
    assert slots.acquire(blocking=False)
    assert not slots.acquire(blocking=False)


def test_getconn_failure_releases_reserved_slot():
    def fail():
        raise RuntimeError("connection failed")
    owner = SimpleNamespace(slots=threading.BoundedSemaphore(1), pool=SimpleNamespace(getconn=fail))
    with pytest.raises(RuntimeError, match="connection failed"):
        _PooledConnections.__call__(owner)
    assert owner.slots.acquire(blocking=False)
