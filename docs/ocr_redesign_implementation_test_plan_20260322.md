# OCR/注文処理 再設計 実装・テスト計画

作成日: 2026-03-22  
作業ブランチ: `ocr-redesign-phase-plan-20260322`  
基準仕様: [ocr_redesign_human_in_loop_state_machine_20260322.md](/Users/mmorinag/Sawa/2025.12/workspace/docs/ocr_redesign_human_in_loop_state_machine_20260322.md)

---

## 0. この文書の位置づけ

この文書は、再設計仕様をそのまま実装へ落とし込むための実行計画である。目的は次の 3 つ。

1. フェーズごとに、どのファイル・モデル・API・UI を変えるかを明確にする
2. 各フェーズで必要なテストと受け入れ条件を明確にする
3. 途中段階でも既存運用を壊さない rollout 順を定める

今回は「個別注文の応急修正」ではなく、`state corruption` を構造的に止めるための再設計を段階的に実行する。

---

## 1. 実装前提と非交渉ルール

以下は全フェーズで守る。

1. Step2 は `OrderLine` を入力に使ってはならない
2. `OrderLine` は confirmed 後にしか更新してはならない
3. request path で OCR/grid/overlay を再生成してはならない
4. OCR evidence は OCR 完了時に immutable に保存する
5. telemetry は operator 向け workflow truth に使ってはならない
6. LLM は patch candidate しか返してはならない
7. ユーザーに聞くのは `critical ambiguity` だけに限定する
8. recovery 方法の選択はシステムが決める

---

## 2. 現行実装の責務マップ

### 2.1 現在の主要ファイル

backend:
- [backend/src/services/order_service.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/services/order_service.py)
- [backend/src/api/orders.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/api/orders.py)
- [backend/src/workers/ingest_worker.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/workers/ingest_worker.py)
- [backend/src/services/evidence_manifest_service.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/services/evidence_manifest_service.py)
- [backend/src/services/template_resolution_service.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/services/template_resolution_service.py)
- [backend/src/models/order.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/models/order.py)
- [backend/src/models/order_ocr_cache.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/models/order_ocr_cache.py)
- [backend/src/models/order_ocr_revision.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/models/order_ocr_revision.py)

frontend:
- [frontend/src/pages/orders/[id].tsx](/Users/mmorinag/Sawa/2025.12/workspace/frontend/src/pages/orders/[id].tsx)
- [frontend/src/pages/orders/index.tsx](/Users/mmorinag/Sawa/2025.12/workspace/frontend/src/pages/orders/index.tsx)
- [frontend/src/features/orders/orderDetailOcrUtils.ts](/Users/mmorinag/Sawa/2025.12/workspace/frontend/src/features/orders/orderDetailOcrUtils.ts)
- [frontend/src/features/orders/orderDetailUtils.ts](/Users/mmorinag/Sawa/2025.12/workspace/frontend/src/features/orders/orderDetailUtils.ts)

OCR pipeline:
- [ocr_pipeline/app/main.py](/Users/mmorinag/Sawa/2025.12/workspace/ocr_pipeline/app/main.py)
- [ocr_pipeline/app/page_correction.py](/Users/mmorinag/Sawa/2025.12/workspace/ocr_pipeline/app/page_correction.py)
- [ocr_pipeline/app/quantity_subgrid.py](/Users/mmorinag/Sawa/2025.12/workspace/ocr_pipeline/app/quantity_subgrid.py)

### 2.2 現在の問題の要約

- `OrderOcrCache.payload` が OCR evidence と draft 的情報を併載している
- `OrderOcrRevision` が history と state を兼務している
- `get_ocr_sheet()` が current OCR, edited revision, weekly menu, confirmed lines fallback を混在させている
- `orders/[id].tsx` が workflow UI と debug/system state を同時に抱えている
- facility/week/template 候補を first-class に持たず、heuristic で collapse している

---

## 3. 目標データモデル

### 3.1 新規テーブル

#### A. `order_ocr_evidence_runs`

役割:
- OCR 完了時に保存される immutable evidence bundle

主要カラム:
- `id`
- `order_id`
- `schema_version`
- `producer_version`
- `status`
- `payload_json`
- `artifact_manifest_json`
- `artifact_digest`
- `capabilities_json`
- `degraded_reasons_json`
- `created_at`

