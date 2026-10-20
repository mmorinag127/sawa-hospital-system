"""Bound the MCP wire representation, never the canonical command receipt."""
import json

LIMIT = 48000


def project(result):
    if '_content' in result or len(json.dumps(result, ensure_ascii=False)) <= LIMIT:
        return result
    summary = {key: result[key] for key in ('ok', 'status', 'command_id', 'error', 'state', 'queued_for_human', 'artifact', 'task', 'review_id') if key in result}
    if isinstance(result.get('review'), dict):
        summary['review'] = {key: result['review'][key] for key in ('id', 'state', 'summary', 'decision') if key in result['review']}
    if isinstance(result.get('items'), list):
        # FAX work-state belongs on the wire even when its full OCR review does
        # not. Preserve every target; oversized batches use the receipt reader.
        items=[]
        for item in result['items']:
            if not isinstance(item,dict) or 'fax_id' not in item:break
            items.append({key:item[key] for key in ('fax_id','state','snapshot','decision') if key in item})
        if len(items)==len(result['items']):summary['items']=items
    if len(json.dumps(summary,ensure_ascii=False))>LIMIT:
        summary={key:result[key] for key in ('ok','status','command_id','error','state','queued_for_human','review_id') if key in result}
    return summary | {
        'result_stored': True,
        'keys': list(result),
        'next_step': '完全な結果は同じ依頼のサーバー証跡に保存済みです。read_tool_resultにcommand_idとJSON Pointerを渡して必要な項目を読む。シフトはprepare_shift_review/complete_shift_work、FAXはprepare_fax_review/complete_fax_workで正本・版・証跡の転記を省く。',
    }


def read(tools, run, raw):
    if set(raw) - {'command_id', 'pointer', 'offset', 'count'} or not {'command_id', 'pointer'} <= set(raw):
        raise ValueError('command_idとJSON Pointerを指定してください')
    commands = {item['id']: item for item in tools.flow.commands(run['id'])}
    if raw['command_id'] not in commands:
        raise ValueError('この依頼の完了済みツール証跡ではありません')
    value = commands[raw['command_id']]['result']
    pointer = raw['pointer']
    if not isinstance(pointer, str) or (pointer and not pointer.startswith('/')):
        raise ValueError('JSON Pointerは空文字または/で始まるパスです')
    for token in pointer.split('/')[1:] if pointer else []:
        token = token.replace('~1', '/').replace('~0', '~')
        try:
            value = value[int(token)] if isinstance(value, list) else value[token]
        except (KeyError, IndexError, ValueError, TypeError):
            raise ValueError('JSON Pointerが保存結果に存在しません') from None
    if isinstance(value, list):
        offset = raw.get('offset', 0); count = raw.get('count', 10)
        if type(offset) is not int or offset < 0 or type(count) is not int or not 1 <= count <= 25:
            raise ValueError('offsetは0以上、countは1〜25です')
        return {'ok': True, 'source_command_id': raw['command_id'], 'pointer': pointer, 'total': len(value), 'offset': offset, 'data': value[offset:offset+count]}
    if len(json.dumps(value, ensure_ascii=False)) > LIMIT:
        return {'ok': True, 'source_command_id': raw['command_id'], 'pointer': pointer, 'keys': list(value) if isinstance(value, dict) else [], 'next_step': '子項目のJSON Pointerを指定してください。'}
    return {'ok': True, 'source_command_id': raw['command_id'], 'pointer': pointer, 'data': value}
