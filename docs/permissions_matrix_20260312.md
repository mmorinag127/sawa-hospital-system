# 権限一覧 2026-03-12

この文書は、現在の `web-prod` / `worker-prod` 実装に合わせて、画面・操作・API の想定権限を整理したものです。

## ロール

- `ユーザー1`
  - 日々の注文確認、OCR確認、シート修正、袋分け、送り状、日別出力を行う人
  - システム上の権限は `operator`
- `ユーザー2`
  - 月次メニュー、注文書生成、注文書アップロード、施設登録・設定を行う人
  - システム上の権限は `operator`
- `管理者`
  - ユーザー管理、システム保守、OCRテンプレート保守、破壊的操作を行う人
  - システム上の権限は `admin`

## 結論

- `ユーザー1` と `ユーザー2` は、どちらも backend 上は `operator` 権限で動きます。
- `ユーザー1 / ユーザー2` は業務上の呼び分けであり、権限を分けるためのロールではありません。
- 今回の監査で、`ユーザー2` ナビにあるのに `admin` しか使えなかった機能は修正済みです。
- `admin` に残しているのは、破壊的操作、DBダウンロード、ユーザー管理、OCRテンプレート保守などです。

## 修正済みの権限ズレ

今回 `operator` に修正した API:

- `/base-menus`
- `/menu-masters`
- `/menu-rules`
- `/facility-master` の保存
- `/monthly-menus`

これにより、`ユーザー2` ナビ配下の主要 CRUD は `operator` で利用できます。

## ユーザー1 が使う画面

### 注文一覧

- 画面: `/orders`
- 権限: `operator`
- 主な API:
  - `GET /orders`

### 注文詳細

- 画面: `/orders/{id}`
- 権限: `operator`
- 主な API:
  - `GET /orders/{id}`
  - `GET /orders/{id}/ocr-output`
  - `GET /orders/{id}/ocr-pages`
  - `GET /orders/{id}/ocr-sheet`
  - `POST /orders/{id}/ocr-apply`
  - `POST /orders/{id}/ocr-sheet-save`
  - `POST /orders/{id}/ocr-review`
  - `PUT /orders/{id}/lines`
  - `POST /orders/{id}/confirm`
  - `GET /orders/{id}/bags`
  - `POST /orders/{id}/bags/rebuild`
  - `POST /orders/{id}/facility`
  - `POST /orders/{id}/week`

### 日別出力

- 画面: `/daily-delivery-notes`
- 権限: `operator`
- 主な API:
  - `GET /orders/by-line-date`
  - `GET /orders/daily-bags`
  - `GET /totals`
  - `GET /outputs/daily-bundle`
  - `GET /outputs/labels`
  - `GET /outputs/delivery-notes`
  - `GET /outputs/manufacturing-aggregate`

### 総量

- 画面: `/totals`
- 権限: `operator`
- 主な API:
  - `GET /totals`

### 施設別注文

- 画面: `/facility-orders`
- 権限: `operator`
- 主な API:
  - `GET /orders`
  - `GET /orders/by-line-date`

### OCR結果

- 画面: `/ocr-results`
- 権限: `operator`
- 主な API:
  - `GET /orders?include_ocr=true`

### 送り状

- 画面: `/shipping`
- 権限: `operator`
- 主な API:
  - `POST /shipping/parse`
  - `POST /shipping/track-status`
  - `POST /shipping/enrich-excel`

### 送り状履歴

- 画面: `/shipping-history`
- 権限: 画面閲覧は `operator`
- 主な API:
  - `GET /shipping/status/history`
- 注意:
  - `DBをCSVで保存`
  - `DBをJSONで保存`
  - `全件クリア`
  は backend では `admin` 限定です

## ユーザー2 が使う画面

### 月次メニュー

- 画面: `/menus/{monthId}`
- 権限: `operator`
- 主な API:
  - `GET /monthly-menus/{month_id}`
  - `POST /monthly-menus`
  - `PUT /monthly-menus/{month_id}/items/{item_id}`
  - `POST /monthly-menus/condiments`