#### B. `order_sheet_drafts`

役割:
- Step2 / Step3 の唯一の作業面

主要カラム:
- `id`
- `order_id`
- `base_evidence_run_id`
- `base_template_resolution_id`
- `base_menu_snapshot_id`
- `draft_sheet_json`
- `draft_state`
- `blockers_json`
- `warnings_json`
- `latest_patch_candidate_id`
- `edited_by`
- `edited_at`

#### C. `order_workflow_states`

役割:
- operator に見せる唯一の workflow truth

主要カラム:
- `order_id`
- `evidence_run_id`
- `draft_id`
- `confirmed_snapshot_id`
- `state`
- `headline`
- `primary_action`
- `secondary_actions_json`
- `blockers_json`
- `warnings_json`
- `confidence_band`
- `last_transition_at`

#### D. `order_critical_decisions`

役割:
- facility/week/template/column/quantity の critical ambiguity をユーザーが選んだ結果を保存

主要カラム:
- `id`
- `order_id`
- `decision_type`
- `candidate_set_json`
- `selected_value`
- `selected_by`
- `selected_at`

#### E. `order_confirmed_snapshots` (Phase 5 以降)

役割:
- confirm 時点の確定スナップショット
- `OrderLine` materialize 元

### 3.2 既存テーブルの扱い

- [OrderOcrCache](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/models/order_ocr_cache.py)
  - Phase 1-2 は互換 cache として維持
  - 新規 truth にはしない
- [OrderOcrRevision](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/models/order_ocr_revision.py)
  - sheet edit history 専用へ縮退
- [OrderLine](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/models/order.py)
  - confirm 後 materialize 専用へ変更

---

## 4. 新規サービス構成

新規追加する service は以下を基本とする。

- `ocr_evidence_service.py`
  - OCR payload から evidence run を生成
  - capability を計算
  - legacy payload backfill を担当

- `draft_sheet_service.py`
  - evidence から初期 draft を生成
  - draft の read/write
  - patch candidate の反映

- `workflow_state_service.py`
  - workflow state 導出
  - operator headline / primary_action 生成

- `candidate_resolution_service.py`
  - facility/week/template/column/quantity 候補を保持
  - auto_accept / ask_user / block を返す

- `apply_gate_service.py`
  - apply / confirm の blocker 判定を一元化

- `confirmed_snapshot_service.py`
  - draft から confirmed snapshot を作成
  - `OrderLine` materialize

任意だが強く推奨:
- `ocr_evidence_backfill_service.py`

---

## 5. API 再編方針

### 5.1 新規 API

Phase 1:
- `GET /orders/{id}/workflow-state`
- `GET /orders/{id}/evidence`
- `GET /orders/{id}/draft-sheet`
- `POST /orders/{id}/draft-sheet`

Phase 2:
- `GET /orders/{id}/candidates/facility`
- `GET /orders/{id}/candidates/week`
- `GET /orders/{id}/candidates/template`
- `GET /orders/{id}/candidates/column-mapping`
- `GET /orders/{id}/candidates/quantity`
- `POST /orders/{id}/critical-decisions`

Phase 3:
- `POST /orders/{id}/draft-sheet/apply-patch-candidate`
- `POST /orders/{id}/apply-draft`
- `POST /orders/{id}/confirm`

### 5.2 既存 API の扱い

- `/orders/{id}/ocr-sheet`
  - 互換 API としてしばらく残す
  - 内部実装は `draft-sheet` アダプタへ置換する
- `/orders/{id}`
  - 現行の `ocr_*` フラグ群は徐々に縮退
  - `workflow_state`, `candidate_resolution_summary`, `draft_summary`, `evidence_capabilities` を優先する

---

## 6. フェーズ別実装計画

## Phase 0: 下準備

### 目的
- schema 追加準備
- 既存 truth の依存箇所の明示

### 実装
- 新規モデル定義
  - `backend/src/models/order_ocr_evidence_run.py`
  - `backend/src/models/order_sheet_draft.py`
  - `backend/src/models/order_workflow_state.py`
  - `backend/src/models/order_critical_decision.py`
  - `backend/src/models/order_confirmed_snapshot.py`
- Alembic migration 追加
- 既存 downstream の `OrderLine` 依存箇所洗い出し
  - `output_builder.py`
  - `total` 系 service
  - bags / daily output

