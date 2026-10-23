"""Persisted operator questions and their corresponding answers."""
from .store import Conflict


def question_result(raw, command_id):
    if set(raw) - {"question", "options"} or not isinstance(raw.get("question"), str) or not raw["question"].strip() or len(raw["question"]) > 2000:
        raise ValueError("質問は1〜2000文字で指定してください")
    options = raw.get("options", [])
    if not isinstance(options, list) or len(options) > 8 or any(not isinstance(x, str) or not x.strip() or len(x) > 200 for x in options) or len(set(options)) != len(options):
        raise ValueError("選択肢は重複のない200文字以内の文字列を8個まで指定してください")
    return {"waiting_input": True, "question": raw["question"], "options": options, "command_id": command_id}


def conversation(events, fax_labels=None):
    messages = []
    for event in events:
        kind, payload = event["kind"], event["payload"]
        if kind == "tool_result" and payload.get("result", {}).get("question"):
            result = payload["result"]
            messages.append({"id": str(event["id"]), "role": "assistant", "text": result["question"], "question_id": result.get("command_id"), "options": result.get("process_choices", result.get("options", [])), "process_context": result.get("process_context"), "question_map": result.get("question_map")})
        elif kind == "agent.message":
            text = "\n".join(c.get("text", "") for c in payload.get("content", []) if c.get("type") == "text")
            if text: messages.append({"id": str(event["id"]), "role": "assistant", "text": text})
        elif kind in ("notice", "error", "completion_evidence"):
            text = payload.get("summary") or payload.get("message")
            if text: messages.append({"id": str(event["id"]), "role": "assistant", "text": text})
        elif kind in ("task_review", "fax_review") and payload.get("summary"):
            prefix = (fax_labels.get(str(payload['fax_id']), '対象のFAX') if fax_labels is not None else 'FAX ' + str(payload['fax_id'])) + '：' if kind == 'fax_review' else ''
            messages.append({"id":str(event["id"]),"role":"assistant","text":prefix+payload["summary"]})
        elif kind in ("task_decision", "fax_decision"):
            text = "この内容で進めてください。" if payload['approve'] else "訂正をお願いします。"
            if payload.get('message'): text += "\n"+payload['message']
            if kind == 'fax_decision':
                label = fax_labels.get(str(payload['fax_id']), '対象のFAX') if fax_labels is not None else 'FAX ' + str(payload['fax_id'])
                text = label + '：' + text
            messages.append({"id":str(event["id"]),"role":"user","text":text})
        elif kind == "operator_reply":
            messages.append({"id": str(event["id"]), "role": "user", "text": payload["message"]})
    return messages


def pending_question(events):
    # Result approvals and per-FAX decisions do not answer a separate question.
    last_reply=max((e['id'] for e in events if e['kind']=='operator_reply'),default=0)
    return next((m for m in reversed(conversation(events)) if m.get('question_id') and int(m['id'])>last_reply),None)


def check_answer(events, message, question_id=None, option=None):
    if not message.strip(): raise ValueError("回答を入力してください")
    pending = pending_question(events)
    if question_id is not None or option is not None:
        if not pending or pending["question_id"] != question_id:
            raise Conflict("質問が更新済みです。最新の質問に回答してください")
    elif pending and pending["options"]:
        raise Conflict("回答する質問を指定してください")
    if option is not None and (option not in pending["options"] or message != option):
        raise ValueError("提示された選択肢を選んでください")


def record_question_receipt(store, ident, name, result):
    """Repair the receipt/event crash window without creating a second question."""
    command = result.get('command_id')
    if not command or not result.get('question'):
        return
    if any(e['kind'] == 'tool_result' and e['payload'].get('result', {}).get('command_id') == command
           for e in store.events(ident)):
        return
    store.event(ident, 'tool_result', {'tool': name, 'result': {k: v for k, v in result.items() if k != '_content'}},
                'recovery-question:' + command)
