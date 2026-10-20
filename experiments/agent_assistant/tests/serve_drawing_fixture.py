"""Synthetic ingredients/work; unmodified registered kitchen SVG and geometry."""
import json
from pathlib import Path
from experiments.agent_assistant.tests.serve_daily_fixture import *
picture=json.loads((Path(__file__).resolve().parents[3]/'tmp/agent-assistant-trial/canonical-question-drawing.json').read_text())
ids={'vegetable-prep-table','vegetable-worktable-center','vegetable-worktable-north','kettle-a','kettle-b','kettle-c','kettle-d','kettle-e','kettle-f'}
equipment=[{**e,'type':'回転釜' if e['id'].startswith('kettle-') else '作業台','ports':[]} for e in picture['equipment'] if e['id'] in ids]
native.data['/api/school-lunch/menu-executions/101/process/question-drawing']=picture
native.data['/api/school-lunch/menu-executions/101/process/route-options']['templates'][0].update(template_version=picture['template_version'],equipment=equipment)
native.data['/api/school-lunch/route-diagrams/layouts'][0]['locations']={e['name']:[0.5,0.5] for e in equipment}
