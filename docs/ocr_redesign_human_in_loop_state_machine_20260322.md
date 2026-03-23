# OCR/注文処理 再設計仕様 v2

作成日: 2026-03-22  
対象: OCR パイプライン、注文詳細 Step2/Step3、施設/週解決、LLM 補完再解析、確定フロー  
位置づけ: 現行の個別バグ修正方針をやめ、状態遷移・source of truth・human-in-the-loop を含めて根本から再設計するための正式設計文書

---

## 0. この文書の目的

この文書の目的は、次の 3 つを同時に満たすことです。

1. OCR や LLM が不完全でも、ユーザーに壊れた状態を見せない
2. システムが自動で安全に決められないときだけ、ユーザーに最小限の介入を求める
3. `OCR evidence / draft / confirmed / telemetry` の責務を完全に分離し、状態破壊を構造的に止める

今回の主目的は OCR 精度の最大化ではありません。主目的は、`不完全な OCR/LLM を前提にしても壊れない状態機械` を作ることです。

---

## 1. 現状の本質問題

### 1.1 問題は精度ではなく状態モデル

現行システムの根本問題は、OCR の読取り精度だけではありません。次の問題が組み合わさっています。

- source of truth が複数ある
- Step2 と Step3 の責務が混線している
- OCR evidence の required 条件が用途別になっていない
- telemetry を業務状態として扱っている
- 複数候補がある状態を持てない
- fallback が強すぎて bad data を真実化する

### 1.2 具体的に何が壊れているか

現行では、同一注文が次のような矛盾状態を取り得ます。

- `ocr_status=done`
- `result_state=hard_failed`
- `confirmed_lines_retained=true`
- `/ocr-pages` は見える
- `/ocr-sheet` は `review_blocked`
- Step2 では `confirmed_lines` から復元した表を見せる

これは OCR の誤読ではなく、状態遷移とデータ責務の破綻です。

---

## 2. 実例から見えた failure classes

この再設計は個別注文向けではありません。以下の failure class を設計で潰すためのものです。

### 2.1 Evidence / Preview Failure

- 例: `ORD032433a2`, `ORD37ff2bcf`
- 症状:
  - `ocr_status=done` なのに `/ocr-pages` が失敗
  - preview artifact 欠損
  - raw PDF fallback
- 原因:
  - request path で重い再計算
  - artifact 欠損と incomplete 状態の混同

### 2.2 Confirmed Lines Backflow

- 例: `ORD71873bb1`, `ORD8931bb3e`
- 症状:
  - bad `OrderLine` が Step2 に逆流
  - stale / failed / confirmed が混線
- 原因:
  - Step2 が evidence-only ではない
  - `OrderLine` を fallback に使っている

### 2.3 Template / Layout-Family Mismatch

- 例: `ORD15b74603`, `ORDc71dce69`
- 症状:
  - 施設区分や列 family が壊れる
  - page correction warp が別 template を使う
- 原因:
  - template resolution が独立 stage ではない
  - confidence と candidate の保存がない

### 2.4 Upstream OCR Cell Corruption

- 例: `ORDd2f601d8`
- 症状:
  - merged numeric corruption
  - `6\n9`, `3\n3` などの崩れ
- 原因:
  - downstream parser 問題ではなく upstream OCR/segmentation 問題

### 2.5 LLM Recall / Semantics Drift

- 例: `ORDbabf3c73`, `ORDb266d5d9`, `ORD1defabff`
- 症状:
  - row tail omission
  - column semantics drift
  - special diet drift
- 原因:
  - LLM が sheet 全体の再構築を担いすぎている

### 2.6 Geometry / Warp Mismatch

- 例: `ORD71873bb1`, `ORDc6003bba`
- 症状:
  - overlay 歪み
  - ROI geometry brittle
- 原因:
  - page correction と template resolution が分離していない

### 2.7 Partial-State / State-Corruption Failure

この再設計で一番重要なのは、`完全失敗` ではなく `中途半端に成立して見える失敗` を first-class に扱うことです。

