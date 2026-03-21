# OCR Draft-First Stage 2 計画

更新日: 2026-03-17

## 1. 目的

Stage 1 では「失敗時でも下書きを失わない」「要レビューで進められる」状態に移行した。
Stage 2 は次を目的とする。

- OCR/LLM が不完全なままでも、注文処理が単一点障害で止まらないことを保証する。
- 再解析の進行状態を段階化し、利用者と運用監視が判断しやすい形にする。
- 重複処理や再現しづらいエラーを抑えるための保守的な防御（duplicate guard）を入れる。

## 2. スコープ

### 対象（In）

- Stage 1 の draft-first フローを前提とした、以下の `state/flow hardening`
  - `ocr 再解析状態` の見える化（draft/ready/blocked/running/failed）
  - `reparse` API の重複実行ガード
  - `確認` API の確定前ガード改善
  - `order一覧/詳細` の表示を状態駆動へ寄せる
- テスト観点として、recoverable/hard-fail の分離を明示

### 対象外（Out）

- OCR エンジン/LLM モデルそのものの置換
- 月次/基準/ルールの施設差分ロジック全面再設計（別タスク）
- 大規模DB再設計（新テーブル追加は Stage 2 では実施しない）
- Cloud Run やインフラの構成変更

## 3. 現状（Stage 1 直後）

- `worker-prod` は Stage 1 をデプロイ済み
  - `worker-prod-00271-q88`
  - `web-prod-00139-fg8`
- 主要実装点
  - reject 結果を `_edited_ocr`（下書き）として保存する
  - recoverable な `/ocr-sheet` は 500/400 で即失敗させず返却
  - `can_apply / can_confirm` と blockers/warnings を API で返却
  - 注文一覧は `下書きあり / 要確認 / OCR失敗` 表示に拡張

## 4. API 変更方針（Stage 2）

### 4.1 `/orders/{id}/ocr-sheet`

- `review_state` を明示的に enum 化する。
  - `none / processing / draft_saved / draft_ready / auto_apply_blocked / confirm_blocked / confirmed / failed`
- `ocr_review_stage` を追加し、どの段階で止まったかを返す
  - `pipeline`, `llm`, `validation`, `mapping`, `persist`, `done`
- `processing_fingerprint` を返す（再解析要求トレーシング用）
- `stage_warnings` を配列化し、UI で固定文言ではなく `label/code/message` で表示
- `draft` と `confirmed` の差分有無を boolean で返す
  - `has_draft`, `draft_is_newer_than_confirmed`

### 4.2 `/orders/{id}/reparse`

- duplicate reparse guard を追加
  - 同一注文で `running` の再解析 job がある場合は新規実行を拒否
  - ただし `force=true` で上書き再実行を許容（監査ログ付き）
- 再解析開始時に `ocr_review_stage='pipeline_start'` を記録
- 既に `draft_ready` があり、再解析が `failed` だった場合は、
  - 現行 draft を保持しつつ、新規再解析を開始（消さない）
- 連続実行時の競合を明示的 `409` 相当で制御

### 4.3 確定系 API（`/orders/{id}/confirm`, `/orders/{id}/apply`, 既存保存 API）

- `confirm` 前提条件を API レベルで明示
  - `review_state=confirm_blocked` の場合は確定不可
- `can_confirm=false` の場合、`confirm_blockers` を必ず返す（`required_weekly_menu` / `draft_out_of_date` / `unresolved_mapping_gaps` など）
- `draft` がある場合の確定フローを2段階化
  - `draft_reconcile`（保存済み下書きを明示的に適用）
  - `confirm`（明確なOK時のみ）

### 4.4 監視・ログ

- 再解析 `start/ok/fail/blocked` のイベントに `review_stage` を追加
- しきい値アラート用に以下を集計
  - running > 180s の停滞数
  - `auto_apply_blocked` 件数
  - duplicate reparse 拒否件数
  - `draft_saved` なのに confirm されない件数

## 5. UI 変更方針

### 5.1 注文一覧（`/orders`）

- ステータスカードを「下書きあり」「要レビュー」「要確認」「エラー停止」に明確分離
- 同一注文で
  - `reparse running`（処理中）
  - `reparse blocked`（要確認）
  - `draft ready`（下書き保存済み）
  を優先表示

### 5.2 注文詳細（`/orders/{id}`）

- OCR 再解析ブロック情報を `警告` と `停止条件` で分離表示
- `シートだけ保存` を第一アクションのまま維持
- `明細へ反映` と `確定` は次の条件を満たす場合のみ有効化
  - `review_state in [draft_ready, confirm_ready]`
  - `can_apply=true`, `can_confirm=true`

### 5.3 オペレーション導線

- まとめアップロード後の初期状態
  - `review_state` が `processing` → `draft_*`/`blocked` と遷移する
- UI は「すぐ確定できない」ものを停止させるより、「どこが未完了か」を明示

## 6. テスト計画

### 6.1 契約/API テスト

- `POST /orders/{id}/reparse`
  - running 重複時は duplicate guard で 409 相当を返却
  - force パスなら新規ジョブが生成される
- `GET /orders/{id}/ocr-sheet`
  - recoverable 警告時は 200 で `review_state/stage` が返る
  - hard fail と draft ready を識別できる
- `GET /orders/{id}`
  - `review_state` / `ocr_review_stage` / `stage_warnings` がレスポンスされる
- `POST /orders/{id}/confirm`
  - `can_confirm=false` 時に確定がブロックされ、blockers が返る

### 6.2 回帰テスト

- Stage 1 で追加された 3件の契約テストを維持
- 追加で以下を増やす
  - duplicate reparse guard
  - confirm blocker precedence
  - draft newer than confirmed で確定抑止

### 6.3 実運用受け入れ

- 3 パターンで確認
  - 正常再解析
  - recoverable なノイズ有り（下書き保存のみ）
  - hard fail（確定ブロック・明確なエラー）
- 各ケースで注文一覧/詳細に状態が正しく反映されること

## 7. ロールアウト

1. Stage 2 ドキュメントを確定し API/実装を分解実施
2. backend から段階デプロイ（確認 API から開始）
3. UI 展開はその後、1〜2日観測後
4. 監視項目のしきい値で「再解析が止まる」「確認不能が長引く」事象を観測

### ロールバック

- API を旧判定へ戻す場合の順序
  1) 再解析 duplicate guard を無効
  2) review state の追加項目を省略
  3) 既存 UI の旧導線へ戻す
- データは draft-first で既存ストレージを再利用しているため、ロールバックでも破壊的なデータ移行は不要

## 8. 完了基準（Go/No-Go）

- `reparse` が重複発行されず、実行順序が追える
- `processing / draft_ready / blocked / failed` が画面・APIで安定表示される
- recoverable 失敗が `500/400` によるフロー停止にならない
- 確定が誤って進まない（blocker 条件が優先される）
- Stage 1 時点の正常ケース回帰が維持される
