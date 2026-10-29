"""Process semantics and explicit daily work. No geometry or inferred mixing."""
from typing import Annotated, Literal, Any
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints, model_validator, model_serializer

Identifier = Annotated[str, StringConstraints(pattern=r'^[A-Za-z0-9_-]{1,80}$')]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
EQUIPMENT = ('シンク', '作業台', 'スライサー', 'ミキサー', '皮剥機', '炊飯器', '回転釜',
             'フライヤー', 'スチコン', 'ゼットオーブン', 'コンロ', '冷却器', '脱水機', '冷蔵庫', '冷凍庫')


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class MaterialSupply(Strict):
    template_id: Identifier
    mode: Literal['delivery', 'on_site'] = 'delivery'
    inspection_point_id: str = Field(default='', max_length=100)
    storage: Literal['unknown', 'direct', 'stored'] = 'unknown'
    storage_point_id: str = Field(default='', max_length=100)
    source_point_id: str = Field(default='', max_length=100)
    inference: dict[str, str] | None = None

    @model_serializer(mode='wrap')
    def preserve_legacy_shape(self, handler):
        value = handler(self)
        if self.inference is None:
            value.pop('inference', None)
        return value


class MaterialDrawing(Strict):
    """A render choice over the cooking graph, never a replacement for it."""
    role: Literal['main', 'seasoning', 'aid', 'individual']
    mode: Literal['independent', 'shared', 'annotation']
    group_id: str = Field(default='', max_length=100)
    independent_step_ids: list[Identifier] = Field(default_factory=list, max_length=300)
    merge_step_id: Identifier | None = None
    shared_step_ids: list[Identifier] = Field(default_factory=list, max_length=300)
    reason: Text
    uncertainty: str = Field(default='', max_length=1000)
    status: Literal['pending', 'user-confirmed'] = 'pending'

    @model_validator(mode='after')
    def mode_shape(self):
        if self.mode == 'shared' and (not self.group_id or not self.shared_step_ids):
            raise ValueError('共有描画にはグループIDと共有工程が必要です')
        if self.mode != 'shared' and self.group_id:
            raise ValueError('共有描画以外にグループIDは指定できません')
        return self

    @model_serializer(mode='wrap')
    def preserve_compact_shape(self, handler):
        value = handler(self)
        if self.merge_step_id is None:
            value.pop('merge_step_id', None)
        return value


class Material(Strict):
    id: Identifier
    name: Text
    state: str = Field(default='', max_length=200)
    supply: MaterialSupply | None = None
    drawing: MaterialDrawing | None = None

    @model_serializer(mode='wrap')
    def preserve_existing_shape(self, handler):
        value = handler(self)
        if self.supply is None:
            value.pop('supply', None)
        if self.drawing is None:
            value.pop('drawing', None)
        return value


class Input(Strict):
    ingredient_id: Identifier | None = None
    step_id: Identifier | None = None
    output_id: Identifier | None = None
    edge_id: Identifier | None = None
    label: str = Field(default='', max_length=500)

    @model_validator(mode='after')
    def exactly_one_source(self):
        if bool(self.ingredient_id) == bool(self.step_id) or (self.output_id and not self.step_id):
            raise ValueError('入力は材料または前工程の一方を指定してください')
        return self


class Equipment(Strict):
    type: str | None = Field(default=None, max_length=80)
    id: str | None = Field(default=None, max_length=100)

    @model_validator(mode='after')
    def known_type(self):
        if self.type is not None and self.type not in EQUIPMENT:
            raise ValueError('設備種類が登録リストにありません')
        if self.id and not self.type:
            raise ValueError('設備IDには設備種類が必要です')
        return self


