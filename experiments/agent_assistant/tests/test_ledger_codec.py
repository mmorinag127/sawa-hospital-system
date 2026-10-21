"""Lossless transport and explicit rejection of damaged/unknown ledger formats."""
import hashlib
import sqlite3
import zlib

import pytest

from experiments.agent_assistant.durable import decode_ledger, encode_ledger, BLOB_HEADER
from experiments.agent_assistant.store import Conflict


def ledger_bytes():
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE proof (value TEXT)')
        db.execute('INSERT INTO proof VALUES (?)',('質問・承認・停止・巻き戻し' * 100_000,))
        return db.serialize()


def test_full_ledger_roundtrip_is_byte_identical_and_legacy_is_explicit():
    raw=ledger_bytes();encoded=encode_ledger(raw)
    assert len(encoded)<len(raw)//10
    assert encoded.startswith(BLOB_HEADER)
    assert decode_ledger(raw)==raw
    assert decode_ledger(encoded)==raw
    assert hashlib.sha256(decode_ledger(encoded)).digest()==hashlib.sha256(raw).digest()
    with sqlite3.connect(':memory:') as db:
        db.deserialize(decode_ledger(encoded))
        assert db.execute('SELECT value FROM proof').fetchone()[0]=='質問・承認・停止・巻き戻し'*100_000


@pytest.mark.parametrize('damage',['truncated','extra','digest','unknown','non_sqlite'])
def test_damaged_or_unknown_format_never_becomes_a_new_empty_ledger(damage):
    encoded=encode_ledger(ledger_bytes())
    if damage=='truncated':encoded=encoded[:-4]
    if damage=='extra':encoded+=b'unexpected trailing bytes'
    if damage=='digest':encoded=encoded[:len(BLOB_HEADER)]+bytes(32)+encoded[len(BLOB_HEADER)+32:]
    if damage=='unknown':encoded=b'Unknown:2\0'+encoded
    if damage=='non_sqlite':encoded=BLOB_HEADER+hashlib.sha256(b'bad').digest()+zlib.compress(b'bad')
    with pytest.raises(Conflict):decode_ledger(encoded)


def test_files_use_exact_persisted_hash_for_legacy_and_compressed_bytes():
    from experiments.agent_assistant.durable import decode_blob,encode_blob
    raw=b'%PDF-trial-original\n'+bytes(range(256))*1000
    digest=hashlib.sha256(raw).hexdigest()
    assert decode_blob(raw,expected=digest)==raw
    assert decode_blob(encode_blob(raw),expected=digest)==raw
    with pytest.raises(Conflict):decode_blob(encode_blob(raw),expected='0'*64)
    with pytest.raises(Conflict):decode_blob(raw+b'changed',expected=digest)


def test_incompressible_original_is_preserved_without_a_new_size_rejection():
    from random import Random
    from experiments.agent_assistant.durable import decode_blob,encode_blob
    raw=Random(20261021).randbytes(1_000_000)
    digest=hashlib.sha256(raw).hexdigest()
    assert decode_blob(encode_blob(raw),expected=digest)==raw
    legacy_with_header=BLOB_HEADER+raw
    assert decode_blob(legacy_with_header,expected=hashlib.sha256(legacy_with_header).hexdigest())==legacy_with_header


@pytest.mark.parametrize('damage',['truncated','header_only','invalid_page'])
def test_corrupt_legacy_sqlite_is_rejected_before_schema_initialization(damage):
    raw=ledger_bytes()
    if damage=='truncated':raw=raw[:-100]
    if damage=='header_only':raw=raw[:16]
    if damage=='invalid_page':raw=raw[:100]+b'\xff'*100+raw[200:]
    with pytest.raises(Conflict,match='破損'):decode_ledger(raw)