特に設計へ正式に取り込むべき壊れ方は次です。

- `semantic shell only`
  - semantic な `日付 / 区分 / メニュー` は作れる
  - しかし数量はまだ信用できない
- `semantic_rows_numeric_untrusted`
  - semantic rows はある
  - ただし `template_resolution / grid_metadata / quantity evidence` が足りず、数量投影は禁止
- `new_evidence_available`
  - rerun/recover は成功した
  - しかし current draft は保持されており、自動上書きしてはならない
- `decision_invalidated_by_new_evidence`
  - 施設/週/テンプレ/列/数量の選択が、古い evidence run に紐づいている
  - 新 evidence では再選択が必要

典型的な危険パターン:

- `legacy payload + missing template_resolution + weekly_menu shell + payload rescue`
- `template unresolved + payload rows present + semantic bootstrap`
- `new evidence available + old draft current`
- `patch candidate from stale draft lineage`
- `facility/week ambiguity + early grouping`

この failure class は「一般的に起こりうる」ものとして扱う。個別例外として扱ってはならない。

---

## 3. 再設計の大原則

### 3.1 絶対ルール

以下は例外なしで守る。

1. Step2 は `OrderLine` を読んではならない
2. `OrderLine` は confirmed 後にしか書いてはならない
3. request path で OCR / grid detection / overlay 再生成 / 高 DPI render をしてはならない
4. OCR evidence は OCR 完了時に immutable に確定保存する
5. `required_artifacts` ではなく `capabilities` で readiness を判定する
6. LLM は patch candidate しか返してはならない
7. apply 判定は 1 箇所の gate に集約する
8. telemetry は業務状態として使ってはならない
9. recovery 方法をユーザーに選ばせてはならない
10. ユーザーに聞くのは critical ambiguity だけに限定する

### 3.2 source of truth の再定義

今後の truth は次の 4 層です。

- `OCR Evidence`
- `Draft Sheet`
- `Confirmed Snapshot / OrderLine`
- `Workflow State`

これ以外のものは truth として扱わない。

---

## 4. 目標アーキテクチャ

### 4.1 データ責務

#### A. OCR Evidence Run

責務:

- OCR 完了時に保存される immutable な証拠束
- request path では読み取り専用

必須項目:

- `order_id`
- `run_id`
- `schema_version`
- `producer_version`
- `pages`
- `overlay_pages`
- `table_raw`
- `tables`
- `quantity_subgrid`
- `template_resolution`
- `grid_metadata`
- `page_correction`
- `artifact_manifest`
- `artifact_digest`
- `capabilities`
- `degraded_reasons`
- `status` (`building | ready | failed`)

保存先候補:

- 新テーブル `order_ocr_evidence_runs`
- もしくは DB index + GCS object の組み合わせ

#### B. Draft Sheet

責務:

- Step2 / Step3 の唯一の作業面
- manual edit と LLM patch の保存

必須項目:

- `draft_id`
- `order_id`
- `base_evidence_run_id`
- `base_template_resolution_id`
- `base_menu_snapshot_id`
- `draft_sheet_json`
- `draft_state` (`draft | blocked | ready_for_apply`)
- `blockers`
- `warnings`
- `edited_by`
- `edited_at`
- `latest_patch_candidate_id`

保存先候補:

- 新テーブル `order_sheet_drafts`

#### C. Confirmed Snapshot / OrderLine

責務:

- confirm 後の業務真実
- downstream output の唯一の source

必須項目:

- `confirmed_snapshot_id`
- `order_id`
- `draft_id`
- `snapshot_digest`
- `confirmed_at`
- `confirmed_by`

`OrderLine` はこの snapshot から materialize される。

#### D. Workflow State

責務:

- operator に見せる状態の唯一の truth
- telemetry ではない

必須項目:

- `order_id`
- `evidence_run_id`
- `draft_id`
- `confirmed_snapshot_id`
- `state`
- `headline`
- `primary_action`
- `secondary_actions`
- `blockers`
- `warnings`
- `confidence_band`
- `last_transition_at`