class Output(Strict):
    id: Identifier
    label: str = Field(default='', max_length=500)
    # A place-graph draft may be edited before its split amounts are known.
    # Version 1 retains its non-empty quantity requirement in ProcessDocument.
    quantity: str = Field(default='', max_length=120)
    # A batch-group can leave by material, unlike portions of a mixed dish.
    ingredient_ids: list[Identifier] | None = Field(default=None, max_length=150,
        description='異なる食材を別々に渡す場合だけ食材IDを指定。同じ材料（水など）や混合物を量で取り分ける場合は必ずnull。')

    @model_serializer(mode='wrap')
    def preserve_legacy_shape(self, handler):
        value = handler(self)
        if self.ingredient_ids is None:
            value.pop('ingredient_ids', None)
        return value


class StepExecution(Strict):
    start_time: str | None = Field(default=None, pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    end_time: str | None = Field(default=None, pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    assignees: list[str] = Field(default_factory=list, max_length=100)
    work_zone: Literal['contaminated', 'contaminated_meat_fish', 'non_contaminated'] | None = None
    phase: Literal['prep', 'wash_open', 'cook', 'heat', 'plate', 'other'] | None = None
    kettle_use: int | None = Field(default=None, ge=1)
    uses_raw_meat: bool | None = None


class DailyPlacement(Strict):
    ingredient_ids: list[Identifier] = Field(min_length=1, max_length=150)
    equipment: Equipment
    question_id: Identifier


class DailyWork(Strict):
    """Operator-reported work, distinct from the recipe's material graph."""
    mode: Literal['individual', 'together', 'separate']
    grouping_question_id: Identifier | None = None
    placements: list[DailyPlacement] = Field(min_length=1, max_length=150)

    @model_serializer(mode='wrap')
    def compact(self, handler):
        value = handler(self)
        if self.grouping_question_id is None: value.pop('grouping_question_id', None)
        return value


class Step(Strict):
    id: Identifier
    # Version 2 intentionally permits an unfinished place node in a draft.
    # Version 1 and every confirmed document validate this as non-empty below.
    operation: str = Field(default='', max_length=500)
    inputs: list[Input] = Field(default_factory=list, max_length=150)
    equipment: Equipment = Field(default_factory=Equipment)
    conditions: dict[str, str] = Field(default_factory=dict)
    basis: dict[str, str] = Field(default_factory=dict)
    outputs: list[Output] = Field(default_factory=list, max_length=30)
    execution: StepExecution | None = None
    daily_work: DailyWork | None = None

    @model_validator(mode='after')
    def bounded(self):
        for mapping in (self.conditions, self.basis):
            if len(mapping) > 30 or any(len(k) > 100 or len(v) > 2000 for k, v in mapping.items()):
                raise ValueError('工程条件・根拠の文字数上限を超えています')
        return self

    @model_serializer(mode='wrap')
    def preserve_existing_shape(self, handler):
        value = handler(self)
        if self.execution is None:
            value.pop('execution', None)
        if self.daily_work is None:
            value.pop('daily_work', None)
        return value


class StepPorts(Strict):
    ingress: str = Field(default='', max_length=100)
    egress: str = Field(default='', max_length=100)


class RouteConfig(Strict):
    template_id: Identifier
    step_ports: dict[Identifier, StepPorts] = Field(default_factory=dict, max_length=300)
    color: str | None = Field(default=None, pattern=r'^#[0-9a-fA-F]{6}$')
    edge_styles: dict[Identifier, Literal['solid', 'dashed']] = Field(default_factory=dict, max_length=500)

    @model_serializer(mode='wrap')
    def preserve_old_document_shape(self, handler):
        value = handler(self)
        if self.color is None:
            value.pop('color', None)
        if not self.edge_styles:
            value.pop('edge_styles', None)
        return value


def material_flow(document):
    """Exact lineage for explicitly partitioned batches or mixed portions."""
    by_step, by_output = {}, {}
    materials = {m['id'] for m in document['ingredients']}
    for step in document['steps']:
        origins = set()
        for source in step['inputs']:
            if source.get('ingredient_id'):
                origins.add(source['ingredient_id'])
            elif source.get('output_id'):
                origins.update(by_output[(source['step_id'], source['output_id'])])
            else:
                origins.update(by_step[source['step_id']])
        by_step[step['id']] = sorted(origins)
        outputs = step.get('outputs', [])
        partitioned = any(o.get('ingredient_ids') is not None for o in outputs)
        if partitioned:
            if step.get('conditions', {}).get('processing_mode') != 'separate_batches':
                raise ValueError('食材別の分岐はseparate_batches（混ぜずに別処理）の工程だけに指定できます')
            used = set()
            for output in outputs:
                ids = output.get('ingredient_ids')
                if not ids or len(ids) != len(set(ids)):
                    raise ValueError('食材別の分岐は全出力に重複のない対象食材を指定してください')
                selected = set(ids)
                if not selected <= origins or not selected <= materials or used & selected:
                    raise ValueError('食材別の分岐に入力外または重複した食材があります。同じ材料や混合物を分量で分ける場合は全outputsのingredient_idsをnullにしquantityで表してください')
                used.update(selected)
            if used != origins:
                raise ValueError('食材別の分岐に行き先のない食材があります')
            preceding = {s['id']:s for s in document['steps']}
            for source in step['inputs']:
                if not source.get('step_id'):
                    continue
                upstream = preceding[source['step_id']]
                if upstream.get('conditions', {}).get('processing_mode') == 'separate_batches':
                    continue
                group = set(by_output[(source['step_id'], source['output_id'])]
                            if source.get('output_id') else by_step[source['step_id']])
                if any(group & set(o['ingredient_ids']) and not group <= set(o['ingredient_ids']) for o in outputs):
                    raise ValueError('食材別の分岐で上流の混合物を元の食材へ分離することはできません。混ぜない共通作業にはseparate_batchesを明示してください')
        for output in outputs:
            by_output[(step['id'], output['id'])] = sorted(output['ingredient_ids'] if partitioned else origins)
    return by_step, by_output


class SharedMenuSource(Strict):
    revision_id: int = Field(ge=1)
    material_mapping: dict[Identifier, Identifier] = Field(min_length=1, max_length=150)


class ProcessDocument(Strict):
    schema_version: Literal[1, 2] = 1
    ingredients: list[Material] = Field(min_length=1, max_length=150)
    steps: list[Step] = Field(default_factory=list, max_length=300)
    route_config: RouteConfig | None = None
    # Provenance is carried with the normal editable graph, then copied into the
    # immutable daily ProcessRevision by the ordinary save path.
    pattern_applications: list['PatternApplicationProvenance'] = Field(default_factory=list, max_length=100)
    shared_menu_source: SharedMenuSource | None = None
    daily_work_source: Identifier | None = None

    @model_serializer(mode='wrap')
    def preserve_existing_shape(self, handler):
        value = handler(self)
        if not self.pattern_applications:
            value.pop('pattern_applications', None)
        if self.shared_menu_source is None:
            value.pop('shared_menu_source', None)
        if self.daily_work_source is None:
            value.pop('daily_work_source', None)
        return value

    @model_validator(mode='after')
    def valid_graph(self):
        materials = {x.id for x in self.ingredients}
        steps = {x.id: x for x in self.steps}
        if len(materials) != len(self.ingredients) or len(steps) != len(self.steps):
            raise ValueError('材料ID・工程IDの重複があります')
        seen = set()
        edge_ids = set()
        consumers = {}
        for s in self.steps:
            out_ids = {o.id for o in s.outputs}
            if len(out_ids) != len(s.outputs) or len(s.outputs) == 1:
                raise ValueError('取り分けは重複しない2つ以上の出力を指定してください')
            if self.schema_version == 1 and s.outputs and s.operation != '取り分け':
                raise ValueError('複数出力は「取り分け」工程で指定してください')
            if self.schema_version == 1 and (not s.operation.strip() or any(not o.label.strip() or not o.quantity.strip() for o in s.outputs)):
                raise ValueError('工程名と取り分け量が必要です')
            for i in s.inputs:
                if i.edge_id:
                    if not i.step_id or i.edge_id in edge_ids:
                        raise ValueError('工程接続IDが重複しているか材料に付与されています')
                    edge_ids.add(i.edge_id)
                if i.ingredient_id and i.ingredient_id not in materials:
                    raise ValueError('存在しない材料が参照されています')
                if i.step_id:
                    if i.step_id not in seen:
                        raise ValueError('前工程の参照が不正です（循環・後方参照・削除済み工程）')
                    outputs = {o.id for o in steps[i.step_id].outputs}
                    if (outputs and i.output_id not in outputs) or (not outputs and i.output_id):
                        raise ValueError('取り分けの出力を正しく指定してください')
                key = ('ingredient', i.ingredient_id) if i.ingredient_id else ('step', i.step_id, i.output_id)
                consumers[key] = consumers.get(key, 0) + 1
                if consumers[key] > 1:
                    source = f'材料 {i.ingredient_id}' if i.ingredient_id else f'工程 {i.step_id} 出力 {i.output_id or "通常出力"}'
                    raise ValueError(f'同じ材料・工程出力を複数回使用しています（{source}、工程 {s.id}）。'
                                     '前処理後の工程への直列接続と重複参照を確認してください。'
                                     '実際に分量を分けて別用途へ使う場合だけ「取り分け」を指定してください')
            seen.add(s.id)
        if self.schema_version == 2:
            for s in self.steps:
                for source in s.inputs:
                    if not source.step_id:
                        continue
                    upstream = steps[source.step_id]
                    if s.daily_work is not None and upstream.daily_work is not None:
                        continue  # Actual work may revisit one place; retain each answered operation.
                    if (s.equipment.id and upstream.equipment.id
                            and s.equipment.id == upstream.equipment.id):
                        raise ValueError('同じ設備に直結する連続工程は同一の場所ノードにまとめてください')
        if self.route_config and any(step_id not in steps for step_id in self.route_config.step_ports):
            raise ValueError('動線設定が存在しない工程を参照しています')
        if self.route_config and any(edge_id not in edge_ids for edge_id in self.route_config.edge_styles):
            raise ValueError('線種設定が存在しない接続を参照しています')
        dumped = self.model_dump()
        by_step, _ = material_flow(dumped)
        for step in self.steps:
            work = step.daily_work
            if work is None: continue
            ids = set(by_step[step.id]); placed = []
            if not self.daily_work_source or step.basis.get('agent_daily_question') != self.daily_work_source or step.basis.get('review') != 'user-confirmed':
                raise ValueError('当日の作業記録には質問に対応した利用者確認が必要です')
            if len(ids) > 1 and not work.grouping_question_id:
                raise ValueError('複数食材は一緒・別々の確認が必要です')
            for placement in work.placements:
                placed.extend(placement.ingredient_ids)
                if not placement.equipment.id or not placement.equipment.type:
                    raise ValueError('当日の作業記録に使用設備がありません')
                if step.equipment.type and placement.equipment.type != step.equipment.type:
                    raise ValueError('食材ごとの設備種類が工程と一致しません')
            if len(placed) != len(set(placed)) or set(placed) != ids:
                raise ValueError('当日の作業記録の食材に欠落・重複・対象外があります')
            if work.mode == 'individual' and len(ids) != 1:
                raise ValueError('単独作業の対象食材は一つです')
            if work.mode in ('individual', 'together'):
                if len(work.placements) != 1 or step.equipment != work.placements[0].equipment:
                    raise ValueError('一緒に調理した工程の設備が一致しません')
            elif len(work.placements) != len(ids) or any(len(p.ingredient_ids) != 1 for p in work.placements) or step.equipment.id is not None:
                raise ValueError('別々に調理した工程は食材ごとの設備記録が必要です')
        self._validate_material_drawings(by_step)
        return self

    def _validate_material_drawings(self, by_step):
        """Drawing choices may select graph intervals, but cannot invent one."""
        step_order = {step.id: index for index, step in enumerate(self.steps)}
        drawings = {material.id: material.drawing for material in self.ingredients if material.drawing}
        for material_id, drawing in drawings.items():
            references = [*drawing.independent_step_ids, *drawing.shared_step_ids]
            if len(references) != len(set(references)):
                raise ValueError('材料描画の工程参照が重複しています')
            if drawing.merge_step_id and drawing.merge_step_id not in references:
                references.append(drawing.merge_step_id)
            for step_id in references:
                if step_id not in step_order or material_id not in by_step[step_id]:
                    raise ValueError('材料描画が存在しない、または到達不能な工程を参照しています')
            if drawing.merge_step_id and drawing.independent_step_ids:
                if max(step_order[sid] for sid in drawing.independent_step_ids) >= step_order[drawing.merge_step_id]:
                    raise ValueError('材料描画の合流工程は独立工程の後でなければなりません')

        groups = {}
        for material_id, drawing in drawings.items():
            if drawing.mode == 'shared':
                groups.setdefault(drawing.group_id, []).append((material_id, drawing))
        for group_id, members in groups.items():
            if len(members) < 2:
                raise ValueError('共有描画グループには2材料以上が必要です')
            intervals = {tuple(d.shared_step_ids) for _, d in members}
            if len(intervals) != 1:
                raise ValueError('共有描画グループの共有工程が一致していません')
            material_ids = {material_id for material_id, _ in members}
            interval = next(iter(intervals))
            for step_id in interval:
                if not material_ids <= set(by_step[step_id]):
                    raise ValueError('共有描画工程が全材料の共通処理ではありません')
            # All members must reach the SAME graph work above. Merely using
            # equal equipment types in different work nodes does not qualify.
            # Legacy common cooking nodes need not contain processing_mode.

    def check_confirmable(self, *, require_equipment_ids=True):
        if any(m.drawing and m.drawing.status!='user-confirmed' for m in self.ingredients):
            raise ValueError('推定した描画対象を確認してください。下書き保存と動線プレビューは利用できます')
        if any(m.supply and m.supply.inference and m.supply.inference.get('status') != 'user-confirmed' for m in self.ingredients):
            raise ValueError('推定した搬入・保管経路を確認してください。下書き保存と動線プレビューは利用できます')
        if not self.steps or any(not s.operation.strip() or not s.inputs or (not s.equipment.type and s.daily_work is None) for s in self.steps):
            raise ValueError('確認済みにするには各工程の操作・入力・設備種類が必要です')
        if self.schema_version == 2:
            if require_equipment_ids and any((not s.equipment.id or not s.equipment.id.strip()) and s.daily_work is None for s in self.steps):
                raise ValueError('確認済みにするには各工程の具体的な設備IDが必要です')
            if any(s.basis.get('review') != 'user-confirmed' for s in self.steps):
                raise ValueError('確認済みにするには各工程をユーザー確認済みにしてください')
            if any(not output.label.strip() or not output.quantity.strip() for s in self.steps for output in s.outputs):
                raise ValueError('確認済みにするには取り分け名と量が必要です')
        used = {i.ingredient_id for s in self.steps for i in s.inputs if i.ingredient_id}
        if used != {m.id for m in self.ingredients}:
            raise ValueError('工程に使われていない材料があります')
        for s in self.steps:
            if s.outputs:
                used_outputs = {i.output_id for x in self.steps for i in x.inputs if i.step_id == s.id}
                if used_outputs != {o.id for o in s.outputs}:
                    raise ValueError('取り分け後の未接続出力があります')


class PatternMaterialSlot(Strict):
    id: Identifier
    label: Text
    required: bool = True


class PatternFixedEquipment(Strict):
    step_id: Identifier
    equipment: Equipment

    @model_validator(mode='after')
    def concrete(self):
        if not self.equipment.type or not self.equipment.id:
            raise ValueError('固定設備には設備種類と具体的な設備IDが必要です')
        return self


class PatternExecutionChoice(Strict):
    step_id: Identifier
    options: list[Equipment] = Field(min_length=1, max_length=30)

    @model_validator(mode='after')
    def concrete_options(self):
        if any(not item.type or not item.id for item in self.options):
            raise ValueError('実施時設備候補には設備種類と具体的な設備IDが必要です')
        if len({item.id for item in self.options}) != len(self.options):
            raise ValueError('実施時設備候補が重複しています')
        return self


class PatternApplicationProvenance(Strict):
    application_id: Identifier
    pattern_revision_id: int = Field(ge=1)
    material_mapping: dict[Identifier, Identifier] = Field(min_length=1, max_length=150)
    execution_selections: list[PatternFixedEquipment] = Field(default_factory=list, max_length=300)


ProcessDocument.model_rebuild()


class BaseRequest(Strict):
    expected_version: int = Field(ge=0)
    menu_version: int = Field(ge=1)


class SaveRequest(BaseRequest):
    expected_recipe_revision: int = Field(ge=0)
    scope: Literal['menu', 'day']
    status: Literal['draft', 'confirmed']
    document: ProcessDocument
    change_note: str = Field(default='', max_length=2000)


class InferRequest(BaseRequest):
    expected_recipe_revision: int = Field(ge=0)
    document: ProcessDocument


class CorrectionRequest(InferRequest):
    """An explicitly targeted edit proposal.  It is never a save request."""
    instruction: Text
    target_ingredient_ids: list[Identifier] = Field(default_factory=list, max_length=150)
    target_step_ids: list[Identifier] = Field(default_factory=list, max_length=300)
    clarification: str | None = Field(default=None, max_length=2000)

    @model_validator(mode='after')
    def requires_target(self):
        if not self.target_ingredient_ids and not self.target_step_ids:
            raise ValueError('修正対象の材料または工程を指定してください')
        if len(set(self.target_ingredient_ids)) != len(self.target_ingredient_ids):
            raise ValueError('材料の修正対象が重複しています')
        if len(set(self.target_step_ids)) != len(self.target_step_ids):
            raise ValueError('工程の修正対象が重複しています')
        return self


class CorrectionOperation(Strict):
    """Bounded, semantic edits; equipment instance IDs and layout are absent."""
    kind: Literal['replace_ingredient_state', 'replace_material_drawing', 'replace_step_field', 'delete_step', 'add_step']
    ingredient_id: Identifier | None = None
    step_id: Identifier | None = None
    field: Literal['operation', 'inputs', 'equipment', 'conditions', 'outputs'] | None = None
    value: Any = None

    @model_validator(mode='after')
    def operation_shape(self):
        if self.kind in ('replace_ingredient_state', 'replace_material_drawing'):
            if not self.ingredient_id or self.step_id or self.field is not None:
                raise ValueError('材料状態の修正対象が不正です')
        elif self.kind == 'delete_step':
            if not self.step_id or self.ingredient_id or self.field is not None:
                raise ValueError('工程削除の対象が不正です')
        elif self.kind == 'add_step':
            if self.ingredient_id or self.step_id or self.field is not None:
                raise ValueError('工程追加の対象が不正です')
        elif not self.step_id or self.ingredient_id or self.field is None:
            raise ValueError('工程修正の対象が不正です')
        return self


class CorrectionPlan(Strict):
    question: str | None = Field(default=None, max_length=2000)
    choices: list[str] = Field(default_factory=list, max_length=20)
    issues: list[str] = Field(default_factory=list, max_length=100)
    operations: list[CorrectionOperation] = Field(default_factory=list, max_length=100)

    @model_validator(mode='after')
    def question_has_no_edits(self):
        if self.question and self.operations:
            raise ValueError('確認が必要な提案は工程を変更できません')
        return self


class UseRecipeRequest(BaseRequest):
    recipe_id: StrictInt = Field(ge=1)
    expected_selected_recipe_revision: StrictInt = Field(ge=1)
    confirmed_reuse_condition_ids: list[Identifier] = Field(default_factory=list, max_length=300)