### テスト
- migration test
- model create/read smoke

### 完了条件
- 本番挙動は一切変えず、新テーブルを作成できる

---

## Phase 1: OCR Evidence の immutable 化

### 目的
- OCR evidence を read-time 合成ではなく write-time 保存へ変える

### 実装
- `ocr_evidence_service.py` 新規追加
- `ingest_worker.py` で OCR 完了時に `order_ocr_evidence_runs` を保存
- `OrderOcrCache.payload` は互換のため書き続ける
- `evidence_manifest_service.py` を `required_artifacts` 一律判定から `capabilities` 生成中心へ変更
- `template_resolution_service.py` の結果を evidence run 内へ保存

### 変更対象
- [backend/src/workers/ingest_worker.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/workers/ingest_worker.py)
- [backend/src/services/evidence_manifest_service.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/services/evidence_manifest_service.py)
- [backend/src/services/template_resolution_service.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/services/template_resolution_service.py)

### テスト
- OCR 完了時に evidence run が immutable 保存される
- `schema_version` と `artifact_digest` が入る
- request path で evidence が書き換わらない
- overlay 欠損・quantity_subgrid 欠損などでも `capabilities` が用途別に出る

### 完了条件
- `OrderOcrCache` なしでも latest evidence run を取り出せる

---

## Phase 2: Step2 を evidence + draft only にする

### 目的
- `confirmed_lines` 逆流を止める

### 実装
- `draft_sheet_service.py` 新規追加
- evidence から初期 draft を生成
- `get_ocr_sheet()` の内部を置換
- `OrderLine` fallback を完全撤去
- `OrderOcrRevision` は history としてのみ利用
- `draft-sheet` API を追加

### 変更対象
- [backend/src/services/order_service.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/services/order_service.py)
- [backend/src/api/orders.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/api/orders.py)
- `backend/src/services/draft_sheet_service.py`

### テスト
- `hard_failed`, `stale`, `recovery_required` でも Step2 が `OrderLine` を読まない
- `ORD71873bb1` 型 regression
- `confirmed_lines` が壊れていても draft は evidence から構成される
- draft 保存と revision history は両立する

### 完了条件
- Step2 source-of-truth が evidence + draft に固定される

---

## Phase 3: Workflow State 分離

### 目的
- telemetry と operator workflow を完全分離する

### 実装
- `workflow_state_service.py` 新規追加
- `order_workflow_states` 更新ロジック追加
- `/orders/{id}` と `/orders` に `workflow_state` を付与
- stale timeout や `ocr_job.metrics.result_state` を UI truth から外す

### 変更対象
- [backend/src/api/orders.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/api/orders.py)
- `backend/src/services/workflow_state_service.py`

### テスト
- stale telemetry が残っても UI state が壊れない
- `ocr_status=done` と `result_state=hard_failed` の共存時に workflow_state が一意
- 一覧 grouping が workflow_state ベースで可能

### 完了条件
- 画面は `workflow_state` だけで operator 主要導線を組める

---

## Phase 4: Candidate Resolution + Human Choice

### 目的
- 複数候補がある状態を first-class に持つ
- critical ambiguity の時だけユーザーに選ばせる

### 実装
- `candidate_resolution_service.py` 新規追加
- 施設/週/template/column/quantity の candidate set を導入
- `order_critical_decisions` にユーザー選択を保存
- 施設/週 resolver を OCR 後の専用 stage として導入

### 変更対象
- [backend/src/workers/ingest_worker.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/workers/ingest_worker.py)
- [backend/src/services/order_service.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/services/order_service.py)
- `backend/src/services/candidate_resolution_service.py`

### テスト
- facility ambiguity で `choice_required`
- week ambiguity で `choice_required`
- template ambiguity で `choice_required`
- confidence 差が十分ある時だけ auto_accept
- 施設/週候補が一覧表示用に早期解決される

### 完了条件
- Step1 が常時手入力ではなく、候補選択ベースに変えられる

---

## Phase 5: LLM を patch-only にする

進捗:
- 2026-03-22 時点で backend の canonical 保存先を `OrderSheetPatchCandidate + ocr_patch_candidate_service` に統一済み
- `LLM補完再解析 -> patch candidate 保存 -> draft へ適用 API` まで実装済み
- 未完は frontend の preset UI と prompt preset の全面導入