保存先候補:

- 新テーブル `order_workflow_state`

---

## 5. 用途別 capability

現行の `required_artifacts` 一律判定を廃止し、用途別 capability に切り替える。

### 5.1 `step2_view_ready`

Step2 で OCR 内容を確認できる状態。

必須:

- `pages` または raw PDF fallback
- `overlay_pages` または表示可能な page preview
- `table_raw` または structured `tables`
- `facility template snapshot`

不要:

- `quantity_subgrid`
- `template_resolution`
- `grid_metadata`
- `corrected_pdf`

### 5.2 `step2_edit_ready`

Step2 で draft sheet を編集できる状態。

必須:

- `step2_view_ready`
- draft skeleton を組むのに十分な context
  - menu snapshot
  - template snapshot
  - date anchors

### 5.3 `apply_ready`

Step3 で `明細へ反映` 可能な状態。

必須:

- `step2_edit_ready`
- `template_resolution`
- `grid_metadata`
- numeric evidence
  - `quantity_subgrid` または同等の数値根拠
- central apply gate pass

### 5.4 `confirm_ready`

最終 confirm 可能な状態。

必須:

- `apply_ready`
- applied draft が存在
- stale conflict なし
- unresolved blocker なし

### 5.5 追加 capability

中間状態を安全に扱うため、以下を capability として持つ。

- `semantic_shell_only`
  - semantic rows は構成できるが、数量や列意味はまだ信用できない
- `numeric_trust_low`
  - quantity evidence が弱く、数量は review または rerun が必要
- `rerunnable`
  - current draft/confirmed を保持したまま、新しい evidence candidate を作れる
- `switch_candidate_available`
  - current draft とは別に、新しい evidence run が存在する
- `legacy_editable`
  - schema version は古いが、Step2 view/edit までは許容できる

---

## 6. 状態遷移

### 6.1 内部状態

- `uploaded`
- `evidence_building`
- `evidence_ready`
- `evidence_ready_raw`
- `evidence_ready_semantic_shell_only`
- `candidate_resolution`
- `choice_required`
- `draft_building`
- `draft_ready`
- `draft_blocked`
- `recovery_required`
- `rerun_in_progress`
- `new_evidence_available`
- `rerun_failed_keep_current`
- `apply_ready`
- `applied_unconfirmed`
- `confirmed`
- `failed_recoverable`
- `failed_hard`
- `stalled`

### 6.2 UI に見せる operator state

ユーザー向けには次だけ見せる。

- `確認待ち`
- `修正待ち`
- `選択待ち`
- `復旧待ち`
- `再取得中`
- `新しいOCR候補あり`
- `反映待ち`
- `確定可能`
- `確定済み`

### 6.3 重要な遷移規則

#### `uploaded -> evidence_building`

- upload 成功時に遷移

#### `evidence_building -> evidence_ready`

- Evidence Run の保存完了
- required ではなく capability 計算まで終わる

#### `evidence_ready -> candidate_resolution`

- facility / week / template / column mapping の候補生成

#### `candidate_resolution -> choice_required`

- confidence 差が小さく、自動決定が危険な場合

#### `candidate_resolution -> draft_building`

- facility / week / template が決まり、draft 生成可能

#### `draft_building -> draft_ready`

- current evidence だけで draft が組めた

#### `draft_building -> draft_blocked`

- evidence はあるが apply-safe ではない

#### `evidence_ready -> evidence_ready_semantic_shell_only`

- preview / menu / template snapshot により semantic rows は組める
- ただし `template_resolution / grid_metadata / quantity evidence` が不足
- この状態では数量投影をしてはならない

#### `* -> rerun_in_progress`

- `OCRパイプライン再実行` を開始
- current draft / confirmed snapshot は保持
- 新しい `evidence_run` を candidate として構築

#### `rerun_in_progress -> new_evidence_available`

