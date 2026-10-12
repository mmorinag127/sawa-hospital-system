#!/usr/bin/env python3
"""Owned, non-deploy Linux comparison. Test failures remain CI failures.

Only this verification harness creates the historical dependency environments.
Production installers, source, tests and dependency declarations are not edited.
"""
import argparse
from copy import deepcopy
from hashlib import file_digest
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import traceback
import xml.etree.ElementTree as ET

BRANCH = 'codex/modernization-native-ci-20261013'
PRIMARY = 'backend/tests/unit/test_modernization_owned_numeric_fax.py'
REVIEW = ['backend/tests/unit/test_modernization_native_operator_review.py',
          'backend/tests/unit/test_modernization_native_receipt_guards.py']
DELTAS = {'backend': {'pillow': ('12.1.1', '12.3.0'), 'grpcio': ('1.78.1', '1.80.0'),
                      'grpcio-status': ('1.78.1', '1.80.0')},
          'ocr_pipeline': {'pillow': ('10.4.0', '12.3.0')}}
FIXED = {'torch': '2.6.0+cpu', 'torchvision': '0.21.0+cpu',
         'yomitoku': '0.9.5', 'sympy': '1.13.1'}
FONT = Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')
TEST_TOOLS = ['pytest', 'httpx2', 'fonttools']
DEADLINE = None


def digest(path):
    with Path(path).open('rb') as handle:
        return file_digest(handle, 'sha256').hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def load(path):
    return json.loads(Path(path).read_text())


def owned_root(run_id, attempt):
    if any(not re.fullmatch(r'[1-9][0-9]{0,19}', x) for x in (run_id, attempt)):
        raise ValueError('explicit numeric GitHub run and attempt required')
    return Path('/tmp').resolve() / f'sawa-native-{run_id}-{attempt}'


def clean_environment(home):
    return {'PATH': os.environ['PATH'], 'HOME': str(home), 'LANG': 'C.UTF-8',
            'LC_ALL': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1',
            'PIP_CONFIG_FILE': os.devnull, 'PIP_DISABLE_PIP_VERSION_CHECK': '1',
            'PIP_NO_INPUT': '1', 'HF_HUB_DISABLE_TELEMETRY': '1',
            'DO_NOT_TRACK': '1', 'TOKENIZERS_PARALLELISM': 'false'}


def group_exists(pid):
    try:
        os.killpg(pid, 0)
        return True
    except ProcessLookupError:
        return False


def command(argv, *, cwd, env, evidence, timeout=1200):
    """Every child has a bounded, owned process group, including failed children."""
    if DEADLINE is not None:
        timeout = min(timeout, int(DEADLINE - time.monotonic()) - 30)
        if timeout <= 0:
            raise TimeoutError('native CI total deadline reached; no new process started')
    evidence.mkdir(parents=True, exist_ok=False)
    argv = list(map(str, argv))
    record = {'argv': argv, 'cwd': str(cwd), 'environment': env,
              'timeout_seconds': timeout, 'reaped': False}
    save(evidence / 'command.json', record)
    started = time.monotonic()
    process = None
    try:
        with (evidence / 'stdout.log').open('w') as out, (evidence / 'stderr.log').open('w') as err:
            process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=out, stderr=err,
                                       start_new_session=True)
            record['pid'] = process.pid
            try:
                record['exit'] = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                record['timed_out'] = True
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=10)
                record['exit'] = process.returncode
    finally:
        if process is not None:
            # A reaped group leader can still have descendants. Never leave them running.
            if group_exists(process.pid):
                record['leftover_group'] = True
                os.killpg(process.pid, signal.SIGKILL)
            if process.poll() is None:
                process.wait(timeout=10)
            record['reaped'] = process.poll() is not None
            record.setdefault('exit', process.returncode)
            deadline = time.monotonic() + 10
            while group_exists(process.pid) and time.monotonic() < deadline:
                time.sleep(0.05)
            record['process_group_absent'] = not group_exists(process.pid)
        record['wall_seconds'] = time.monotonic() - started
        save(evidence / 'result.json', record)
    return record


def require_command(argv, **kwargs):
    result = command(argv, **kwargs)
    if (result['exit'] or result.get('timed_out') or result.get('leftover_group')
            or not result['process_group_absent']):
        raise RuntimeError(f'command failed: {kwargs["evidence"]}')
    return result