### 目的
- LLM が sheet 全体や order lines を直接作らないようにする

### 実装
- `LLM補完再解析` の backend 出力を patch candidate 形式へ変更
- prompt preset 導入
  - `column_missing`
  - `row_alignment`
  - `numeric_verification`
  - `special_diet_semantics`
  - `freeform`
- candidate を `draft_sheet` へ apply する専用 API 追加

### 変更対象
- [backend/src/services/order_service.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/services/order_service.py)
- [backend/src/api/orders.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/api/orders.py)

### テスト
- LLM 出力が patch candidate 以外を返さない
- column missing preset で列候補だけを返す
- numeric verification preset で高影響セル候補のみ返す
- `ORDbabf3c73`, `ORDb266d5d9`, `ORD1defabff` 型 drift regression

### 完了条件
- LLM は draft を壊さず patch 候補だけを出す

---

## Phase 6: Central Apply Gate + Confirmed Snapshot

進捗:
- 2026-03-22 時点で `apply_gate_service` / `workflow_state` / `confirmed snapshot` の backend 基盤は実装済み
- `confirm` は latest draft から materialization candidate を作って `OrderLine` を更新する経路へ移行済み
- `apply` も `save exact draft -> apply latest draft` に切り替え済みで、legacy の `rows -> parse -> direct OrderLine write` は撤去済み
- 未完は frontend と downstream を `workflow_state` / confirmed snapshot 中心へさらに寄せることと、legacy blocker の削除

### 目的
- apply/confirm 判定を 1 箇所に集約
- `OrderLine` を confirmed 後の materialization に限定

### 実装
- `apply_gate_service.py` 新規追加
- `confirmed_snapshot_service.py` 新規追加
- `order_confirmed_snapshots` を導入
- `apply-draft` と `confirm` の責務分離
- confirm 時にのみ `OrderLine` を materialize

### 変更対象
- [backend/src/api/orders.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/api/orders.py)
- [backend/src/services/order_service.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/services/order_service.py)
- `backend/src/services/apply_gate_service.py`
- `backend/src/services/confirmed_snapshot_service.py`

### テスト
- confirm 前に `OrderLine` が書き換わらない
- blocker 一元化
- `draft_newer_than_lines` 概念が不要になる
- downstream output が confirmed snapshot / OrderLine のみを見る

### 完了条件
- Step2/Step3/confirm の責務分離が完成する

---

## Phase 7: Legacy Cleanup / Backfill / Rollout 完了

### 目的
- 旧 payload との互換を残しつつ、新設計へ完全移行する

### 実装
- `ocr_evidence_backfill_service.py` 新規追加
- `backend/scripts/backfill_ocr_evidence_runs.py` 追加
- 既存注文の evidence run backfill
- `schema_version=v1_legacy` と `v2_native` を分離
- `OrderOcrCache` の責務縮退
- `/ocr-sheet` 旧互換処理の段階的削除

### テスト
- backfill dry-run
- sample orders の spot check
- old payload でも `step2_view_ready` が壊れない
- backfill 不能注文が `recovery_required` に落ちる

### 完了条件
- 旧注文/新注文を version-aware に扱える

---

## 7. frontend 実装計画

## Frontend Phase A: adapter 層導入

### 目的
- 新旧 API 契約を同時に扱えるようにする

### 追加推奨
- `frontend/src/features/orders/orderWorkflowState.ts`
- `frontend/src/features/orders/orderCandidates.ts`

### 実装
- `workflow_state` があればそれを優先
- 無ければ既存 `ocr_*` から暫定 normalize
- `orders/index.tsx` と `orders/[id].tsx` は adapter 経由に寄せる

### テスト
- adapter unit tests
- legacy payload -> normalized state
- new payload -> normalized state

---

## Frontend Phase B: Step1 candidate choice UI

### 実装
- `OrderIdentityResolutionPanel.tsx`
- facility/week の自動確定済み / 推薦 / 選択待ち の3モード
- 自由入力は fallback details に退避

### テスト
- candidate 2件時だけ選択 UI
- 推薦 accepted path

---

## Frontend Phase C: Step2 evidence-only 化

### 実装
- `OcrEvidenceViewer.tsx`
- `DraftSheetEditor.tsx`
- `CriticalChoicePanel.tsx`
- `OcrAdvancedActions.tsx`
- recovery と critical ambiguity を同時に主表示しない

