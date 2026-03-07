# Production Pre-Deploy Checks

このチェックが **全て OK** になった時だけデプロイ可能とする。

## 1. Web (UI) 到達性
- `GET {WEB_URL}/` -> 200 or 308
- `GET {WEB_URL}/login` -> 200 or 308

## 2. Worker ヘルス
- `GET {WORKER_URL}/health` -> 200 / `{"status":"ok"}`
- `GET {WORKER_URL}/health/backlog` -> 200

## 3. 認証済み API
- `GET {WORKER_URL}/orders?include_ocr=false` -> 200
- `GET {WORKER_URL}/system/status` -> 200
- （postdeployで `CHECK_WEB_PROXY=1` の場合）
  - `GET {WEB_URL}/api/orders?include_ocr=false` -> 200 or 308
  - `GET {WEB_URL}/api/system/status` -> 200 or 308

## 4. Gmail/OAuth 状態
`/system/status` の値が以下を満たすこと
- `gmail_config.configured == true`
- `oauth_config.configured == true`
- `gmail_watch.status == "ok"`

## 5. OCR再解析 品質ゲート
`/system/status` の値が以下を満たすこと
- `ocr_reparse_quality.gate.status == "pass"`
- provider別指標（成功率 / truncated率 / empty率 / validation失敗率）が閾値内

補足:
- `task predeploy_prod_checks` は `STRICT_OCR_QUALITY=1` で実行する（fail-fast）。
- provider行ごとの `gate_status=warming_up` は許容（failではない）。
- 直近失敗パターンは `task ocr_reparse_failure_report` で集計し、同時に回帰テスト追加対象を洗い出す。

## 実行方法

```
OPERATOR_USER=admin OPERATOR_PASSWORD=****** task predeploy_prod_checks
```

## デプロイ手順

```
OPERATOR_USER=admin OPERATOR_PASSWORD=****** task deploy_prod_web
```

この手順はチェックが通らない限り失敗します。

## Worker デプロイ時の必須ゲート

`scripts/deploy_worker_prod_with_checks.sh` は以下を **必須** で実行する:
- ローカル回帰テスト
  - `tests/integration/test_ocr_sheet_history.py`
  - `tests/contract/test_orders_ocr_sheet_history_api.py`
- `GET /orders/{ORDER_ID}/ocr-sheet` 品質ゲート
  - `warnings` が空
  - `weekly_menu*` ソース時、数量あり行比率が閾値以上
  - 数量の異常スパイク検知（中央値ベース）と上限値チェック
- `web` 経由と `worker` 直接の `ocr-sheet` 一致検証
- `predeploy_prod_checks.sh` の strict 品質ゲート実行

実行例:

```bash
OPERATOR_USER=admin OPERATOR_PASSWORD=****** \
./scripts/deploy_worker_prod_with_checks.sh \
  asia-northeast2-docker.pkg.dev/sawahospitalsystem/backend/backend:prod-backend-YYYYMMDD-HHMMSS \
  ORDb266d5d9
```
