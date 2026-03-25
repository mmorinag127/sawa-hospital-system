# Shipping History 再構築計画 2026-03-24

## 1. 目的

- `/shipping-history` を「追跡の生ログ一覧」から「運用で使う佐川出荷状況画面」へ再構築する
- 既定表示を `有効中` にして、未配達の伝票をすぐ追えるようにする
- `日にちごと` と `施設ごと` にまとまって見える画面にする
- 生ログは監査用に分離し、通常運用画面の見づらさと Quota 圧迫の原因を切り分ける

## 2. 現状の課題

- 現行 `/shipping-history` は `shipping_tracking_logs` の生ログをそのまま一覧表示している
- 同じ伝票でも再照会のたびに新しい行が増えるため、最新状態より重複ログが目立つ
- 施設名は `shipping_pdf_parse` と `excel_enrich` では入るが、`manual_track` と `scheduled_refresh` では欠けることがある
- `ship_date` は PDF/Excel から抽出できているが、追跡ログには保存していない
- `直近 +/-3日` を本来の出荷日基準で出したくても、現行DBでは `looked_up_at` しか使えない
- `Quota` は主にログ行数ベースで増えるが、画面はそのログを運用用一覧として見せてしまっている

## 3. 目標画面

### 3.1 役割

- `運用画面`
  - いま何が未完了か
  - どの施設に未配達が残っているか
  - いつ最後に照会されたか
- `監査画面`
  - いつ誰がどの経路で照会したか
  - エクスポート
  - 全件削除

### 3.2 画面構成

- 画面名: `佐川出荷状況`
- 既定タブ: `有効中`
- サブタブ:
  - `有効中`
  - `直近±3日`
  - `全件`
  - `要確認`
  - `監査ログ`

### 3.3 上部サマリ

- 有効中件数
- 配達完了件数
- 要確認件数
- 最終自動照会時刻
- 次回自動照会予定
- Quota
  - 主表示ではなく補助表示
  - 警告時のみ強く見せる

### 3.4 フィルタ

- 基準日
- 施設
- 状態
- 取得元
- 施設未設定を含む/除外

### 3.5 一覧表示

- 第一階層: `出荷日`
- 第二階層: `施設`
- 第三階層: `伝票一覧`

施設グループの表示内容:

- 件数
- 未完了件数
- 配達完了件数
- 最終照会日時

伝票行の表示内容:

- 伝票番号
- 現在状態
- 到着日時
- 最終照会日時
- 取得元
- エラー
- 履歴を見る

## 4. 表示ルール

### 4.1 有効中

- 伝票ごとの最新1件のみ表示
- 条件:
  - `delivered = false`
- `error` は除外せず表示する
- 並び順:
  - 出荷日 asc
  - 施設名 asc
  - 最終照会日時 desc

### 4.2 直近±3日

- 本命の定義:
  - `ship_date` が `基準日 - 3日` 以上 `基準日 + 3日` 以下
- ただし現状DBでは `ship_date` 未保存なので、Phase 1 ではこのタブは仮置きにする
- Phase 1 の選択肢:
  - 非表示
  - `looked_up_at` ベースの暫定表示
- 推奨:
  - 中途半端な暫定仕様は混乱するので、Phase 1 では `準備中` 表示にする

### 4.3 全件

- 伝票ごとの最新1件のみ表示
- 期間指定なし

### 4.4 要確認

- 条件:
  - `error` がある
  - `status = 該当なし`
  - `facility_name` が欠損
  - 一定期間以上 `delivered = false`
- 停滞判定の初期案:
  - `looked_up_at` から 24時間以上更新がない未配達

### 4.5 監査ログ

- 既存 `GET /shipping/status/history` ベース
- 生ログをそのまま表示
- `admin` のみ:
  - CSV/JSON エクスポート
  - 全件削除

## 5. API 案

## 5.1 継続利用

- `GET /shipping/status/history`
  - Raw log 用
- `DELETE /shipping/status/history`
  - admin のみ
- `GET /shipping/status/export`
  - admin のみ
- `POST /shipping/status/refresh-pending`
  - 既存継続

## 5.2 新規追加

- `GET /shipping/status/latest`

クエリ案:

- `view=active|all|attention|recent`
- `base_date=YYYY-MM-DD`
- `window_days=3`
- `facility_name=...`
- `source=...`
- `limit=...`
- `include_quota=true|false`

レスポンス案:

