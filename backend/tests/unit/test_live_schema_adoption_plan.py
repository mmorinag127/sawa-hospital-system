import json
from copy import deepcopy

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from src.maintenance import live_schema_adoption as adoption


def test_adoption_includes_preservation_and_current_successors():
    contract = json.loads(adoption.CONTRACT.read_text())
    target, revisions = adoption._adoption_plan(contract)
    assert target == ScriptDirectory.from_config(Config(str(adoption.ALEMBIC_CONFIG))).get_current_head()
    names = [revision.revision for revision in revisions]
    assert names[0] == "0027"
    assert names[-1] == target
    assert names.index("0035") < names.index("0036")


def test_adoption_refuses_unobserved_successor_contract():
    contract = deepcopy(json.loads(adoption.CONTRACT.read_text()))
    head = ScriptDirectory.from_config(Config(str(adoption.ALEMBIC_CONFIG))).get_current_head()
    del contract["revisions"]["postgresql"][head]
    with pytest.raises(RuntimeError, match="observed target"):
        adoption._adoption_plan(contract)


def test_adoption_refuses_split_heads(monkeypatch):
    monkeypatch.setattr(ScriptDirectory, "get_heads", lambda self: ["0035", "0036"])
    with pytest.raises(RuntimeError, match="exactly one reviewed target"):
        adoption._adoption_plan(json.loads(adoption.CONTRACT.read_text()))