- rerun 成功
- current draft はそのまま
- latest evidence run と draft の `base_evidence_run_id` が異なる
- ユーザーには
  - `現在のシートを維持`
  - `新しいOCR候補に切替`
  の 2 択だけを提示

#### `rerun_in_progress -> rerun_failed_keep_current`

- rerun 失敗
- current draft / confirmed は不変
- operator には failure を見せるが、作業面は巻き戻さない

#### `draft_ready -> apply_ready`

- draft validation pass

#### `apply_ready -> applied_unconfirmed`

- 明細へ反映済み
- ただしまだ confirm 前

#### `applied_unconfirmed -> confirmed`

- confirm 実行

#### `* -> recovery_required`

- OCR evidence が壊れた
- artifact 欠損
- digest mismatch
- template resolution 再取得が必要

#### `* -> stalled`

- async job が停止

#### `failed_recoverable -> recovery_required`

- 再実行可能

#### `failed_hard`

- 自動処理不能
- internal review or limited user choice

---

## 7. Candidate Resolution Layer

この層を新設する。ここが現行に無い最重要レイヤです。

### 7.1 candidate type

- `facility_candidates`
- `week_candidates`
- `template_candidates`
- `column_mapping_candidates`
- `critical_quantity_candidates`

### 7.2 candidate 共通項目

- `candidate_id`
- `candidate_type`
- `value`
- `confidence`
- `reason`
- `evidence_ref`
- `decision_source`
- `auto_selectable`
- `requires_user_choice`

### 7.3 decision policy

候補に対して常に 3 択で決める。

- `auto_accept`
- `ask_user`
- `block`

この判定は `candidate_resolution_service` のみが行う。

### 7.4 critical decision の束縛

critical decision は、常に次へ束縛される。

- `order_id`
- `decision_type`
- `candidate_set`
- `selected_value`
- `base_evidence_run_id`
- `base_draft_id` (必要時)

新しい evidence run が current draft とずれた時は、古い decision を自動再利用してはならない。`decision_invalidated_by_new_evidence` として再判定する。

---

## 8. 人間介入の原則

### 8.1 ユーザーに聞いてよいもの

critical ambiguity に限定する。

1. 施設候補が 2 つに割れた
2. 週候補が 2 つに割れた
3. 列マッピングが 2 候補で割れた
4. 高影響セルの数字が 2 候補で割れた

### 8.2 ユーザーに聞いてはいけないもの

- `OCR基盤を復旧` か `OCR再実行` か `LLM補完再解析` か
- stale / hard_failed / recovery_required の違い
- overlay や manifest の内部状態
- telemetry の意味

追加:

- rerun が失敗した時に recovery へ行くか再 rerun するか
- fallback を許すかどうか
- bad patch candidate を採用するかどうか

### 8.3 ユーザー介入の設計制約

- 1注文につき原則 1 回、多くても 2 回
- 選択肢は原則 2 件、多くても 3 件
- 同じ facility/layout family で同じ回答が続く場合は、以後自動化候補に格上げする

### 8.4 新しいOCR候補への切替

`OCRパイプライン再実行` を常時可能にする場合、operator に聞いてよい追加選択は 1 つだけである。

- `現在のシートを維持`
- `新しいOCR候補に切替`

この選択は recovery 手段の選択ではなく、`candidate evidence adoption` の選択である。よって許可する。

禁止:

- rerun 成功時に current draft を自動上書きすること
- confirmed snapshot / OrderLine を自動更新すること

---

## 9. LLM の役割

### 9.1 施設/週推論専用 LLM stage

これは一般 reparse と分ける。

入力:

- first-pass OCR text
- `table_raw`
- facility 候補一覧
- week 候補範囲
- received_at
- hint

出力:

- `facility_candidates`
- `week_candidates`
- confidence
- reason
- `requires_user_choice`

禁止:

- 直接 `facility_id` や `week_id` を確定値として書くこと

### 9.2 LLM 補完再解析

現行の自由入力一本をやめ、`preset_id + optional free text` にする。

推奨 preset:

1. `column_missing`
   - 列欠落・列意味の補正
