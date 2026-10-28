"""Small historical period facts, using the existing run-period rules verbatim."""
from datetime import date
import json
import re


def display_period(target_month, config):
    config = config or {}
    start = config.get('start_date')
    end = config.get('end_date_exclusive') or config.get('end_date')
    mode = config.get('period_mode', 'calendar_month')
    if start and end:
        return {'target_month': target_month, 'start_date': start, 'end_date_exclusive': end, 'period_mode': mode}
    if len(target_month) == 7:
        year, month = (int(part) for part in target_month.split('-'))
        next_month = f'{year + 1:04d}-01-01' if month == 12 else f'{year:04d}-{month + 1:02d}-01'
        return {'target_month': target_month, 'start_date': f'{target_month}-01',
                'end_date_exclusive': next_month, 'period_mode': 'calendar_month'}
    return {'target_month': target_month, 'start_date': None, 'end_date_exclusive': None, 'period_mode': mode}


def predecessor_end(target_month, config):
    config = config or {}
    raw_end = config.get('end_date_exclusive') or config.get('end_date')
    if raw_end:
        try:
            return date.fromisoformat(raw_end)
        except (TypeError, ValueError):
            return None
    if len(target_month) != 7:
        return None
    try:
        year, month = (int(part) for part in target_month.split('-'))
        return date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    except ValueError:
        return None


def _source_date(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def period_metadata(target_month, config):
    error = config is not None and not isinstance(config, dict)
    summary, end = {}, None
    if not error:
        try:
            summary = display_period(target_month, config)
            end = predecessor_end(target_month, config)
        except (TypeError, ValueError, AttributeError):
            error = True
    known = bool(not error and _source_date(summary.get('start_date'))
                 and _source_date(summary.get('end_date_exclusive'))
                 and summary['start_date'] < summary['end_date_exclusive'])
    prior = (config or {}).get('prior_candidate_for_start_date') if isinstance(config, dict) and config.get('prior_candidate_id') else None
    return {'period_summary_json': summary, 'period_end_key': end.isoformat() if end else None,
            'display_period_end_key': summary['end_date_exclusive'] if known else None,
            'period_known': known, 'period_display_error': error,
            'prior_candidate_start_key': prior if _source_date(prior) else None}


def rotation_metadata(config):
    references, invalid, end = set(), False, None
    config = config or {}
    if not isinstance(config, dict):
        return {'rotation_reference_error': True, 'rotation_end_key': None}, []
    for name in ('rotation_state_snapshot', 'rotation_queue_snapshot'):
        snapshot = config.get(name, {})
        if not isinstance(snapshot, dict):
            invalid = True
        else:
            for key in snapshot:
                persisted_key = key if isinstance(key, str) else (
                    json.dumps(key) if key is None or isinstance(key, (int, float)) else None)
                if persisted_key is not None and len(persisted_key) <= 128:
                    references.add(persisted_key)
    inputs = config.get('input_snapshot', {})
    ids = inputs.get('rotation_policy_ids', []) if isinstance(inputs, dict) else None
    if not isinstance(ids, list):
        invalid = True
    else:
        for item in ids:
            if isinstance(item, (int, float)):
                try:
                    integer = int(item)
                except (ValueError, OverflowError):
                    continue
                if item == integer:
                    references.add(str(integer))
    try:
        end = date.fromisoformat(config.get('end_date') or config.get('end_date_exclusive')).isoformat()
    except (TypeError, ValueError):
        pass
    return {'rotation_reference_error': invalid, 'rotation_end_key': end}, sorted(references)
