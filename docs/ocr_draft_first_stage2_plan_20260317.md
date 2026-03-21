# OCR Draft-First Stage 2 計画

更新日: 2026-03-17

## 1. 目的

- Stage 1 で導入した `draft-first` 導線を保ったまま、`どの段階で止まっているか` を API と画面で明確にする。
- OCR / LLM の失敗を `全面失敗` ではなく、`再解析中 / 下書きあり / 自動反映保留 / 要手確認` に分解する。
- 既存の `OrderLine` を最後の確定値として維持しつつ、作業者が次に取るべき操作を短く判断できるようにする。

## 2. Scope（Stage 2）

- `GET /orders` と `GET /orders/{id}` のレビュー状態を段階付きメタデータに拡張する。
- `GET /orders/{id}/ocr-sheet` に、blocker/warning の構造化詳細と行数差分を追加する。
- `POST /orders/{id}/reparse` に重複実行ガードを追加し、既に再解析中のときは 409 で返す。
- 既存レスポンスは維持し、新しい項目は追加に留める。

## 3. 対象外

- OCR エンジンや LLM prompt の精度改善そのもの
- DB スキーマ追加や新規テーブル導入
- Queue / worker / Cloud Run の大規模再設計
- frontend の大規模 UI 再配置

## 4. API / 状態モデル変更

### 4.1 注文サマリ

- `ocr_review_stage`
  - `idle`
  - `parsing`
  - `drafting`
  - `needs_human_review`
  - `confirmed`
- `ocr_reparse_status`
  - `idle`
  - `running`
  - `draft_ready`
  - `blocked`
  - `failed`
- `ocr_reparse_last_error_code`
  - 直近の reparse error code
- `ocr_apply_blocker_details`
- `ocr_confirm_blocker_details`
- `ocr_confirm_warning_details`

### 4.2 OCR シート

- 既存の `apply_blockers / confirm_blockers / confirm_warnings` に加えて:
  - `apply_blocker_details`
  - `confirm_blocker_details`
  - `confirm_warning_details`
- 行数比較:
  - `draft_line_count`
  - `confirmed_line_count`
  - `line_count_delta`
  - `line_count_mismatch`
- `review_stage`
- `reparse_status`
- `reparse_last_error_code`

### 4.3 再解析 API

- 既に `OCR-{order_id}` の job が `running / pending` で stale でない場合:
  - `409`
  - `error = reparse_in_progress`
  - `ocr_job_id`
  - `updated_at`

## 5. UI で期待する見え方

- 注文一覧:
  - `ocr_review_state` に加えて `ocr_review_stage` と `ocr_reparse_status` をもとに表示の優先度を決めやすくする。
- 注文詳細:
  - `blockers` のコードだけでなく、理由文をそのまま表示できるようにする。
  - 行数差分を見て「最後の確定明細と、今の下書きがどれだけ違うか」を判断できるようにする。

## 6. テスト計画

- 契約テスト
  - recoverable `ocr-sheet` が構造化 blocker detail と行数差分を返す
  - saved draft を持つ注文が `ocr_review_stage / ocr_reparse_status` を返す
  - 重複 reparse が 409 `reparse_in_progress` で止まる
- 回帰
  - 既存の `draft-first` ケースを壊さない
  - `confirm` blocker の既存挙動を維持する

## 7. 完了条件

- 注文サマリと詳細で `どこで止まっているか` を段階で把握できる
- recoverable な `ocr-sheet` が blocker detail と line count mismatch を返す
- 同一注文への二重 reparse をサーバ側で止められる
- 既存の Stage 1 契約テストが引き続き通る

## 8. ロールアウト

1. backend を先行反映
2. `orders / orders/{id} / ocr-sheet / reparse` の live 応答を spot check
3. Stage 2 の UI 反映が必要なら次段で行う

## 9. リスク

- `reparse_in_progress` を厳しくしすぎると、stale job の再実行がしづらくなる
- detail object 追加で frontend 側が旧実装のままでも壊れないよう、既存配列も残す必要がある
