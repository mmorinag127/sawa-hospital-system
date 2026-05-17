# Order Pipeline Lineage Remediation Plan

Date: 2026-05-18

Related documents:

- `docs/strict_atomic_state_design_rules_20260506.md`
- `docs/order_workflow_state_machine_redesign_20260502.md`
- `docs/strict_atomic_code_db_review_20260506.md`
- `docs/strict_atomic_remediation_detail_plan_20260506.md`
- `docs/workflow_v2_confirmed_sheet_edit_path.md`

## Purpose

この文書は、注文処理パイプラインの実装思想、現状の実装で無理が生じている箇所、最低限すぐ直すべき範囲、段階的な実装計画を説明するための整理である。

この文書は計画と進捗を記録する。deploy はこの文書の範囲外であり、別途明示された場合だけ行う。

## One Sentence Summary

注文処理は CRUD ではなく、Step1 から Step4 までの immutable artifact lineage として扱う。

画面表示や GET は lineage を読むだけで、足りない artifact や矛盾を自動補完しない。補修が必要な場合は、明示 repair / migration / command として実行する。

## Pipeline Contract

注文処理は必ず次の順序で進む。

```text
Step1: context confirm
  facility / week / template_version を確定する

Step2: OCR selection
  OCR evidence candidates から selected_ocr_result を1つ選ぶ

Step3: sheet save
  selected OCR result から作った sheet をユーザーが保存する

Step4: output review / final confirm
  saved_sheet_id だけを入力に bagging_result を作る
  bagging_result から output_bundle を作る
  確認済み output_bundle から confirmed_snapshot を作り、OrderLine を materialize する
```

現状UIでは Step4 が「袋分け・出力確認」として分かれて見えるが、ユーザーの業務操作として袋分け結果と出力確認を別々の step / panel にする理由はない。

今後の UI 方針は Step4 を「出力確認」に一本化する。袋分け結果は出力確認を構成する内部セクションとして表示し、別 step として扱わない。

現状実装上の内部操作は以下である。

```text
Step4a: 袋分けを計算
Step4b: 袋分けを確定 / 出力確認を作成
Step4c: 確定して一覧にもどる
```

これを UI 上は以下へ寄せる。

```text
Step4: 出力確認
  - 保存済みシートから出力確認を作成
  - 出力確認内で袋分け・総量・ラベル・日別出力をまとめて確認
  - 問題なければ確定
```

つまり、設計上の artifact chain は `bagging_result -> output_bundle -> confirmed_snapshot` として残すが、画面の操作単位は Step4「出力確認」に一本化する。

この順序は UI の見た目ではなく、artifact の入力関係として固定する。

```text
selected_ocr_result.template_version_id == workflow.template_version_id
saved_sheet.base_evidence_run_id == selected_ocr_result_id
saved_sheet.template_version_id == workflow.template_version_id
bagging_result.source_saved_sheet_id == saved_sheet_id
output_bundle.source_bagging_result_id == bagging_result_id
confirmed_snapshot.output_bundle_id == output_bundle_id
```

この不変条件を満たせない場合、通常表示で補完せず blocker にする。

## Current Implementation Shape

現在の workflow-v2 は正しい概念を持っている。

- `OrderWorkflowState`
- `OrderOcrEvidenceRun`
- `OrderSheetDraft`
- workflow-v2 endpoint
- `OrderConfirmedSnapshot`
- read-only inspection page
- legacy endpoint の多くを 410 化
- GET 中の canonical write を止める read guard

一方で、実装はまだ完全な artifact lineage system ではない。

特に `backend/src/services/order_workflow_v2_service.py` が以下を同じ service 内で扱っている。

- workflow read
- workflow command
- projection
- legacy state projection
- repair 的な表示復旧
- bagging payload construction
- output bundle construction
- final confirmation
- confirmed snapshot creation

このため、1つの visible blocker を直すたびに、別の lineage 不整合が別名の blocker として出やすい。

## Where The Current Implementation Is Under Strain

### 1. Bagging and output are stored inside workflow JSON

Current shape:

```text
OrderWorkflowState.secondary_actions_json.workflow_v2.bagging_result
OrderWorkflowState.secondary_actions_json.workflow_v2.output_bundle
OrderWorkflowState.secondary_actions_json.workflow_v2.bagging_result_id
OrderWorkflowState.secondary_actions_json.workflow_v2.output_bundle_id
```

Problem:

- `bagging_result_id` / `output_bundle_id` と payload 本体が分離して欠ける。
- payload が workflow state の mutable JSON に入っているため、artifact としての所有権が曖昧になる。
- confirmed snapshot との lineage を DB constraint で守れない。

Design mismatch:

本来は `bagging_result` と `output_bundle` は immutable artifact であり、workflow state はその artifact id だけを参照する。

### 2. Confirmed state is repaired by GET projection

Current shape:

legacy confirmed data に対して、read path が `apply_ready` や stale blocker を `confirmed` に投影する。

Problem:

- GET は DBを書かないが、表示上の canonical state を補正している。
- `confirmed_snapshot_id` / `bagging_result_id` / `output_bundle_id` の欠損や不一致が、repair ではなく projection 条件で扱われる。
- 条件が増えるほど、実際の DB lineage と画面表示がずれる。

Design mismatch:

lineage 欠損は blocker または repair candidate であり、通常表示で正解化しない。

### 3. Confirmed snapshot lineage is incomplete

Current shape:

`OrderConfirmedSnapshot` は存在するが、bagging / output との関係が snapshot JSON の中に埋まる。DB column として `output_bundle_id` を持たない。

Problem:

- `confirmed_snapshot.output_bundle_id == output_bundle_id` を DB と query で検証できない。
- snapshot row が存在しても、workflow の draft id や JSON 内 payload とずれる可能性がある。
- legacy confirmed の補完判断が service logic に寄る。

Design mismatch:

confirmed snapshot は Step4 final confirm の atomic output artifact であり、どの output bundle を確定したかを明示 lineage として持つ必要がある。

### 4. Step4 final confirm can regenerate materialization

Current shape:

`final_confirm` は `bagging_result.materialization_candidate` がなければ saved sheet から再生成できる。

Problem:

- Step4 の袋分け・出力確認でユーザーが確認した内容と、同じStep4内の確定で materialize される内容 が理論上ずれる。
- Step4 final confirm が output confirmation ではなく、再計算を含む command になる。

Design mismatch:

Step4 の確定操作は、同じStep4内で作成・確認した output review artifact を確定するだけであるべき。再計算が必要なら Step4 の袋分け・出力確認を再実行し、新しい artifact を作る。

### 5. Some GET-style workflow-v2 functions still use get-or-create

Current shape:

`_get_or_create_workflow()` は workflow row を insert する。`get_workflow()` は read-only projection に寄っているが、quad/header review など一部 GET 系で get-or-create が残る。

Problem:

- read guard があるため stg/prod では write がブロックされうる。
- 仕様としても、GET が workflow row を作る可能性を持つ関数に依存している。

Design mismatch:

GET は未初期化なら `not_initialized` / blocker を返すだけにする。

### 6. Legacy frontend code still exists

Current shape:

注文一覧は workflow-v2 に向いているが、旧 detail page には `draft-sheet`, `workflow-state`, `ocr-sheet`, old `confirm` などの呼び出しが残る。backend 側は多くを 410 にしている。

Problem:

- 運用・調査時にどの画面が current workflow の入口なのか混乱しやすい。
- 旧UIコードが残っているため、将来の修正で旧 endpoint を再利用する誘惑が残る。

Design mismatch:

current workflow の UI は workflow-v2 API だけを使う。旧画面は archive/read-only か削除対象として扱う。

## Immediate Minimum Fix Set

すぐ着手すべき最小範囲は、完全な artifact table 化ではなく、これ以上 read projection の例外を増やさないための土台作りである。

### A. Lineage audit

Read-only script または admin-only endpoint で、以下を検出する。