def versions(text):
    result = {}
    for line in text.splitlines():
        if not line or line.startswith('#'):
            continue
        if '==' not in line or ' @ ' in line:
            raise ValueError(f'non-pinned installed package: {line}')
        name, version = line.split('==', 1)
        key = re.sub(r'[-_.]+', '-', name).lower()
        if key in result:
            raise ValueError(f'duplicate installed package: {key}')
        result[key] = version
    return result


def dependency_delta(before, after, track):
    changed = {k: [before.get(k), after.get(k)] for k in before.keys() | after.keys()
               if before.get(k) != after.get(k)}
    expected = {k: list(v) for k, v in DELTAS[track].items()}
    if changed != expected:
        raise ValueError(f'unapproved dependency delta: {changed}')
    for runtime in (before, after):
        if any(runtime.get(k) != v for k, v in FIXED.items()):
            raise ValueError('fixed CPU/model dependencies changed')
        if any(k.startswith(('nvidia-', 'cuda-', 'cupy', 'tensorrt')) for k in runtime):
            raise ValueError('GPU package present')
    return changed


def differences(a, b, path=''):
    if type(a) is not type(b):
        return [{'path': path, 'kind': 'type', 'before': a, 'after': b}]
    if isinstance(a, dict):
        out = []
        for key in sorted(a.keys() | b.keys()):
            if key not in a or key not in b:
                out.append({'path': path + '/' + key, 'kind': 'key',
                            'before': a.get(key), 'after': b.get(key)})
            else:
                out.extend(differences(a[key], b[key], path + '/' + key))
        return out
    if isinstance(a, list):
        out = ([] if len(a) == len(b) else
               [{'path': path, 'kind': 'length', 'before': len(a), 'after': len(b)}])
        for index, (x, y) in enumerate(zip(a, b)):
            out.extend(differences(x, y, path + '/' + str(index)))
        return out
    return [] if a == b else [{'path': path, 'kind': 'value', 'before': a, 'after': b}]


def native_semantics(observed):
    expected = ['_build_preprocess_for_ocr', '_run_text_recognizer_records',
                '_apply_hakodate_sheet_output_to_sheet_payload']
    if [x['function'] for x in observed] != expected:
        raise ValueError('complete preprocess, recognizer and projection observations required')
    result = deepcopy(observed)
    records, metrics, _contact_sheet = result[1]['result']['items']
    if len(records['items']) != 56:
        raise ValueError('all 56 typed records required')
    timings = {}
    for key in ('contact_sheet_seconds', 'ocr_seconds'):
        if type(metrics.get(key)) not in (int, float):
            raise ValueError(f'missing actual timing: {key}')
        timings[key] = metrics.pop(key)
    return result, timings


def input_semantics(phase):
    audit = load(phase / 'audit.json')
    if audit['violations'] or audit['observer_errors']:
        raise ValueError('input evidence audit failed')
    items = load(phase / 'inputs.json')
    if len(items) != 1:
        raise ValueError('one complete primary numeric fixture input required')
    item = items[0]
    pdf = phase / item['pdf_file']
    if digest(pdf) != item['pdf_sha256'] or not item['page_pixels']:
        raise ValueError('authored PDF integrity failure')
    return {k: item[k] for k in ('scenario', 'page_pixels', 'font')}, pdf


def source_hashes(source):
    files = subprocess.check_output(['git', 'ls-files', '-z'], cwd=source).decode().split('\0')
    return {f: digest(source / f) for f in files if f and (source / f).is_file()}


