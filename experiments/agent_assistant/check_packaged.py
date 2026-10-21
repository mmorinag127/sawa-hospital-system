"""Image-build smoke check: fixed sources, native imports, SQLite and shutdown.

No model calls, cloud queues, external uploads or business approvals are made.
"""
import asyncio
from pathlib import Path
import shutil
from uuid import uuid4

from .runtime import configuration
from .sandbox import Sandbox


async def check():
    if any(not shutil.which(binary) for binary in ('soffice','pdftoppm')) or not list(Path('/usr/share/fonts').rglob('NotoSansCJK*.ttc')):
        raise RuntimeError('Native workbook PDF conversion and Japanese fonts are required')
    config=configuration('/opt/sawa-agent')
    # Image smoke uses only the declared synthetic baselines, with no cloud reads.
    config['domains']['school-lunch']['registered_source']=False
    root=Path('/tmp/sawa-agent-image-smoke')
    try:
        for domain,cfg in config['domains'].items():
            # Import/start smoke needs no recognition weights or external API key.
            cfg=dict(cfg);cfg.pop('model_cache',None)
            sandbox=Sandbox(root,{domain:cfg})
            run={'id':uuid4().hex,'domains':[domain]}
            try:
                sandbox.prepare(run)
                await sandbox.start(run)
                import sqlite3
                with sqlite3.connect(sandbox.path(run['id'])/'data'/domain/'database.sqlite3') as db:
                    assert db.execute('PRAGMA journal_mode').fetchone()[0]=='wal'
                    assert not list(db.execute('PRAGMA foreign_key_check'))
                print(domain+': fixed-source preparation, native startup and isolated WAL passed',flush=True)
            finally:
                await sandbox.stop(run['id'])
                assert not sandbox.children
    finally:
        shutil.rmtree(root,ignore_errors=True)


if __name__=='__main__':asyncio.run(check())