```text
confirmed なのに confirmed_snapshot_id がない
confirmed_snapshot_id はあるが snapshot row がない
snapshot row はあるが order_id が違う
snapshot row はあるが template_version_id が workflow と違う
snapshot row の draft_id が workflow.draft_id と違う
bagging_result_id はあるが bagging_result payload がない
output_bundle_id はあるが output_bundle payload がない
output_bundle.source_bagging_result_id と bagging_result_id が違う
bagging_result.source_saved_sheet_id と saved_sheet_id が違う
saved_sheet.template_version_id が null
saved_sheet.template_version_id が workflow.template_version_id と違う
```

Output:

- order_id
- current workflow state
- missing artifact ids
- mismatch type
- proposed repair type
- whether automatic repair is safe

This audit must not write DB.

Implementation status:

- Added read-only service `backend/src/services/order_pipeline_lineage_audit_service.py`.
- Added CLI `backend/scripts/audit_order_pipeline_lineage.py`.
- Added unit coverage for missing confirmed snapshot rows, missing payloads, template mismatch candidates, and source id mismatch classes.

### B. Legacy confirmed repair command

Audit で automatic repair safe と判定できるものだけ、明示 command / script で補完する。

Allowed repair examples:

- snapshot JSON に bagging/output payload がある場合、workflow meta の欠損 payload を復元する。
- workflow meta に bagging/output payload があり snapshot lineage が足りない場合、confirmed snapshot artifact を再作成する。
- IDだけが欠けているが payload digest から一意に復元できる場合、新しい canonical artifact id を作る。

Forbidden repair examples:

- 複数候補から勝手に1つ選ぶ。
- saved sheet と snapshot の内容が矛盾しているのに片方を採用する。
- template version が不一致なのに現在の active template に寄せる。
- OrderLine から Step3 sheet を復元して current にする。

Repair apply must record:

- actor
- reason
- idempotency key
- before digest
- after digest
- affected artifact ids
- skipped / blocked rows

Implementation status:

- Added repair service `backend/src/services/order_pipeline_lineage_repair_service.py`.
- Added CLI `backend/scripts/repair_order_pipeline_lineage.py`.
- Current automatic repair scope is intentionally narrow:
  - restore missing workflow-v2 `bagging_result` / `output_bundle` payloads from an existing confirmed snapshot JSON for the same order
  - restore missing ids only when the payload itself carries the id
  - write an `audit_logs` record on apply with actor, reason, idempotency key, before digest, after digest, affected artifact ids, skipped rows, and blocked rows
- Current blocked cases:
  - workflow row missing
  - confirmed snapshot id missing
  - confirmed snapshot row missing
  - confirmed snapshot belongs to another order
  - confirmed snapshot does not contain bagging/output payloads

Not implemented yet:

- recreate confirmed snapshot artifact from workflow payload
- reconstruct missing artifact ids from digest
- batch repair across many orders
- schema-level first-class bagging/output artifact tables

### C. Stop adding confirmed projection exceptions

Current emergency projection should be treated as temporary compatibility, not as the model.

New rule:

- `GET /workflow-v2` に confirmed lineage exception を追加しない。
- 追加で出た legacy mismatch は audit/repair 側に分類する。
- blocker を隠す条件を増やす前に、repair 可能かを判定する。

### D. Final confirm digest guard

`final_confirm` が Step4 output artifact と違う内容を materialize しないようにする。

Minimum guard:

- Step4 の袋分け・出力確認で作った materialization candidate digest を output bundle に保存する。
- Step4 final confirm で materialization candidate を再生成した場合、その digest が output bundle の digest と一致しなければ block する。
- 一致しない場合は `output_bundle_materialization_mismatch` として Step4 袋分け・出力確認の再実行を要求する。

Goal:

Step4 final confirm は同じStep4内で確認済みの output artifact を確定するだけに寄せる。

Implementation status:

- `bagging_result.materialization_digest` is recorded when Step4 materialization is built.
- `output_bundle.materialization_digest` is carried forward from the bagging result.
- `final_confirm` blocks with `output_bundle_materialization_mismatch` when the current materialization candidate digest no longer matches the reviewed output bundle digest.