### テスト
- Step2 で `OrderLine` 依存が無い
- recovery required では choice UI を出さない
- apply_ready で primary CTA が一意

---

## Frontend Phase D: LLM preset UI

### 実装
- `LlmRepairPanel.tsx`
- preset-first
- freeform は optional
- provider/model は advanced 内に退避

### テスト
- preset payload 送信
- freeform 表示条件

---

## Frontend Phase E: Step3 draft review 専用化

### 実装
- `DraftReviewPanel.tsx`
- changed rows / blockers / warnings のみ表示
- OCR overlay や recovery は出さない

### テスト
- draft review から apply
- choice_required なら Step2 へ戻す

---

## Frontend Phase F: 注文一覧の workflow 中心化

### 実装
- `orders/index.tsx` を `workflow_state` ベース grouping に変更
- レーン例:
  - `選択待ち`
  - `修正待ち`
  - `反映待ち`
  - `復旧待ち`
  - `処理中`
  - `確定済み`

### テスト
- grouping logic
- badge/headline rendering
- workflow 別カード表示

---

## 8. テスト計画

## 8.1 backend unit / service

新規:
- `test_ocr_evidence_service.py`
- `test_draft_sheet_service.py`
- `test_workflow_state_service.py`
- `test_candidate_resolution_service.py`
- `test_apply_gate_service.py`
- `test_confirmed_snapshot_service.py`

主な観点:
- immutable evidence
- capability 計算
- no backflow
- candidate auto_accept / ask_user / block
- patch-only LLM output
- confirm 前 non-materialization

## 8.2 backend contract

追加:
- `test_order_workflow_state_api.py`
- `test_order_draft_sheet_api.py`
- `test_order_candidate_resolution_api.py`
- `test_order_critical_decisions_api.py`
- `test_order_apply_gate_api.py`

既存流用:
- [backend/tests/contract/test_orders_draft_review_api.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/tests/contract/test_orders_draft_review_api.py)
- [backend/tests/contract/test_orders_ocr_status_api.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/tests/contract/test_orders_ocr_status_api.py)

## 8.3 backend integration / regression

既存流用:
- [backend/tests/integration/test_ocr_sheet_history.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/tests/integration/test_ocr_sheet_history.py)
- [backend/tests/integration/test_ocr_pipeline.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/tests/integration/test_ocr_pipeline.py)
- [backend/tests/integration/test_reparse_job_progress.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/tests/integration/test_reparse_job_progress.py)

追加する regression fixture:
- `ORD032433a2` 型 preview/evidence degraded
- `ORD71873bb1` 型 confirmed backflow
- `ORD15b74603` 型 template mismatch
- `ORD8931bb3e` 型 stale + draft interaction
- `ORDbabf3c73` 型 LLM semantics drift

## 8.4 frontend unit

新規:
- `frontend/src/features/orders/__tests__/orderWorkflowState.test.ts`
- `.../CriticalChoicePanel.test.tsx`
- `.../LlmRepairPanel.test.tsx`
- `.../DraftSheetEditor.test.tsx`
- `.../DraftReviewPanel.test.tsx`

## 8.5 Playwright / E2E

既存:
- [frontend/tests/e2e/order_confirm.spec.ts](/Users/mmorinag/Sawa/2025.12/workspace/frontend/tests/e2e/order_confirm.spec.ts)

追加シナリオ:
- facility choice required
- week choice required
- recovery required
- draft save -> apply
- apply -> confirm
- LLM preset execution

## 8.6 failure injection

最低限必要:
- overlay artifact 欠損
- quantity_subgrid 欠損
- template candidate 競合
- stale telemetry

---

## 9. 直近の修正波: Partial-State / Rerun Candidate 対応

この節は、`ORD71873bb1` 系で露出した「semantic shell only」「常時 rerun」「new evidence available」問題を潰すための具体計画である。

### 9.1 対象にする failure mode

優先度 P0/P1 とする。

- `semantic shell only`
  - semantic rows はあるが数量は信用できない
- `new evidence available`
  - rerun/recover 成功後、新 evidence はあるが current draft は保持されている
- `rerun overwrites current draft`
  - rerun が current draft/confirmed を暗黙上書きする
- `decision invalidated by new evidence`
  - 施設/週/テンプレ/列/数量の選択が、古い evidence run に対するまま残る
