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