def prepare_runtime(source, root, evidence, track, env):
    directory = root / track
    directory.mkdir()
    out = evidence / (track + '-dependencies')
    out.mkdir()
    req = source / track / 'requirements.txt'
    constraints = source / track / 'constraints-yomitoku-cpu.txt'
    (out / 'after-requirements.txt').write_bytes(req.read_bytes())
    (out / 'cpu-constraints.txt').write_bytes(constraints.read_bytes())
    before_req = out / 'before-requirements.txt'
    text = req.read_text()
    for name, (old, new) in DELTAS[track].items():
        pattern = rf'(?im)^{re.escape(name)}=={re.escape(new)}$'
        text, count = re.subn(pattern, f'{name}=={old}', text)
        if count != 1:
            raise ValueError(f'actual declaration mismatch: {track}/{name}')
    before_req.write_text(text)
    (out / 'test-tools.txt').write_text('\n'.join(TEST_TOOLS) + '\n')
    freeze = {}
    for phase in ('after', 'before'):
        venv = directory / phase
        require_command([sys.executable, '-m', 'venv', venv], cwd=source, env=env,
                        evidence=out / (phase + '-create'))
        python = venv / 'bin/python'
        install = [python, '-m', 'pip', 'install', '--index-url', 'https://pypi.org/simple',
                   '-r', req if phase == 'after' else before_req, '-c', constraints,
                   '-r', out / 'test-tools.txt']
        if phase == 'before':
            install += ['-c', out / 'unchanged-packages.txt']
        require_command(install, cwd=source, env=env, evidence=out / (phase + '-install'), timeout=1500)
        require_command([python, '-m', 'pip', 'check'], cwd=source, env=env,
                        evidence=out / (phase + '-pip-check'))
        require_command([python, '-m', 'pip', 'freeze', '--all'], cwd=source, env=env,
                        evidence=out / (phase + '-freeze'))
        raw = (out / (phase + '-freeze/stdout.log')).read_text()
        freeze[phase] = versions(raw)
        if phase == 'after':
            (out / 'unchanged-packages.txt').write_text(''.join(
                f'{name}=={version}\n' for name, version in sorted(freeze[phase].items())
                if name not in DELTAS[track]))
        require_command([python, source / 'ocr_pipeline/scripts/check_no_gpu_packages.py'],
                        cwd=source, env=env, evidence=out / (phase + '-no-gpu'))
    save(out / 'delta.json', dependency_delta(freeze['before'], freeze['after'], track))
    return {phase: directory / phase / 'bin/python' for phase in ('before', 'after')}


def phase_environment(source, phase, models, font):
    for name in ('home', 'cache', 'temp', 'outputs', 'artifacts'):
        (phase / name).mkdir()
    env = clean_environment(phase / 'home')
    uri = 'sqlite:///' + str(phase / 'bootstrap.sqlite')
    env.update({key: uri for key in ('DB_URI', 'ALEMBIC_DB_URI', 'DATABASE_URL')})
    env.update({'OWNED_EVIDENCE_ROOT': str(phase), 'NATIVE_CI_SOURCE': str(source),
                'NATIVE_CI_FONT': font['path'], 'NATIVE_CI_FONT_SHA256': font['sha256'],
                'NATIVE_CI_FONT_PACKAGE': font['package_version'],
                'HF_HOME': str(models), 'HF_HUB_OFFLINE': '1',
                'HF_HUB_DISABLE_IMPLICIT_TOKEN': '1', 'TRANSFORMERS_OFFLINE': '1',
                'HF_DATASETS_OFFLINE': '1', 'TMPDIR': str(phase / 'temp'),
                'XDG_CACHE_HOME': str(phase / 'cache'), 'OCR_ARTIFACT_DIR': str(phase / 'artifacts'),
                'ORDERS_OUTPUT_DIR': str(phase / 'outputs'),
                'OCR_PIPELINE_STATE_URI': str(phase / 'state.json'),
                'PYTHONPATH': os.pathsep.join(map(str, (source / 'backend',
                    source / 'backend/tests/unit', source / 'scripts', source / 'ocr_pipeline'))),
                'GOOGLE_APPLICATION_CREDENTIALS': str(phase / 'no-credentials.json'),
                'GCE_METADATA_HOST': '127.0.0.1:9'})
    # These services create output directories at import time, before fixtures run.
    for key in ('ORDER_FORM_OUTPUT_DIR', 'FAX_ORDER_FORM_OUTPUT_DIR',
                'FAX_SOURCE_TEMPLATE_MATERIALIZED_DIR', 'SHIPPING_OUTPUT_DIR',
                'OCR_TRAINING_EXPORT_DIR', 'SYSTEM_EXPORT_DIR', 'SHIPPING_EXPORT_DIR'):
        directory = phase / 'outputs' / key.lower()
        directory.mkdir()
        env[key] = str(directory)
    return env