- `legacy payload false block`
  - old evidence に new required を当てて不自然に block する

### 9.2 今回の実装スコープ

今回の修正波で最低限入れるもの:

1. `workflow_state` に中間状態を追加
   - `semantic_shell_only`
   - `rerun_in_progress`
   - `new_evidence_available`
   - `rerun_failed_keep_current`
2. `OCRパイプライン再実行` を常時可能にする
3. rerun 成功時に current draft を自動上書きしない
4. `switch-evidence` API を追加し、明示切替でのみ draft を更新
5. `critical_decision` を `evidence_run_id` と照合し、古い選択を無条件再利用しない
6. frontend Step2 を
   - `基盤`
   - `候補`
   - `LLM`
   の 3 ブロックへ整理する

### 9.3 backend 変更詳細

#### A. migration / model

対象:
- [backend/src/models/order_workflow_state.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/models/order_workflow_state.py)
- [backend/src/models/order_ocr_evidence_run.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/models/order_ocr_evidence_run.py)
- `backend/migrations/*`

追加候補:
- `order_workflow_states.candidate_evidence_run_id`
- `order_workflow_states.state_reason`
- `order_ocr_evidence_runs.parent_evidence_run_id`
- `order_ocr_evidence_runs.trigger`

最低限の受け入れ条件:
- new evidence candidate を current evidence と区別して保存できる
- workflow row だけで `new_evidence_available` を表現できる

#### B. evidence capabilities

対象:
- [backend/src/services/ocr_evidence_service.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/services/ocr_evidence_service.py)
- [backend/src/services/evidence_manifest_service.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/services/evidence_manifest_service.py)

追加:
- `semantic_shell_only`
- `numeric_trust_low`
- `rerunnable`
- `switch_candidate_available`
- `legacy_editable`

ルール:
- `template_resolution / grid_metadata / quantity evidence` が無い時は、semantic shell まで
- 数量投影はしない

#### C. rerun / switch flow

対象:
- [backend/src/api/orders.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/api/orders.py)
- [backend/src/services/order_service.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/services/order_service.py)
- [backend/src/services/draft_sheet_service.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/services/draft_sheet_service.py)
- [backend/src/services/workflow_state_service.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/services/workflow_state_service.py)

新規/変更 API:
- `POST /orders/{id}/ocr-rerun`
  - 常時利用可能
  - current draft/confirmed を壊さない
- `POST /orders/{id}/draft-sheet/switch-evidence`
  - latest candidate evidence を採用して draft を再初期化
- 既存 `POST /orders/{id}/ocr-recover`
  - hard recovery 用に維持

ルール:
- rerun は新しい `evidence_run` を candidate として作る
- rerun 成功後は `new_evidence_available`
- adopt は `switch-evidence` でのみ行う
- rerun 失敗時は `rerun_failed_keep_current`

#### D. critical decision binding

対象:
- [backend/src/services/critical_decision_service.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/services/critical_decision_service.py)
- [backend/src/services/candidate_resolution_service.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/services/candidate_resolution_service.py)

ルール:
- decision は `base_evidence_run_id` を持つ
- current latest evidence とずれたら `decision_invalidated_by_new_evidence`
- 再選択が必要

### 9.4 frontend 変更詳細

対象:
- [frontend/src/pages/orders/[id].tsx](/Users/mmorinag/Sawa/2025.12/workspace/frontend/src/pages/orders/[id].tsx)
- [frontend/src/features/orders/orderDetailOcrUtils.ts](/Users/mmorinag/Sawa/2025.12/workspace/frontend/src/features/orders/orderDetailOcrUtils.ts)

実装:
- `workflow_state` に応じた単一 CTA 帯
- `semantic_shell_only`
  - 主: `OCRパイプラインを再実行`
  - 副: `OCR基盤を復旧`
- `rerun_in_progress`
  - 進行表示のみ
- `new_evidence_available`
  - 主: `新しいOCR候補を確認`
  - 副: `現在のシートを維持`
- `LLM補完再解析`
  - `基盤` ブロックの後ろへ配置
  - freeform textarea は専用 panel に分離

### 9.5 テスト matrix

#### backend integration

