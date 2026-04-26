# Pre-Deploy Checks

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

## 4. 認証 / 取込状態
`/system/status` の値が以下を満たすこと
- `oauth_config.configured == true`
- `intake.mode == "manual_upload"`
- `intake.manual_upload_storage.configured == true`

## 5. OCR再解析 品質ゲート
`/system/status` の値が以下を満たすこと
- `ocr_reparse_quality.gate.status == "pass"`
- provider別指標（成功率 / truncated率 / empty率 / validation失敗率）が閾値内

## 6. Three-Surface Parity Gate
対象の `ORDER_ID` について、以下を同じ snapshot として確認すること。

- `GET /orders/{id}/draft-sheet`
- `GET /orders/{id}/ocr-sheet`
- `GET /orders/{id}/workflow-state`

最低限、以下が coherent であること。
- current editor が generic raw sheet (`col1`, `col2`, `col3`, ...) に落ちていない
- `source`
- `fields` / `row_count`
- `can_apply` / `apply_blockers`
- `warnings`

補足:
- 3 endpoint の内容が完全一致である必要はない
- ただし、同じ current order state を説明できない不整合は fail とする

## 7. Current vs Candidate Integrity
- candidate evidence がある場合、current editor が candidate を勝手に current 扱いしていないこと
- `keep current` / `switch to candidate` の判断が current state に明示反映されていること
- stale candidate/job failure が current draft を上書きしていないこと

## 8. Saved-Draft / No-Saved-Draft Bootstrap
- `saved draft present` では persisted semantic draft を優先すること
- `saved draft missing` では semantic bootstrap を先に試し、warning だけで raw fallback しないこと
- semantic shell があるのに current editor が generic raw sheet へ落ちていれば fail

## 9. Deploy-Source Parity
- deploy 前に current prod revision/image を表示して確認する
- prod が branch より先なら clean branch から deploy しない
- parity-safe な integration tree / clean deploy copy を使うこと
- web deploy では stale clean deploy copy の再利用を禁止する
- 標準導線は `scripts/prepare_web_deploy_source.sh` で fresh copy を作り、`.codex-deploy-source.json` sentinel parity が通らない限り build に進めないこと

## 10. Blast Radius
- 原則 service-only deploy
- `worker` 修正なら `worker only`
- `web` 修正なら `web only`
- 両方必要な時だけ理由を明示して full deploy

## 11. Exact-Order Live Smoke
- high-risk OCR/order fix では、対象の exact order を live で before/after 確認する
- 少なくとも
  - Step2 current sheet
  - apply/confirm 可否
  - stale candidate / stale failure の再表示がないこと
  を確認する

補足:
- `task predeploy_prod_checks` は `STRICT_OCR_QUALITY=1` で実行する（fail-fast）。
- `task predeploy_stg_checks` は `STRICT_OCR_QUALITY=0` が既定で、warming-up を許容する。
- provider行ごとの `gate_status=warming_up` は許容（failではない）。
- `scope.mode == "explicit_only"` かつ `scope.included_jobs == 0` の場合は、全量が新しい explicit tag へ切り替わるまで `gate.status == "insufficient_data"` を warming-up として警告扱いにする。
- 直近失敗パターンは `task ocr_reparse_failure_report` で集計し、同時に回帰テスト追加対象を洗い出す。

## 実行方法

共通スクリプト:

```bash
OPERATOR_USER=admin OPERATOR_PASSWORD=****** \
PROJECT_ID=<project-id> REGION=<region> \
WEB_SERVICE=<web-service> WORKER_SERVICE=<worker-service> \
./scripts/predeploy_env_checks.sh
```

staging:

```bash
OPERATOR_USER=admin OPERATOR_PASSWORD=****** task predeploy_stg_checks
```

staging infra plan:

```bash
TF_GOOGLE_CREDENTIALS=/path/to/infra-admin-key.json task infra_stg_plan
```

production:

```
OPERATOR_USER=admin OPERATOR_PASSWORD=****** task predeploy_prod_checks
```

## デプロイ手順

staging web:

```bash
OPERATOR_USER=admin OPERATOR_PASSWORD=****** task deploy_stg_web
```

staging web with exact-order parity:

```bash
OPERATOR_USER=admin OPERATOR_PASSWORD=****** \
ORDER_ID=ORDb266d5d9 task deploy_stg_web
```

production web:

```
OPERATOR_USER=admin OPERATOR_PASSWORD=****** task deploy_prod_web
```

production web with exact-order parity:

```bash
OPERATOR_USER=admin OPERATOR_PASSWORD=****** \
ORDER_ID=ORDb266d5d9 task deploy_prod_web
```

この手順はチェックが通らない限り失敗します。

補足:
- `task deploy_prod_web` / `task deploy_stg_web` は毎回 fresh な clean deploy copy を自動生成する
- 手動で `FRONTEND_DIR` を古い integration copy に向けた deploy は禁止
- `last_frontend_deploy_dir.txt` は直前に生成した deploy source の記録であり、再利用前提ではない

## Worker デプロイ時の必須ゲート

`scripts/deploy_worker_prod_with_checks.sh` は以下を **必須** で実行する:
- ローカル回帰テスト
  - `tests/integration/test_ocr_sheet_history.py`
  - `tests/contract/test_orders_ocr_sheet_history_api.py`
- `GET /orders/{ORDER_ID}/ocr-sheet` 品質ゲート
  - `warnings` が空
  - `weekly_menu*` ソース時、数量あり行比率が閾値以上
  - 数量の異常スパイク検知（中央値ベース）と上限値チェック
- `GET /orders/{ORDER_ID}/draft-sheet` / `ocr-sheet` / `workflow-state` の exact-order parity
  - current editor に generic raw fields (`col1`, `col2`, `col3`, ...) を許可しない
  - `ocr-sheet.can_apply=true` なのに `workflow-state.apply_gate.can_apply=false` のような surface divergence を許可しない
  - `worker` 直接と `web` proxy の `draft-sheet / ocr-sheet / workflow-state` payload が一致すること
- `predeploy_prod_checks.sh` の strict 品質ゲート実行

補足:
- quality gate は `ocr-sheet` 単体の品質を見る。
- parity gate は「実際に Step2 で見える current state が壊れていないか」を見る。
- 両方が必要。

production 実行例:

```bash
OPERATOR_USER=admin OPERATOR_PASSWORD=****** \
./scripts/deploy_worker_prod_with_checks.sh \
  asia-northeast2-docker.pkg.dev/sawahospitalsystem/backend/backend:prod-backend-YYYYMMDD-HHMMSS \
  ORDb266d5d9
```

staging 実行例:

```bash
OPERATOR_USER=admin OPERATOR_PASSWORD=****** \
STRICT_OCR_SHEET_GATE=0 STRICT_OCR_QUALITY=0 \
task deploy_stg_worker ORDER_ID=ORDb266d5d9 IMAGE=asia-northeast2-docker.pkg.dev/sawahospitalsystem/backend/backend:stg-backend-YYYYMMDD-HHMMSS
```
