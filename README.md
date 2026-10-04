# Sawa Hospital System

病院・施設からの発注FAXを取り込み、OCR解析、注文管理、袋分け、ラベル/納品書出力までを一貫して支援するシステムです。運用担当者が確認・修正・確定できるUIと、GCP上のバックエンド/ワーカー/OCRパイプラインで構成されています。

## 主な機能
- FAX/PDFの取り込みとOCR解析（テンプレート方式 + ROI）
- 注文の一覧/詳細/確認フロー
- 施設マスターの管理（施設・区分・テンプレート関連）
- 週次メニューの登録と編集
- 袋分けロジックに基づくラベル/納品書の生成

## 構成
- `backend/` FastAPI + SQLAlchemy（API/ワーカー）
- `frontend/` Next.js（運用UI）
- `ocr_pipeline/` OCR専用サービス
- `infra/` GCP IaC（OpenTofu/Terraform）

## 開発の入口
手元で動かす場合は `docs/quickstart.md` を参照してください。

## Menu master production UI mock test

`frontend/` で事前に `npm run build` を完了させた後、Playwright bundled WebKitで実行します。

```sh
cd frontend
npx playwright install webkit
npm run build
E2E_PORT=31318 E2E_BASE_URL=http://127.0.0.1:31318 npx playwright test --config=playwright.menu-masters.config.js
```

このconfigは`tests/e2e/menu_masters.spec.ts`だけを対象にし、WebKit/headless、現行productionと同じ`npm run start`、`reuseExistingServer:false`を固定します。
`E2E_PORT`は1024から65535の整数、`E2E_BASE_URL`は同一portの`http` localhost root URL（query/hash/userinfoなし）だけを受け付けます。
