# OCR Draft-First Stage 3 計画

更新日: 2026-03-17

## 1. 目的

- Stage 1/2 の `draft-first` 運用を壊さず、`stale running reparse` を自動で失敗側へ寄せる。
- `_edited_ocr` キャッシュが消えても、保存済み draft revision を読み戻せる最小の永続化経路を追加する。
- `confirmed lines` を最後の真実として維持しつつ、`draft_ready_blocked` と `hard_failed` を分ける。

## 2. Scope

- `GET /orders` と `GET /orders/{id}` で stale reparse job を検出し、`failed` へ遷移させる。
- `POST /orders/{id}/reparse` の再実行ガードから stale job を外す。
- `save_ocr_sheet_exact()` の revision を cache に加えて DB にも dual-write する。
- `_edited_ocr` キャッシュが無いとき、history / draft summary は persisted revision を read fallback する。

## 3. 実装方針

### 3.1 stale reparse hardening

- stale 判定:
  - `OCR_JOB_STALE_MINUTES` を使う
  - 対象は `OCR-{order_id}` の reparse job のみ
- stale 時の job 更新:
  - `status = failed`
  - `error_message = reparse_stale_timeout>{minutes}m`
  - `metrics.processing_stage = stale_timeout`
  - `metrics.result_state = draft_ready_blocked | hard_failed`
  - `metrics.confirmed_lines_retained = true|false`
- recoverable draft がある場合は `draft_ready_blocked` を優先する

### 3.2 OCR revision dual-write/read-path

- 新規 table:
  - `order_ocr_revisions`
- write:
  - `_append_edited_ocr_revision()` から cache と並行して保存する
- read:
  - `_select_order_sheet_revision()` は cache 優先、無ければ persisted revision
  - `get_ocr_edit_history()` は cache 無しでも persisted revision を返す

## 4. 非目標

- UI の大きな変更
- revision テーブルを中心にした全面再設計
- OCR エンジン / LLM prompt の精度改善

## 5. テスト計画

- stale running reparse が `GET /orders` / `GET /orders/{id}` で `stale_timeout` に変わる
- stale reparse は `POST /orders/{id}/reparse` の再実行を塞がない
- `_edited_ocr` cache を消しても persisted revision から draft summary/history が読める
- Stage 1/2 の `draft-first` 契約テストが継続して通る

## 6. 完了条件

- `running` のまま放置された stale reparse が `draft_ready_blocked` か `hard_failed` に着地する
- stale job のせいで再解析ボタンが永久に塞がらない
- cache 消失時も保存済み draft revision を API が返せる
- 既存の confirmed lines は保持される
