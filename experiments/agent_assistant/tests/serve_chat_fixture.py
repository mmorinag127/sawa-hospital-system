"""Synthetic chat exercise, isolated copies only, no provider/model calls."""
import asyncio

import uvicorn

from experiments.agent_assistant.tests.serve_ui_fixture import app, store, tools, managed, work, ROOT
from experiments.agent_assistant.conversation import check_answer, pending_question
from experiments.agent_assistant.store import Conflict

async def ask(run, ident, raw):
    result=await tools.execute(run['id'],run['generation'],'ask_operator',raw,ident)
    store.event(run['id'],'operator_wait',{'command_id':ident,'result':result})

async def start(run):
    store.update(run['id'],status='running')
    await ask(store.get(run['id']),'synthetic-question-1',{'question':'【合成画面試験】勤務条件の確認をどう進めますか？','options':['登録内容を確認して案を作る','作業を停止する']})

async def reply(ident,message,question_id=None,option=None):
    async with tools.lock(ident):
        run=store.get(ident)
        if run['status']!='waiting_input':raise Conflict('回答待ちではありません')
        check_answer(store.events(ident),message,question_id,option)
        question=pending_question(store.events(ident))
        store.event(ident,'operator_reply',{'message':message,'question_id':question_id,'option':option})
        store.update(ident,status='running')
    if message=='作業を停止する':
        await managed.stop(ident);return
    run=store.get(ident)
    if question['question_id']=='synthetic-question-1':
        await ask(run,'synthetic-question-2',{'question':'【合成画面試験】確認時の申し送りがあれば教えてください。なければ「なし」と回答してください。'})
    else:
        managed.tasks[ident]=asyncio.create_task(work(run))
managed.start=start
managed.reply=reply

if __name__=='__main__':
    print('SYNTHETIC_CHAT_ROOT='+str(ROOT),flush=True)
    uvicorn.run(app,host='127.0.0.1',port=8148)
