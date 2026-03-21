# OCR Draft-First Stage 1 計画

更新日: 2026-03-16

## 1. 現状の問題

- OCR/LLM は精度改善されているが、失敗時の扱いが `全体失敗` に寄っている。
- `要再解析` など回収可能な結果でも `エラー` 扱いになりやすく、注文者の手作業判断が止まりやすい。
- 現在は `確定明細(OrderLine)` と `下書き候補` の分離が弱く、再解析の結果が崩れると既存の良い状態も巻き戻ししやすい。
- 結果として、`OCR結果は取れている` でも `保存フロー` で落ちるケースが再発しやすい。

## 2. Scope（Stage 1）

- 目的: `精度を壊さず`、失敗時でも継続作業できる状態管理へ移行
- 対象: `注文詳細` の OCR 再解析と OCR シート参照、注文一覧の状態表示、確認フロー
- 対象外（Phase 1 非対応）:
  - OCR/LLM のアルゴリズム追加開発
  - 月次メニューの構造差分（facilityタグなど）の完全再設計
  - DB スキーマの大規模追加（新規テーブル追加）
  - インフラ刷新（Cloud Run/SQL の根本設定変更）

## 3. API/状態モデル変更

### 3.1 状態の分離
- `確認済み明細`（既存の `OrderLine`）を最終真実として維持
- `下書き候補` を別レイヤで保持（既存の `OrderOcrCache` を活用）
- `失敗` を `落とす` ではなく `レビュー待ち` に落とす

### 3.2 API レベルの見直し方針
- `GET /orders/{id}/ocr-sheet`
  - recoverable な再解析結果は `500/400` で断たない
  - `warnings/blockers` と `draft payload` を返却し、レビュー可能状態を明示
- `POST /orders/{id}/reparse`
 - 成功・失敗に関わらず、再解析成果を下書きとして保存
 - ブロック種別を明示（`hard_fail` / `auto_apply_blocked` / `draft_ready`）
- `POST /orders/{id}/confirm` 系
  - `can_confirm` を明示して、不可の場合は `confirm_blockers` を明示

### 3.3 サービス側挙動
- `reparse_order()` は「有効な候補は保存、適用可否は別判定」に変更
- `sheet` の warning は `エラー` から `検知付き継続` に変更
- 既存の `OrderLine` は可能な限り最終既知値を保持し、再解析失敗時でも壊さない

## 4. オペレーションインパクト（ユーザー向け）

- 注文一覧
  - `処理失敗` と `要レビュー下書きあり` を分離表示
  - 処理が止まる状態を減らし、次アクションを提示

- 注文詳細
  - Step2 は `下書き保存` を主導線に変更
  - `明細反映` は確認後のサブアクションに格上げ
  - 警告（warnings）とブロッカー（blockers）を分離表示

- 期待オペレーション
  - 「再解析で落ちる」より「下書きとして取り込まれ、確認して反映」が基本になります

## 5. テスト計画（非コード実装フェーズの必須観点）

- 単体/結合
  - 再解析が失敗しても下書き保存が成立すること
  - `ocr-sheet` が recoverable 問題時に `draft + blockers/warnings` を返すこと
  - `confirm` が `can_confirm=false` の場合に確定しないこと
  - `confirm` が必要情報を返却し、誤確定を防げること

- 回帰
  - 既存の trusted OCR 代表ケースで `confirmed_lines` が不意に変化しないこと
  - `要レビュー` ルートでも画面クラッシュしないこと

- 運用受入（実運用）
  - 1件成功、1件の recoverable エラー、1件の hard fail の3ケースで運用フローを確認
  - ユーザーが「下書き保存→明細反映」で完了できることを確認

## 6. 実装対象（Stage 1）

- backend
  - `backend/src/services/order_service.py`
    - reject した reparse candidate を draft revision として保持
    - `review_state / blockers / warnings` を `ocr-sheet` と注文 summary に反映
  - `backend/src/api/orders.py`
    - recoverable な `ocr-sheet` は payload を返し、確認不可理由を API で返す
  - `backend/tests/contract/test_orders_draft_review_api.py`
    - draft-first 契約テスト

- frontend
  - `frontend/src/pages/orders/[id].tsx`
    - Step2 を `シートだけ保存` 主導線へ変更
    - `明細へ反映 / 確定` は blockers/warnings を見て制御
  - `frontend/src/pages/orders/index.tsx`
    - `下書きあり / 要確認 / OCR失敗` の軽量表示を追加

## 7. 完了条件

- reject された再解析結果が失われず、注文詳細で draft として見える
- recoverable なケースで `ocr-sheet` が 500/400 ではなくレビュー可能 payload を返す
- 注文一覧で `下書きあり / 要確認 / OCR失敗` が見える
- 注文詳細で `シートだけ保存` を優先し、blocker があると `明細へ反映 / 確定` が止まる
- 既存の confirmed lines は、新しい再解析失敗で壊れない

## 8. ロールアウト/ロールバック

- ロールアウト手順
  - API/運用文言を同時に deploy
  - 管理画面で 10〜20件の再解析テストを実施
  - `can_confirm` と `review state` の誤表示を監視

- ロールバック
  - 旧実装と同等挙動へ戻すことが可能なため、順序:
  1) UI の新しい導線を無効化
  2) OCR/確認 API の旧判定に戻す
  3) `draft` ベースの新規導線を一時停止

## 9. 期待されるユーザー目視変更

- 「失敗」表示が減り、`確認必要` の状態表示が増える
- 再解析結果は `落ちる` より `保存→確認` へ移行
- 週次運用（まとめアップロード）での停止率が低下

## 10. 除外

- 本計画は「既存品質を落とさず回復性を上げる」ことが目的で、OCR精度そのものの大幅改修は Phase 2 以降。
- 価格見直し、OCRコスト、インフラ自動スケーリング最適化は別タスク。