### E. Remove get-or-create from GET functions

GET 系 workflow-v2 function は `_get_workflow()` だけを使う。

Required behavior:

- workflow row がなければ `workflow_not_initialized`
- context がなければ `context_not_confirmed`
- template がなければ `template_version_required`
- GET は workflow row を作らない

Targets:

- quad review GET
- header axis review GET
- sheet auto edit status GET
- inspection GET if any creation path is introduced later

Implementation status:

- `get_quad_review()` now returns `workflow_not_initialized` when workflow-v2 state does not exist.
- `get_header_axis_review()` now returns `workflow_not_initialized` when workflow-v2 state does not exist.
- Unit tests cover that these GET paths do not create workflow rows.

## Full Implementation Plan

### Phase 0: Freeze the current emergency compatibility rules

Purpose:

- stg/prod の表示を壊さず、これ以上 projection 例外を増やさない。

Tasks:

- Current confirmed projection exceptions を一覧化する。
- Each exception に `temporary legacy compatibility` comment を付ける。
- New blocker class は projection で消さず audit に出す。
- Regression test で current emergency behavior を固定する。

Exit criteria:

- 同じ注文が blocker 名だけ変えて再発した場合、実装修正ではなく audit/repair backlog に分類できる。

### Phase 1: Add lineage audit

Purpose:

- 既存データの壊れ方を定量化する。

Tasks:

- read-only audit script を追加する。
- stg で実行し、mismatch class ごとの件数を出す。
- prod 実行前に dry-run output format を固定する。
- CI/test fixture で代表的な mismatch を検証する。

Exit criteria:

- `ORD580668f5` 型の mismatch が audit output で説明できる。
- repair safe / blocked の分類ができる。

### Phase 2: Add explicit legacy confirmed repair

Purpose:

- GET projection で救済している legacy confirmed を canonical lineage に寄せる。

Tasks:

- repair dry-run を作る。
- repair apply を作る。
- idempotency key と audit record を残す。
- safe cases だけ apply する。
- blocked cases は operator-visible blocker として残す。

Exit criteria:

- safe legacy confirmed rows は repair 後、projection exception なしで `confirmed` と読める。
- blocked rows は理由つきで止まる。

### Phase 3: Add first-class bagging/output artifacts and unify Step4 UI

Purpose:

- workflow JSON から bagging/output payload を外し、artifact lineage にする。
- UI 上の Step4 を「出力確認」に一本化し、袋分け結果を独立 panel / 独立 step として扱わない。

Schema candidates:

```text
order_bagging_results
  id
  order_id
  source_saved_sheet_id
  template_version_id
  payload_json
  payload_digest
  created_by
  created_at

order_output_bundles
  id
  order_id
  source_bagging_result_id
  source_saved_sheet_id
  template_version_id
  materialization_digest
  payload_json
  payload_digest
  created_by
  created_at
```

Workflow state should keep:

```text
bagging_result_id
output_bundle_id
confirmed_snapshot_id
```

If schema change is not immediately possible, the temporary bridge may keep ids in `secondary_actions_json`, but payload source of truth should move to artifact tables.

Exit criteria:

- Step4 creates `order_bagging_results`.
- output review creates `order_output_bundles`.
- workflow response loads artifact by id.
- missing artifact row becomes blocker.
- frontend Step4 is presented as one output review surface.
- bagging result is visible inside output review, not as a separate workflow step.

Implementation status:

- Added `order_bagging_results` and `order_output_bundles` models.
- Added migration `backend/migrations/0019_order_output_artifacts.py`.
- Added artifact service `backend/src/services/order_output_artifact_service.py`.
- `run_bagging()` now stores bagging/output payloads in artifact tables and keeps workflow-v2 meta as ids plus null bridge payload fields.
- workflow-v2 read/inspection/audit paths load Step4 payloads from artifact ids.
- downstream invalidation deletes Step4 artifacts along with snapshots/materialized output.
- Step4 workflow UI now presents one `出力確認` surface; bagging result is an internal section.
- Added backfill CLI `backend/scripts/backfill_order_output_artifacts.py` for legacy workflow JSON payloads.
- Missing Step4 artifact rows now surface as explicit workflow blockers (`bagging_result_artifact_missing` / `output_bundle_artifact_missing`) instead of being silently treated as ordinary missing output.