### 基準メニュー

- 画面: `/base-menus`
- 権限: `operator`
- 主な API:
  - `GET /base-menus`
  - `POST /base-menus`
  - `PUT /base-menus/{item_id}`

### メニューマスター

- 画面: `/menu-masters`
- 権限: `operator`
- 主な API:
  - `GET /menu-masters`
  - `POST /menu-masters`
  - `PUT /menu-masters/{item_id}`

### メニュールール

- 画面: `/menu-rules`
- 権限: `operator`
- 主な API:
  - `GET /menu-rules`
  - `POST /menu-rules`
  - `PUT /menu-rules/{rule_id}`
  - `DELETE /menu-rules/{rule_id}`

### 注文書生成

- 画面: `/order-forms`
- 権限: `operator`
- 主な API:
  - `GET /order-forms/patterns`
  - `POST /order-forms/generate`

### 注文書アップロード

- 画面: `/pdf-upload`
- 権限: `operator`
- 主な API:
  - `POST /ingest/upload`

### 週次注文

- 画面: `/weekly-orders`
- 権限: `operator`
- 主な API:
  - `GET /orders`

### 施設一覧

- 画面: `/facility-master`
- 権限: `operator`
- 主な API:
  - `GET /facility-master`
  - `PUT /facility-master`

## 管理者が使う画面

### システム管理

- 画面: `/system-status`
- 権限: 画面閲覧は `operator`
- `admin` 限定操作:
  - `GET /system/db/download`
  - `POST /system/clear-all`

### ユーザー管理

- 画面: `/users`
- 権限: `admin`
- 主な API:
  - `GET /users`
  - `POST /users`
  - `PUT /users/{user_id}`

### OCRキュー

- 画面: `/ocr-queue`
- 権限: `admin`
- 主な API:
  - `GET /ocr/unclassified`
  - `POST /ocr/unclassified/{job_id}/resolve`

### OCR学習データ

- 画面: `/ocr-training-data`
- 権限: 閲覧は `operator`
- `admin` 限定操作:
  - `DELETE /ocr/training-samples`
  - `GET /ocr/training-samples/export`
  - `GET /ocr/training-samples/export-pdfs`

## 混在している画面

以下は、画面自体は `operator` が開けるが、一部操作だけ `admin` のものです。

### 注文詳細 `/orders/{id}`

- `operator` でできること
  - OCR確認
  - シート保存
  - 明細反映
  - 再解析
  - 施設/週設定
  - 確定
- `operator` でできる施設設定反映
  - `PUT /orders/{id}/facility-template-columns`
  - 施設区分列の列名と数量列定義を、現在選択中の施設テンプレートへ保存
- `admin` 限定
  - `PUT /facilities/{facility_id}/config`

### 送り状履歴 `/shipping-history`

- `operator`
  - 履歴閲覧
- `admin`
  - DBエクスポート
  - 全件削除

### システム管理 `/system-status`

- `operator`
  - 状態確認
- `admin`
  - DBダウンロード
  - 全権クリア

### 施設詳細 `/facilities/{id}`

- 画面は直接URLで開ける
- `operator`
  - 一部参照系
- `admin`
  - 施設更新
  - 施設 config 更新

## 監査結果

今回の監査時点で、`注文処理` 本線の権限ズレは見つかっていません。

確認済み:

- 注文一覧
- 注文詳細
- OCR確認 / シート保存 / 明細反映 / 確定
- 日別出力
- 袋分け
- 総量
- 送り状
- 送り状履歴
- 月次メニュー
- 基準メニュー
- メニューマスター
- メニュールール
- 注文書アップロード
- 施設一覧

## 今後の注意

- `operator` 画面に `admin` 操作が混ざるページは、UI 上でも明示した方がよい
- 新しい画面を追加する時は、`TopNav` の配置と backend の `require_role()` を同時に確認する
- `ユーザー1` と `ユーザー2` はシステム上は同じ `operator` 権限なので、業務上の役割分担はマニュアルで維持する