2. `row_alignment`
   - 行ずれ・ブロックずれの補正
3. `numeric_verification`
   - 数字確認優先
4. `special_diet_semantics`
   - 特食・禁食の意味優先
5. `freeform`
   - 例外対応

### 9.3 LLM の出力制約

LLM は `patch candidate` のみ返す。

許可:

- quantity cell の `replace`

禁止:

- sheet 全体の真実化
- template / facility / week の暗黙確定
- stale draft に対する無条件 patch
- quantity が不確かな状態での構造 rewrite

---

## 13. Failure Taxonomy Deep Dive

状態を壊す状況は「実際に起きたか」ではなく、「起きた時に invariant を破るか」で分類する。

### 13.1 Evidence 構築系

- partial OCR payload 保存
- overlay/pages 欠損
- corrected PDF だけ欠損
- quantity_subgrid 欠損
- template_resolution / grid_metadata 欠損
- read-time 再生成

安全状態:

- `evidence_unusable`
- `raw_only_recoverable`
- `semantic_shell_only`

### 13.2 Semantic 汚染系

- `weekly_menu` はある
- `payload rows` もある
- そこで数量 rescue を走らせる

安全規則:

- `template semantics` が無い時は数量投影禁止
- `semantic shell` と `trusted quantity` を別状態にする

### 13.3 Lineage / Draft 汚染系

- stale tab save
- old draft resurrection
- rerun 後の auto-switch
- stale patch candidate apply

安全規則:

- draft は `base_evidence_run_id` 必須
- patch candidate は `base_evidence_run_id/base_draft_id` 必須
- stale lineage からの保存は 409

### 13.4 Candidate Collapse 系

- facility/week/template/column/quantity 候補を silent auto-accept

安全規則:

- 2 候補競合時は collapse しない
- `choice_required` へ送る

### 13.5 Apply / Confirm / Output 汚染系

- apply と confirm が別 truth を見る
- downstream output が draft と confirmed を混ぜる

安全規則:

- `apply/confirm/downstream` は confirmed lineage だけを見る

---

## 14. Always-Available OCR Rerun Policy

`OCRパイプライン再実行` は常時可能にしてよい。ただし意味は「現在の作業中シートの置換」ではなく「新しい evidence candidate の生成」である。

### 14.1 rerun の正しい意味

- current draft は保持
- confirmed snapshot / OrderLine も保持
- rerun は新しい `evidence_run` を別に作る
- 成功後は `new_evidence_available`
- adopt は明示的な `switch-evidence` でのみ行う

### 14.2 禁止事項

- rerun 成功で current draft を自動上書き
- rerun 成功で confirmed snapshot / OrderLine を自動更新
- rerun と recover と LLM を operator に選ばせる

### 14.3 UI で見せるべき最小導線

- `semantic_shell_only`
  - 主: `OCRパイプラインを再実行`
  - 副: `OCR基盤を復旧`
- `rerun_in_progress`
  - 進行表示のみ
- `new_evidence_available`
  - 主: `新しいOCR候補を確認`
  - 副: `現在のシートを維持`

---

## 15. 設計上の追加 invariant

追加で厳守する。

1. `semantic shell` と `trusted quantities` を同一視してはならない
2. current draft と new OCR candidate は別物である
3. critical decision は evidence run に束縛される
4. rerun/recover/reparse は current draft/confirmed を即時に壊さない
5. old payload には schema-version-aware readiness を使う

- 行追加
- 行削除
- 列追加
- 列削除
- 列移動
- menu/daypart/date の新規生成
- confirmed lines の直接生成

---

## 10. apply gate

`central_apply_gate` を新設し、次を一元判定する。

### 10.1 blockers

- `evidence_missing_for_apply`
- `template_mismatch`
- `template_confidence_low`
- `column_mapping_ambiguous`
- `critical_quantity_ambiguous`
- `structural_projection_detected`
- `sheet_column_anomaly`
- `stale_conflict`
- `first_pass_ocr_missing`
- `hard_failed`