Still pending:

- execute stg/prod backfill after migration rollout
- remove legacy workflow JSON payload compatibility after backfill has been verified

### Phase 4: Strengthen confirmed snapshot lineage

Purpose:

- confirmed snapshot がどの output bundle を確定したかを DB 上で追跡できるようにする。

Schema candidates:

```text
order_confirmed_snapshots.output_bundle_id
order_confirmed_snapshots.bagging_result_id
order_confirmed_snapshots.saved_sheet_id
```

Tasks:

- migration で nullable columns を追加する。
- new confirm path では non-null で保存する。
- legacy rows は repair で backfill する。
- future constraint は legacy migration 後に検討する。

Exit criteria:

- `confirmed_snapshot.output_bundle_id == workflow.output_bundle_id` を query で検証できる。

Implementation status:

- Added nullable lineage columns on `OrderConfirmedSnapshot`:
  - `saved_sheet_id`
  - `bagging_result_id`
  - `output_bundle_id`
- Added the columns/indexes/FKs to migration `backend/migrations/0019_order_output_artifacts.py`.
- New workflow-v2 final confirm writes these lineage columns.
- Unit tests assert that the confirmed snapshot row carries saved sheet, bagging result, and output bundle ids.

Still pending:

- stg/prod migration rollout
- legacy confirmed snapshot backfill for the new columns

### Phase 5: Make Step4 final confirm artifact-only

Purpose:

- Step4 final confirm で saved sheet から再生成しない。

Tasks:

- `final_confirm` input を `output_bundle_id` に固定する。
- output bundle に materialization candidate または digest を必須化する。
- digest mismatch は Step4 袋分け・出力確認の再実行 blocker にする。
- `OrderLine` materialization は output bundle の確定内容だけを使う。

Exit criteria:

- Step4で見たものとStep4 final confirmで確定されるものが同じであることを digest で保証できる。

Implementation status:

- `final_confirm` now uses the materialization candidate already stored in the Step4 bagging/output artifact path.
- If the artifact path lacks the materialization candidate, final confirm blocks with `output_bundle_materialization_missing`.
- The previous saved-sheet regeneration fallback inside final confirm has been removed.

### Phase 6: Split services by responsibility

Purpose:

- read / command / projection / repair の境界をコード構造で守る。

Target split:

```text
workflow_read_service
workflow_projection_service
workflow_command_service
workflow_repair_service
ocr_evidence_read_service
ocr_evidence_command_service
sheet_projection_service
sheet_command_service
bagging_command_service
output_command_service
confirmed_snapshot_service
```

Rules:

- read service cannot import command service.
- projection service cannot persist.
- repair service cannot be called from GET.
- command service can call projection/validation helpers.

Exit criteria:

- GET endpoint call graph has no command/repair/write service.
- command endpoint call graph has explicit artifact inputs and outputs.

Implementation status:

- Added dedicated Step4 artifact service `backend/src/services/order_output_artifact_service.py`.
- The workflow service now delegates Step4 artifact persistence/loading/deletion to that service.
- Repair/backfill code remains in `order_pipeline_lineage_repair_service.py` and is only exposed through scripts, not GET.

Still pending:

- Full workflow service split into read / command / projection modules.
- Moving OCR/sheet command boundaries out of `order_workflow_v2_service.py`.

### Phase 7: Retire legacy frontend and backend paths

Purpose:

- current workflow の入口を workflow-v2 に一本化する。

Tasks:

- old order detail page を archive/read-only にするか削除する。
- legacy endpoint 410 を維持し、current UI から呼ばれないことを test で固定する。
- inspection-v2 は read-only projection として残す。

Exit criteria:

- 注文一覧、詳細、inspection が workflow-v2 current source だけを見る。
- old endpoints are not used in current e2e flow.

Implementation status:

