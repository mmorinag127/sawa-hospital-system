# Deploy Summary (2026-03-24)

このメモは、このリポジトリの deploy 方法と認証の扱いをまとめたものです。

## 1. まず結論

- deploy 自体に `operator` 認証は使わない
- deploy は `gcloud` / GCP 権限で行う
- `operator` 認証は、deploy 前後の HTTP チェックやアプリ利用時に使う

つまり、

- `gcloud run deploy ...` を実行する権限
- `curl -u OPERATOR_USER:OPERATOR_PASSWORD ...` で API を確認する権限

は別物です。

## 2. operator 認証は何に使うか

バックエンド API は `require_role("operator")` で保護されています。

- 例: `backend/src/api/order_forms.py`
- 例: `backend/src/api/orders.py`
- 例: `backend/src/api/menus.py`

認証方式は 2 系統あります。

- Google ログインの `Bearer` token
- Basic 認証の `OPERATOR_USER` / `OPERATOR_PASSWORD`

ログイン画面でもこの 2 方式が出ています。

- `frontend/src/pages/login.tsx`

フロントはログイン後の `Authorization` ヘッダを sessionStorage に保持して API に付けます。

- `frontend/src/services/apiClient.ts`

## 3. 本番で使う deploy 手段

### 3-1. 事前確認だけ実行

スクリプト:

- `scripts/predeploy_prod_checks.sh`

このチェックは以下を見ます。

- Web 到達性
- Worker health
- 認証付き API 到達性
- `/system/status` の intake / OAuth 状態
- OCR quality gate

実行例:

```bash
cd /Users/mmorinag/Sawa/2025.12/workspace

export WEB_URL="https://web-prod-avlnzjjrca-dt.a.run.app"
export WORKER_URL="https://worker-prod-avlnzjjrca-dt.a.run.app"
export OPERATOR_USER="..."
export OPERATOR_PASSWORD="..."
export STRICT_OCR_QUALITY=1

./scripts/predeploy_prod_checks.sh
```

補足:

- このスクリプトは `curl -u "$OPERATOR_USER:$OPERATOR_PASSWORD"` で認証付き API を叩く
- つまり、ここで `operator` 認証は deploy 用ではなく確認用

## 3-2. Web を本番 deploy

スクリプト:

- `scripts/deploy_prod_web.sh`

このスクリプトがやること:

1. predeploy check
2. Cloud Build で frontend image build
3. `gcloud run deploy web-prod`
4. postdeploy check

実行例:

```bash
cd /Users/mmorinag/Sawa/2025.12/workspace

export WEB_URL="https://web-prod-avlnzjjrca-dt.a.run.app"
export WORKER_URL="https://worker-prod-avlnzjjrca-dt.a.run.app"
export OPERATOR_USER="..."
export OPERATOR_PASSWORD="..."
export PROJECT_ID="sawahospitalsystem"
export REGION="asia-northeast2"
export GOOGLE_CLIENT_ID="..."
export STRICT_OCR_QUALITY=1

./scripts/deploy_prod_web.sh
```

注意:

- この deploy の実行主体は `gcloud` 認証
- `OPERATOR_USER` / `OPERATOR_PASSWORD` は pre/post deploy 確認用

## 3-3. Worker/API を本番 deploy

スクリプト:

- `scripts/deploy_worker_prod_with_checks.sh`

このスクリプトがやること:

1. 必須のローカル回帰テスト
2. `gcloud run deploy worker-prod`
3. 最新 revision / image の一致確認
4. 指定 `order_id` の `/orders/{id}/ocr-sheet` 品質ゲート
5. web 経由との一致確認
6. strict predeploy check

実行例:

```bash
cd /Users/mmorinag/Sawa/2025.12/workspace

export OPERATOR_USER="..."
export OPERATOR_PASSWORD="..."
export WEB_URL="https://web-prod-avlnzjjrca-dt.a.run.app"
export PROJECT_ID="sawahospitalsystem"
export REGION="asia-northeast2"

./scripts/deploy_worker_prod_with_checks.sh \
  asia-northeast2-docker.pkg.dev/sawahospitalsystem/backend/backend:prod-backend-YYYYMMDD-HHMMSS \
  ORDc935f9e2
```

引数:

- 第1引数: deploy する backend image
- 第2引数: deploy 後検証に使う `order_id`

注意:

- worker deploy は image を自分で用意済みである前提
- ここでも `operator` 認証は API 品質確認用

## 3-4. Terraform でインフラ反映

用途:

- Cloud Run サービス定義
- env vars
- Secret 参照
- Pub/Sub / Scheduler / IAM など

ドキュメント:

- `infra/terraform/README.md`

初回 bootstrap:

```bash
cd /Users/mmorinag/Sawa/2025.12/workspace/infra/terraform/bootstrap
cp terraform.tfvars.example terraform.tfvars
tofu init
tofu plan
tofu apply
```

prod 環境:

```bash
cd /Users/mmorinag/Sawa/2025.12/workspace/infra/terraform/envs/prod
cp backend.hcl.example backend.hcl
cp terraform.tfvars.example terraform.tfvars
tofu init -backend-config=backend.hcl
tofu plan
tofu apply
```

補足:

- 本番の Cloud Run 環境変数は `infra/terraform/envs/prod/main.tf`
- `AUTH_DISABLED`, `OPERATOR_USER`, `OPERATOR_PASSWORD`, `GOOGLE_OAUTH_CLIENT_ID`, `ALLOWED_EMAILS` などもここで入る

## 4. deploy と認証の関係

### deploy で必要なもの

- `gcloud` が対象 project / Cloud Run / Cloud Build を操作できること

### アプリ/API 確認で必要なもの

- `OPERATOR_USER`
- `OPERATOR_PASSWORD`

### 認証を無効化する例外

バックエンドには `AUTH_DISABLED=true` で認証を無効化する分岐があります。

ただし本番運用では通常無効化しない前提です。

## 5. どれを使えばよいか

普段の判断はこれで十分です。

- UI だけ更新したい
  - `./scripts/deploy_prod_web.sh`
- backend / worker を更新したい
  - `./scripts/deploy_worker_prod_with_checks.sh <image> <order_id>`
- インフラや env vars を変えたい
  - Terraform (`tofu plan / apply`)
- deploy 前にまず健康診断だけしたい
  - `./scripts/predeploy_prod_checks.sh`

## 6. 注意事項

- Cloud Run の env 変更は partial update で落とさない
- Cloud Run 設定変更は IaC か、必要 env を全部確認した上で一括反映する
- `operator` 認証は deploy 権限の代替ではない

## 7. 主な参照先

- `backend/src/api/auth.py`
- `backend/src/api/order_forms.py`
- `frontend/src/pages/login.tsx`
- `frontend/src/services/apiClient.ts`
- `scripts/predeploy_prod_checks.sh`
- `scripts/deploy_prod_web.sh`
- `scripts/deploy_worker_prod_with_checks.sh`
- `infra/terraform/README.md`
- `infra/terraform/envs/prod/main.tf`
- `docs/predeploy_checks.md`