### 10.2 ルール

- blocker が 1 つでもあれば `pass=false`
- `pass=false` でも current confirmed data を壊さない
- `pass=false` でも current draft を上書きしない
- `OrderLine` へ書くのは gate pass 後の explicit apply だけ

---

## 11. いまのシステムで状態を壊しうる状況

実際に起きたかどうかは問わず、設計上 state corruption を起こしうるものはすべて対象にする。

### 11.1 Evidence

- partial OCR payload 保存
- old payload に new required を後付け
- overlay/pages と table_raw の不整合
- request path 再生成
- template mismatch warp
- digest mismatch

### 11.2 Candidate

- facility 候補競合
- week 候補競合
- template 候補競合
- column mapping 候補競合
- quantity 候補競合

### 11.3 Draft

- Step2 が confirmed lines を読む
- draft store と cache revision の split-brain
- stale tab の保存
- manual edit と auto patch の race
- menu missing fallback を真実化

### 11.4 Confirmed

- confirm 前の `OrderLine` 更新
- failed reparse の line 保存
- Step2 への逆流
- provenance 不明の confirmed data

### 11.5 Workflow / Telemetry

- job metrics を UI truth にする
- stale timeout が業務状態を上書き
- recovery と editing の混線
- UI が internal code を直接解釈

### 11.6 Concurrency

- 複数人同時編集
- stale save
- duplicate reparse
- OCR rerun と LLM rerun の競合

### 11.7 Output

- draft 由来の日別出力
- facility/week 未確定で一覧へ流入
- old confirmed lines ベースの bags/total

---

## 12. 実装フェーズ

### Phase 1

- `Step2 must never use OrderLine as input again`
- `OrderOcrCache` を evidence cache に限定
- evidence/capability/version の枠を導入

### Phase 2

- `order_ocr_evidence_runs`
- `candidate_resolution_service`
- facility/week/template candidate 管理

### Phase 3

- `order_sheet_drafts`
- draft を `_edited_ocr` から分離
- Step2/Step3 を draft-only に切替

### Phase 4

- `order_workflow_state`
- UI は workflow state だけを見る
- internal state 計算を backend へ集約

### Phase 5

- `central_apply_gate`
- `OrderLine` 書き込みを explicit apply/confirm のみへ制限

### Phase 6

- critical ambiguity UI
- preset-based LLM assist
- legacy payload backfill / mapper

---

## 13. テスト方針

### 13.1 不変条件テスト

- Step2 が `OrderLine` を読まない
- `OrderLine` は confirm 前に更新されない
- stale telemetry が workflow state を壊さない
- old payload でも false block しない

### 13.2 Candidate resolution テスト

- facility candidate 2択時に `ask_user`
- week candidate 2択時に `ask_user`
- confidence 高なら `auto_accept`

### 13.3 Apply gate テスト

- blocker が 1 つでもあれば apply 不可
- patch candidate が non-quantity mutation を含めば reject

### 13.4 Regression seeds

最低限:

- `ORD71873bb1`
- `ORD032433a2`
- `ORD15b74603`
- `ORD8931bb3e`
- `ORDd2f601d8`
- `ORDbabf3c73`

---

## 14. 最重要メッセージ

この再設計で本当に直したいのは、OCR そのものではありません。

直したいものはこれです。

- `複数候補が存在する`
- `不完全な evidence がある`
- `ユーザーが最小限だけ判断する`
- `confirmed truth が Step2 へ逆流しない`
- `システムが recovery mechanics を内部で処理する`

最終的に目指す状態は次です。

- OCR/LLM が imperfect でも壊れない
- 自動決定できるものはシステムが決める
- 危険な曖昧さだけユーザーに短く聞く
- operator は `今やること` だけを見る

この文書の中で、最も重要な不可逆ルールは 3 つです。

1. `Step2 must never use OrderLine as input again.`
2. `Workflow state must not be derived directly from job telemetry.`
3. `Human choice is only for critical ambiguity, never for recovery mechanics.`