def cases(phase):
    document = ET.parse(phase / 'results.xml')
    result = []
    for item in document.findall('.//testcase'):
        state = next((key for key in ('failure', 'error', 'skipped')
                      if item.find(key) is not None), 'passed')
        result.append({'id': item.get('classname', '') + '::' + item.get('name', ''),
                       'state': state, 'message': None if state == 'passed' else item.find(state).get('message')})
    if not result:
        raise ValueError('empty test collection')
    save(phase / 'cases.json', result)
    return result


def run(source, root):
    global DEADLINE
    DEADLINE = time.monotonic() + 85 * 60
    root.mkdir(mode=0o700, exist_ok=False)
    evidence = root / 'evidence'
    evidence.mkdir()
    (root / 'home').mkdir()
    env = clean_environment(root / 'home')
    env['PIP_CACHE_DIR'] = str(root / 'pip-cache')
    probe = source / 'scripts/native_dependency_probe.py'
    initial = source_hashes(source)
    summary = {'source_sha': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip(),
               'source': str(source), 'label': 'Linux-authored-input font override; not Mac/live/production renderer proof',
               'total_deadline_seconds': 85 * 60, 'visual_review_required': True, 'failures': [], 'phases': {}}
    save(evidence / 'source-before.json', initial)
    save(evidence / 'run.json', summary)
    runtimes = {}
    try:
        require_command([sys.executable, '-B', source / 'scripts/native_dependency_audit_checks.py',
                         '--output', evidence / 'fd-audit-checks'], cwd=source, env=env,
                        evidence=evidence / 'fd-audit-process', timeout=120)
        if not FONT.is_file() or FONT.resolve() != FONT:
            raise ValueError(f'explicit runner Noto CJK font missing or aliased: {FONT}')
        require_command(['dpkg-query', '-W', '-f=${Version}', 'fonts-noto-cjk'],
                        cwd=source, env=env, evidence=evidence / 'font-package')
        font = {'path': str(FONT), 'sha256': digest(FONT),
                'package_version': (evidence / 'font-package/stdout.log').read_text().strip()}
        save(evidence / 'font.json', font)
        for track in DELTAS:
            try:
                runtimes[track] = prepare_runtime(source, root, evidence, track, env)
            except Exception:
                summary['failures'].append({'phase': track + '-setup', 'error': traceback.format_exc()})
        if not runtimes:
            raise ValueError('no complete runtime pair available')
        models = root / 'models'
        downloader = next(iter(runtimes.values()))['after']
        require_command([downloader, probe, 'models', '--models', models,
                         '--output', evidence / 'models.json'], cwd=source, env=env,
                        evidence=evidence / 'model-download', timeout=1500)

        def execute(track, version, name, args, pdf=None):
            phase = evidence / name
            phase.mkdir()
            native_env = phase_environment(source, phase, models, font)
            if pdf is not None:
                native_env.update({'OWNED_PDF': str(pdf), 'OWNED_PDF_SHA256': digest(pdf)})
            before_models = {str(p.relative_to(models)): digest(p) for p in models.rglob('*') if p.is_file()}
            result = command([runtimes[track][version], '-B', probe, *args],
                             cwd=source, env=native_env, evidence=phase / 'process', timeout=900)
            result['models_unchanged'] = before_models == {
                str(p.relative_to(models)): digest(p) for p in models.rglob('*') if p.is_file()}
            result['font_unchanged'] = digest(FONT) == font['sha256']
            save(phase / 'model-hashes.json', before_models)
            if args[0] == 'backend' and (phase / 'results.xml').is_file():
                result['cases'] = cases(phase)
            audit = load(phase / 'audit.json') if (phase / 'audit.json').is_file() else None
            required_models = ({'text-recognizer-parseq-middle-v2'} if track == 'backend' else
                               {'text-recognizer-parseq-middle-v2', 'text-detector-dbnet-v2',
                                'layout-parser-rtdtrv2-v2', 'table-structure-recognizer-rtdtrv2-open-beta'})
            actual_models = ({x['name'] for x in load(phase / 'model-loads.json')}
                             if (phase / 'model-loads.json').is_file() else set())
            result['audit_clean'] = (audit is not None and not audit['violations']
                                     and not audit['observer_errors'] and actual_models == required_models)
            summary['phases'][name] = result
            if (result['exit'] or not result['models_unchanged'] or not result['font_unchanged'] or not result['audit_clean']
                    or not result['process_group_absent'] or result.get('leftover_group')
                    or any(c['state'] != 'passed' for c in result.get('cases', []))):
                summary['failures'].append({'phase': name, 'reason': 'test/probe/audit/process failure; not waived'})
            return phase

        pdf = None
        if 'backend' in runtimes:
            before = execute('backend', 'before', 'backend-before', ['backend', PRIMARY])
            after = execute('backend', 'after', 'backend-after', ['backend', PRIMARY])
            try:
                x, pdf_before = input_semantics(before)
                y, pdf_after = input_semantics(after)
                if not all(summary['phases'][name]['font_unchanged'] for name in
                           ('backend-before', 'backend-after')):
                    raise ValueError('source font changed; input parity blocked')
                changed = differences(x, y)
                save(evidence / 'input-parity.json', {'differences': changed,
                     'exact_pixels_and_scenario': not changed, 'pdf_bytes_equal': digest(pdf_before) == digest(pdf_after),
                     'pdf_metadata_differences': differences(load(before / 'inputs.json')[0]['pdf_metadata'],
                                                            load(after / 'inputs.json')[0]['pdf_metadata']),
                     'before_pdf_sha256': digest(pdf_before), 'after_pdf_sha256': digest(pdf_after)})
                if changed:
                    raise ValueError('Linux authored inputs differ; pipeline comparison blocked')
                pdf = pdf_before
                a, timing_a = native_semantics(load(before / 'native-observed.json'))
                b, timing_b = native_semantics(load(after / 'native-observed.json'))
                changed = differences(a, b)
                save(evidence / 'backend-parity.json', {'exact_except_two_timings': not changed,
                     'excluded_timings': {'before': timing_a, 'after': timing_b}, 'differences': changed})
                if changed:
                    raise ValueError('native dependency output drift')
            except Exception:
                summary['failures'].append({'phase': 'backend-parity', 'error': traceback.format_exc()})
            execute('backend', 'after', 'backend-operator-guards', ['backend', *REVIEW])
        if 'ocr_pipeline' in runtimes and pdf is not None:
            before = execute('ocr_pipeline', 'before', 'ocr-before', ['ocr'], pdf)
            after = execute('ocr_pipeline', 'after', 'ocr-after', ['ocr'], pdf)
            try:
                changed = {name: differences(load(before / name), load(after / name))
                           for name in ('pixels.json', 'native-result.json')}
                save(evidence / 'ocr-parity.json', {'exact': not any(changed.values()), 'differences': changed})
                if any(changed.values()):
                    raise ValueError('OCR pipeline pixel/native output drift')
            except Exception:
                summary['failures'].append({'phase': 'ocr-parity', 'error': traceback.format_exc()})
        else:
            summary['failures'].append({'phase': 'ocr', 'reason': 'runtime pair or validated input unavailable'})
    except Exception:
        summary['failures'].append({'phase': 'runner', 'error': traceback.format_exc()})
    finally:
        final = source_hashes(source)
        save(evidence / 'source-after.json', final)
        summary['source_unchanged'] = initial == final
        if initial != final:
            summary['failures'].append({'phase': 'source', 'reason': 'tracked source changed'})
        summary['exit'] = 1 if summary['failures'] else 0
        summary['acceptance'] = 'Pending parent visual inspection; never whole-task/live approval'
        save(evidence / 'summary.json', summary)
        save(evidence / 'sha256.json', {str(p.relative_to(evidence)): digest(p)
             for p in sorted(evidence.rglob('*')) if p.is_file() and p.name != 'sha256.json'})
    return summary['exit']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--attempt', required=True)
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    if (sys.platform != 'linux' or sys.version_info[:2] != (3, 11)
            or os.environ.get('GITHUB_ACTIONS') != 'true'
            or os.environ.get('GITHUB_REF') != 'refs/heads/' + BRANCH
            or not os.environ.get('GITHUB_REPOSITORY_OWNER')
            or os.environ.get('GITHUB_ACTOR') != os.environ['GITHUB_REPOSITORY_OWNER']):
        parser.error('owner/exact-branch Linux Python3.11 GitHub Actions only')
    def interrupted(_signal, _frame):
        raise InterruptedError('CI termination requested; reap owned process group')
    signal.signal(signal.SIGTERM, interrupted)
    return run(source, owned_root(args.run_id, args.attempt))


if __name__ == '__main__':
    raise SystemExit(main())
