"""Offline diagnostic invariants; no model download, database or OCR execution."""
import importlib
import io
import json
import logging
import sys
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from src.services import recognizer_diagnostics as diagnostic
from src.services.yomitoku_text_recognizer_topk import YomitokuTextRecognizerTopKWrapper
from test_yomitoku_text_recognizer_topk import _FakeRecognizerTwoValuePreprocess


def events(caplog):
    return [json.loads(record.getMessage().split(' ', 1)[1]) for record in caplog.records
            if record.name == diagnostic.__name__]


@pytest.fixture(autouse=True)
def info_logs(caplog):
    caplog.set_level(logging.INFO, logger=diagnostic.__name__)
    diagnostic.logger.addHandler(caplog.handler)
    try:
        yield
    finally:
        diagnostic.logger.removeHandler(caplog.handler)


def test_exact_return_exception_and_timer_cleanup(monkeypatch, caplog):
    timers = []
    class Timer:
        def __init__(self, delay, callback):
            assert delay == 60
            self.callback = callback
            self.cancelled = False
            timers.append(self)
        def start(self):
            assert self.daemon is True
        def cancel(self):
            self.cancelled = True
    monkeypatch.setattr(diagnostic.threading, 'Timer', Timer)
    original = object()
    error = ValueError('SECRET_EXCEPTION')
    @diagnostic.diagnosed('initialize')
    def run(fail):
        if fail:
            raise error
        return original
    assert run(False) is original
    with pytest.raises(ValueError) as caught:
        run(True)
    assert caught.value is error
    assert all(timer.cancelled for timer in timers)
    before = len(events(caplog))
    for timer in timers:
        timer.callback()
    assert len(events(caplog)) == before
    assert diagnostic._current.get() is None
    assert 'SECRET_EXCEPTION' not in caplog.text
    assert [event['outcome'] for event in events(caplog) if event['event'] == 'end'] == ['return', 'error']


def test_delayed_stack_is_target_thread_only_and_bounded(monkeypatch, caplog):
    monkeypatch.setattr(diagnostic, 'STACK_DELAY_SECONDS', 0.01)
    observed = threading.Event()
    original_emit = diagnostic._Observation.emit
    def emit(self, event, **kwargs):
        original_emit(self, event, **kwargs)
        if event == 'waiting_stack':
            observed.set()
    monkeypatch.setattr(diagnostic._Observation, 'emit', emit)
    target_id = []
    @diagnostic.diagnosed('recognize')
    def target():
        secret = 'SECRET_LOCAL_VALUE'
        target_id.append(threading.get_ident())
        with diagnostic.phase('inference'):
            assert observed.wait(3), secret
    thread = threading.Thread(target=target)
    thread.start()
    thread.join(4)
    assert not thread.is_alive()
    stacks = [event for event in events(caplog) if event['event'] == 'waiting_stack']
    assert len(stacks) == 1
    assert stacks[0]['thread'] == target_id[0]
    assert stacks[0]['phase'] == 'inference'
    assert 0 < len(stacks[0]['frames']) <= diagnostic.MAX_STACK_FRAMES
    assert any(frame['function'] == 'target' for frame in stacks[0]['frames'])
    assert all(set(frame) == {'filename', 'function', 'line'} for frame in stacks[0]['frames'])
    assert 'SECRET_LOCAL_VALUE' not in caplog.text


def test_phase_log_budget_and_current_phase_survive_limit(monkeypatch, caplog):
    @diagnostic.diagnosed('recognize')
    def run():
        for _ in range(100):
            with diagnostic.phase('postprocess'):
                pass
        with diagnostic.phase('candidates'):
            diagnostic._current.get().snapshot()
    run()
    records = events(caplog)
    assert len(records) == 2 + 2 * diagnostic.MAX_PHASE_PAIRS + 1
    assert next(event for event in records if event['event'] == 'waiting_stack')['phase'] == 'candidates'


def test_dedicated_handler_emits_with_root_warning(monkeypatch):
    stream = io.StringIO()
    handler = next(handler for handler in diagnostic.logger.handlers
                   if type(handler) is logging.StreamHandler)
    monkeypatch.setattr(handler, 'stream', stream)
    monkeypatch.setattr(logging.getLogger(), 'level', logging.WARNING)
    diagnostic.diagnosed('initialize')(lambda: None)()
    records = [json.loads(line.split(' ', 1)[1]) for line in stream.getvalue().splitlines()]
    assert [record['event'] for record in records] == ['begin', 'end']
    assert logging.getLogger().level == logging.WARNING
    assert diagnostic.logger.propagate is False