```json
{
  "generated_at": "2026-03-24T16:00:00+09:00",
  "timezone": "Asia/Tokyo",
  "view": "active",
  "base_date": "2026-03-24",
  "window_days": 3,
  "summary": {
    "total": 18,
    "delivered": 0,
    "pending": 18,
    "errors": 2,
    "facility_missing": 3
  },
  "groups": [
    {
      "ship_date": "2026-03-23",
      "facility_name": "春日苑 松茂",
      "item_count": 2,
      "pending_count": 2,
      "delivered_count": 0,
      "latest_looked_up_at": "2026-03-24T15:15:00+09:00",
      "items": [
        {
          "tracking_key": "491721685792",
          "tracking_number": "4917-2168-5792",
          "status": "配達中",
          "delivered": false,
          "arrival_text": null,
          "looked_up_at": "2026-03-24T15:15:00+09:00",
          "source": "scheduled_refresh",
          "error": null,
          "facility_name_source": "backfilled"
        }
      ]
    }
  ],
  "quota": {}
}
```

## 6. DB/保存変更

### 6.1 追跡ログへ追加する列

- `ship_date DATE NULL`
- 可能なら将来的に:
  - `facility_id VARCHAR NULL`
  - `facility_name_source VARCHAR NULL`

### 6.2 保存時の補完

- `shipping/parse`
  - `ship_date` を保存
  - `facility_name` を保存
- `shipping/enrich-excel`
  - `ship_date` が Excel にあるなら保存
  - `facility_name` を保存
- `manual_track`
  - `facility_name` は通常不明
- `scheduled_refresh`
  - 同一 `tracking_key` の直近履歴から `facility_name` を補完
  - 同一 `tracking_key` の直近履歴から `ship_date` を補完

### 6.3 重要な実装方針

- 保存は引き続き履歴として append してよい
- ただし画面表示用には毎回 `tracking_key` ごとの最新1件へ集約する
- 後続でさらに Quota を抑えたい場合は、別途 `current_shipping_statuses` のような派生テーブルを検討する

## 7. 段階実装

### Phase 1

- `GET /shipping/status/latest` 実装
- `tracking_key` ごとの最新集約ロジックを service 層に追加
- `/shipping-history` を新UIに置き換え
- タブは `有効中 / 全件 / 要確認 / 監査ログ`
- `直近±3日` はまだ出さないか、準備中表示

完了条件:

- operator が未配達の最新状態を重複なしで見られる
- admin の破壊操作が監査ログに隔離される

### Phase 2

- `ship_date` 列追加
- `shipping/parse` と `shipping/enrich-excel` 保存経路へ `ship_date` 反映
- `scheduled_refresh` 時の `facility_name` / `ship_date` 補完

完了条件:

- 新規データについて `ship_date` ベースのグルーピングが機能する

### Phase 3

- `直近±3日` タブ有効化
- 既存データの backfill 検討
- 施設欠損率の監視追加

完了条件:

- 基準日ベースで出荷日窓を絞った運用ができる

### Phase 4

- 必要なら raw log を別URLへ退避
  - 例: `/shipping-history/logs`
- 必要なら `current_shipping_statuses` テーブル導入
- Quota 対策の整理

## 8. テスト計画

### 8.1 Backend Unit

- `latest` 集約が `tracking_key` 単位で最新1件になる
- `active` が `delivered=false` を返す
- `attention` が `error`, `該当なし`, `facility欠損`, `停滞` を拾う
- `scheduled_refresh` で施設名と出荷日が履歴から補完される

### 8.2 Backend Contract

- `GET /shipping/status/latest`
  - 200
  - スキーマ
  - 権限制御
- 既存 `GET /shipping/status/history`
  - 互換維持
- `admin` だけが export/delete できる

### 8.3 Backend Integration

- PDF解析から保存した伝票が `ship_date` と `facility_name` を持つ
- Excel更新から保存した伝票が `facility_name` を持つ
- `refresh-pending` 後も施設名が消えない
- 同一伝票の複数ログから `latest` が正しい1件を返す

### 8.4 Frontend E2E

- 初期表示が `有効中`
- 施設グループが折りたたみ/展開できる
- `要確認` タブで異常項目が見える
- operator では admin ボタンが表示されない
- admin では監査ログで export/delete が使える

### 8.5 データ品質テスト

- `ship_date IS NULL` の割合
- `facility_name IS NULL` の割合
- `status = 該当なし` の割合
- `error` 発生率

## 9. 検証に必要な実データ

- 実在する未配達番号
- 実在する配達完了番号
- facility_name が欠ける scheduled refresh 後のデータ
- 同一伝票に対して複数回照会した履歴

## 10. 非目標

- この段階では佐川の公式API移行は前提にしない
- この段階では tracking と order の厳密な1対1紐付けは必須にしない
- この段階では Quota 削減のための大規模履歴整理までは着手しない

## 11. 次の実装順

1. `shipping_status_store` に latest 集約ロジックを追加
2. `GET /shipping/status/latest` を追加
3. `shipping-history.tsx` を新UIへ置換
4. `ship_date` 保存の migration と保存経路修正
5. `直近±3日` タブを有効化