- `semantic_shell_only` evidence で apply/confirm 不可
- `POST /ocr-rerun` は `done` 注文でも `202`
- rerun 中は重複 rerun が `409`
- rerun 成功で `new_evidence_available`
- current draft がある時も自動上書きしない
- `switch-evidence` 時だけ `base_evidence_run_id` が更新
- stale decision は current evidence に再適用されない

#### backend contract

- `GET /orders/{id}` に新 state が載る
- `GET /orders/{id}/workflow-state` に `candidate_evidence_run_id` 相当が載る
- `POST /orders/{id}/draft-sheet/switch-evidence`
  - success
  - no candidate
  - already current
  - stale choice invalidation

#### frontend

- `semantic_shell_only` で rerun が primary CTA
- `recovery_required` で recover が primary CTA
- `new_evidence_available` で switch/keep の 2 択
- freeform prompt panel が full-width で表示される
- choice_required 時は他 action を抑制

### 9.6 rollout 順

1. migration / model
2. backend evidence capabilities
3. rerun + switch-evidence API
4. workflow_state 拡張
5. frontend Step2 整理
6. targeted tests
7. live spot check

### 9.7 完了条件

- `ORD71873bb1` 型で、壊れた数量投影ではなく `semantic_shell_only` として止まる
- `OCRパイプライン再実行` は常時見える
- rerun 成功後に current draft が自動で消えない
- new evidence を採用するかどうかが operator に明確に見える
- `classifier mismatch` だけでは block せず、resolved template が requested scope 内で安定していて
  template registry から quantity column semantics を供給できる場合は `semantic_shell_only` に落とさない
- `merged_numeric_cell` など high-risk 数量シグナルがある evidence では、
  semantic shell を維持しつつ payload 数量の自動投影を止める
- `ocr-history` は legacy revision が空でも、latest evidence run から最低限の履歴を構成できる

---

## 10. 追加 failure matrix テスト観点

少なくとも以下の組み合わせは regression に入れる。

- `legacy payload + missing template_resolution + weekly_menu shell + payload rescue`
- `bad latest draft + rerun success + no explicit switch`
- `patch candidate + stale draft lineage`
- `facility ambiguity + early grouping`
- `week ambiguity + semantic shell`
- `template mismatch + column ambiguity + numeric rescue`
- `telemetry stale + evidence usable`
- `confirmed exists + current draft blocked`
- bad confirmed lines 既存保持
- LLM patch が row rewrite を返す

---

## 9. rollout 戦略

### Step 1
- schema 追加
- code path 未切替

### Step 2
- evidence persistence を write-only で併記
- read path はまだ旧実装

### Step 3
- Step2 read path を evidence + draft へ切替
- feature flag で戻せるようにする

### Step 4
- workflow_state を UI へ反映
- 一覧/注文詳細を順次 adapter 経由へ移行

### Step 5
- candidate resolution / critical decisions を有効化

### Step 6
- apply gate / confirmed snapshot / legacy cleanup

---

## 10. rollback 基準

即 rollback する条件:
- Step2 が表示不能になる
- draft 保存が不安定になる
- `OrderLine` 非更新のはずが confirm 前に変わる
- 一覧 grouping が壊れて operator が追えなくなる

rollback 方法:
- feature flag で read path を旧実装へ戻す
- migration は残してよいが、新テーブルは unused に戻す

---

## 11. 最初に着手する具体作業

最初の 3 手は固定とする。

1. Phase 0
   - schema 追加
   - 既存 `OrderLine` downstream 依存洗い出し

2. Phase 1
   - `order_ocr_evidence_runs`
   - `ocr_evidence_service.py`
   - ingest/OCR 完了時 evidence 保存

3. Phase 2
   - `order_sheet_drafts`
   - `draft_sheet_service.py`
   - `get_ocr_sheet()` から `confirmed_lines` fallback を撤去

この 3 手をやらない限り、他の UI 改善や LLM prompt 改善を積んでも state corruption は止まらない。

---

## 12. 現時点の判断

最重要ポイントはこれです。

- 最初の本丸は `order_ocr_evidence_runs`
- その次が `Step2 evidence-only`
- その次が `workflow_state`

逆に、これを飛ばして
- LLM prompt を増やす
- UI 文言だけ直す
- 個別注文の fallback を足す
という順で進めると、また同じ種類の事故が出る。

この計画は、`個別修正ではなく、壊れ方そのものを減らす順` で並べている。