def test_periodic_observation_catches_later_phase_with_one_timer(monkeypatch, caplog):
    monkeypatch.setattr(diagnostic, 'STACK_DELAY_SECONDS', 0.01)
    first = threading.Event()
    third = threading.Event()
    timers = []
    original_timer = threading.Timer
    def timer(*args, **kwargs):
        result = original_timer(*args, **kwargs)
        timers.append(result)
        return result
    monkeypatch.setattr(diagnostic.threading, 'Timer', timer)
    original_emit = diagnostic._Observation.emit
    snapshots = []
    def emit(self, event, **kwargs):
        original_emit(self, event, **kwargs)
        if event == 'waiting_stack':
            snapshots.append(self.phase)
            first.set()
            if len(snapshots) == 3:
                third.set()
    monkeypatch.setattr(diagnostic._Observation, 'emit', emit)
    @diagnostic.diagnosed('recognize')
    def run():
        with diagnostic.phase('preprocess'):
            assert first.wait(3)
        with diagnostic.phase('inference'):
            assert third.wait(3)
    run()
    for pending in timers:
        pending.join(1)
        assert not pending.is_alive()
    assert len(timers) == 1
    assert snapshots == ['preprocess', 'inference', 'inference']


@pytest.mark.parametrize('fail', [False, True])
def test_real_timer_cancelled_before_first_snapshot(monkeypatch, caplog, fail):
    timers = []
    original_timer = threading.Timer
    def timer(*args, **kwargs):
        result = original_timer(*args, **kwargs)
        timers.append(result)
        return result
    monkeypatch.setattr(diagnostic.threading, 'Timer', timer)
    @diagnostic.diagnosed('initialize')
    def run():
        if fail:
            raise ValueError('secret')
    if fail:
        with pytest.raises(ValueError):
            run()
    else:
        run()
    for pending in timers:
        pending.join(1)
        assert not pending.is_alive()
    assert not any(record['event'] == 'waiting_stack' for record in events(caplog))


def test_metadata_read_only_and_logging_failure_does_not_replace_result(monkeypatch):
    calls = []
    monkeypatch.setattr(diagnostic, 'version', lambda name: calls.append(name) or '0.9.5')
    monkeypatch.setattr(diagnostic.sys, 'modules', {**sys.modules, 'torch': SimpleNamespace(
        get_num_threads=lambda: 2, get_num_interop_threads=lambda: 4)})
    assert diagnostic._metadata() == {'yomitoku': '0.9.5', 'torch': '0.9.5',
        'huggingface-hub': '0.9.5', 'get_num_threads': 2, 'get_num_interop_threads': 4}
    def fail(*args, **kwargs):
        raise RuntimeError('SECRET_LOG_ERROR')
    monkeypatch.setattr(diagnostic.logger, 'info', fail)
    result = object()
    assert diagnostic.diagnosed('initialize')(lambda: result)() is result


@pytest.mark.parametrize('module', ['run_text_recognizer_trial', 'run_text_recognizer_corner_noise_trial'])
def test_both_loaders_preserve_arguments_and_return(module, monkeypatch, caplog):
    runtime = importlib.import_module('src.hakodate_best_method_runtime.' + module)
    calls = []
    recognizer = object()
    def constructor(**kwargs):
        calls.append(kwargs)
        return recognizer
    if module == 'run_text_recognizer_corner_noise_trial':
        from src.services import hakodate_empty_sequence_model as adapter
        monkeypatch.setattr(adapter, 'load_empty_sequence_recognizer',
                            lambda device: constructor(device=device, adapter='initial_eos'))
        expected = {'device': 'cpu', 'adapter': 'initial_eos'}
    else:
        monkeypatch.setitem(sys.modules, 'yomitoku', SimpleNamespace(TextRecognizer=constructor))
        expected = {'device': 'cpu', 'visualize': False}
    assert runtime._load_text_recognizer('cpu') is recognizer
    assert calls == [expected]
    assert [event['event'] for event in events(caplog)] == ['begin', 'end']


def test_wrapper_result_parity_and_all_boundaries(caplog):
    wrapper = YomitokuTextRecognizerTopKWrapper(_FakeRecognizerTwoValuePreprocess())
    image = np.zeros((20, 20, 3), dtype=np.uint8)
    points = [[[0, 0], [10, 0], [10, 10], [0, 10]]]
    expected = wrapper.recognize.__wrapped__(wrapper, image, points=points)
    actual = wrapper.recognize(image, points=points)
    assert actual == expected
    phases = [event['phase'] for event in events(caplog) if event['event'] == 'phase_begin']
    assert phases == ['preprocess', 'inference', 'postprocess', 'candidates']
    assert all('contents' not in event and 'scores' not in event for event in events(caplog))


@pytest.mark.parametrize('boundary', ['preprocess', '_run_inference', 'postprocess', '_sequence_candidates'])
def test_boundary_exception_identity_and_no_value_logging(boundary, monkeypatch, caplog):
    recognizer = _FakeRecognizerTwoValuePreprocess()
    wrapper = YomitokuTextRecognizerTopKWrapper(recognizer)
    error = RuntimeError('SECRET_RECOGNIZED_VALUE')
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(wrapper if boundary == '_sequence_candidates' else recognizer, boundary, fail)
    with pytest.raises(RuntimeError) as caught:
        wrapper.recognize(np.zeros((20, 20, 3)), points=[[[0, 0], [10, 0], [10, 10], [0, 10]]])
    assert caught.value is error
    assert diagnostic._current.get() is None
    assert events(caplog)[-1]['outcome'] == 'error'
    assert 'SECRET_RECOGNIZED_VALUE' not in caplog.text