- Current Step4 UI no longer calls split `bagging/confirm` or `outputs/review` endpoints.
- Backend split Step4 endpoints now return HTTP 410:
  - `/workflow-v2/bagging/confirm`
  - `/workflow-v2/outputs/review`
- Contract tests assert these endpoints are retired.

Still pending:

- Archive/remove the old non-v2 order detail page after stg parity verification.

### Phase 8: Simplify Step4 interaction model

Purpose:

- Step4 のユーザー操作を「出力確認」に一本化する。
- 袋分け結果と出力確認を別々の panels / actions として見せることで発生する認知負荷と状態分岐を減らす。

Current UI issue:

- `袋分けを計算`
- `袋分けを確定`
- `出力確認を作成`
- `確定して一覧にもどる`

が同じ Step4 内で別々に見えている。

Required change:

- Step4 の主見出しを `出力確認` にする。
- saved sheet から出力確認を作成する action に統合する。
- bagging result は output review の内部セクションとして表示する。
- output bundle がなければ `出力確認を作成` を主 action にする。
- output bundle があれば `確定して一覧にもどる` を主 action にする。
- artifact としての `bagging_result` は残すが、UI step としては露出しない。

Exit criteria:

- operator は Step4 で「袋分け」と「出力確認」のどちらを先に見るべきか迷わない。
- Step4 の workflow state は output review を中心に説明される。
- tests assert that Step4 renders one output review surface.

Implementation status:

- Workflow-v2 Step4 now renders one `出力確認` panel.
- Bagging result is shown inside that output review panel.
- The separate `袋分けを確定` / `出力確認を作成` UI actions were removed.
- Frontend text no longer contains `Step5`, `袋分けを確定`, or the old action function names in the workflow-v2/inspection-v2 pages.

## Risk And Rollout

### Main risks

- legacy confirmed rows の壊れ方が複数あり、automatic repair できないものがある。
- bagging/output artifact 化で daily output や shipping output の参照元が変わる。
- final confirm の digest guard により、これまで通っていた曖昧なデータが blocker になる。
- old UI code が残ると、移行中に旧 endpoint を再利用してしまう可能性がある。

### Rollout order

1. stg audit only
2. stg repair dry-run
3. stg repair apply for safe rows
4. stg workflow-v2 verification
5. prod audit only
6. prod repair dry-run
7. prod repair apply for safe rows
8. artifact table migration
9. new command path rollout
10. remove temporary projection exceptions

Do not deploy artifact table migration and projection exception removal in the same release.

## Verification Checklist

For any root fix in this area, verify:

- `GET /orders/{id}/workflow-v2` performs no canonical writes.
- `GET /orders/{id}/workflow-v2/inspection` performs no canonical writes.
- Step3 cannot run without selected OCR.
- Step4 bagging cannot run without saved sheet.
- Step4 output review cannot run without bagging result.
- Step4 final confirm cannot run without output bundle.
- confirmed cannot be reached without confirmed snapshot.
- selected OCR / saved sheet / bagging / output / snapshot template versions agree.
- stale saved sheet does not block already repaired confirmed state.
- stale saved sheet does block unconfirmed sheet/bagging/output states.
- daily output reads confirmed materialization only.
- no workflow-v2 current path reads old `OrderLine` as Step2/Step3 fallback.

## Communication Summary

The current issue is not one bad blocker name. It is a structural mismatch:

```text
Design:
  Step artifacts are immutable and linked by ids.

Current implementation:
  Some step payloads live in mutable workflow JSON,
  and legacy rows are corrected by read projection.
```

The minimum correction is not to rewrite everything immediately. The minimum correction is:

```text
1. audit the broken lineage
2. repair legacy confirmed rows explicitly
3. stop adding GET projection exceptions
4. add digest checks so Step4 final confirm confirms exactly what Step4 bagging/output review produced
5. remove get-or-create from GET
```

The full correction is:

```text
1. make bagging/output real artifacts
2. strengthen confirmed snapshot lineage
3. make final confirm artifact-only
4. split read/command/projection/repair services
5. retire legacy UI/API paths
```
