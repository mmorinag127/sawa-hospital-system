---
marp: true
theme: default
paginate: true
size: 16:9
---

# Sawa Hospital System
## ユーザー向けマニュアル

- 作成日: 2026-01-21
- 対象: オペレーター / 管理者

---

# 目次
1. 概要・日常運用
2. 注文処理（ステップ）
3. 施設一覧・月次メニュー・ルール
4. OCRキュー / トラブル対応
5. 運用ルール・連絡フロー

---

# 1. 概要
FAX注文書PDFを取り込み、OCR→明細→袋分け→出力までを一連で処理します。

**現行URL（暫定）**
- https://web-prod-avlnzjjrca-dt.a.run.app
- ※正式版では置き換わります

**主な流れ**
1. FAX受信 → 注文自動登録
2. OCR結果確認／修正
3. 明細確認
4. 袋分け確認
5. 出力ダウンロード

---

# 2. 日常運用（毎日やること）
**朝**
- 注文一覧で新着とOCR失敗を確認

**日中**
- 注文詳細を順番に処理

**夕方**
- OCR失敗・出力漏れがないか確認

---

# ダッシュボード
![Dashboard](./user_manual_screenshots/dashboard.png)

---

# 注文一覧（Orders）
![Orders](./user_manual_screenshots/orders_list.png)

**操作**
- 対象の注文をクリック → 注文詳細へ

---

# 注文詳細: ステップ概要
1. 原本PDF
2. OCR結果
3. 明細
4. 袋分け
5. 出力

---

# 1) 原本PDFの確認
![OrderPDF](./user_manual_screenshots/order_pdf.png)

**ポイント**
- 表が読めるか／施設名が見えるかを確認

---

# 2) OCRステータス
![OCRStatus](./user_manual_screenshots/ocr_status.png)

**操作**
- 失敗/停止 → 再解析
- 実行中 → しばらく待機

---

# 3) OCR結果（先頭10行）
![OCRMarkdown](./user_manual_screenshots/ocr_markdown.png)

**操作**
- 文字化けや空欄が多い場合は「修正する」

---

# 4) OCR修正（オーバーレイ編集）
![OCREdit](./user_manual_screenshots/ocr_overlay_edit.png)

---

# 4-1) レイアウト調整
![LayoutAdjust](./user_manual_screenshots/layout_adjust.png)

**操作**
- 「自動で合わせる」→ ずれが残る場合は微調整

---

# 5) 明細確認
![Detail](./user_manual_screenshots/detail.png)

**ポイント**
- 空欄・誤りがないか確認

---

# 6) 袋分け結果
![Bagging](./user_manual_screenshots/bagging.png)

**操作**
- 修正した場合は「袋分け再計算」

---

# 7) 出力（ダウンロード）
![Outputs](./user_manual_screenshots/outputs.png)

**出力種類**
- ラベルCSV / 納品書Excel / 総量CSV

---

# 施設一覧（施設マスター）
![FacilityList](./user_manual_screenshots/facility_list.png)

---

# 施設詳細
![FacilityDetail](./user_manual_screenshots/facility_detail.png)

---

# 月次メニュー
![MonthlyMenu](./user_manual_screenshots/monthly_menu.png)

**操作**
1. Excelアップロード
2. 単位・温冷・袋種類を確認／修正

---

# メニュールール
![MenuRules](./user_manual_screenshots/menu_rules.png)

**操作**
- 基本ルール + 施設/メニュー例外の設定

---

# OCR Queue
![OCRQueue](./user_manual_screenshots/ocr_queue.png)

**用途**
- OCR失敗の確認／理由の把握

---

# よくある詰まりポイント
- 注文が出ない → OCR Queue確認
- OCR結果が空 → 修正 or 再解析
- 袋分けが空 → OCR反映の再実行
- 出力できない → 再ダウンロード

---

# 運用ルール（記入欄）
- 締切時間: 未記入
- 優先順位: 未記入
- 最終確認者: 未記入
- 修正判断基準: 未記入

---

# 出力ファイルの扱い（記入欄）
- 保存先: 未記入
- ファイル名ルール: 未記入
- 共有方法: 未記入
- 保管期間: 未記入

---

# 連絡フロー（記入欄）
- 一次連絡先: 未記入
- 二次連絡先: 未記入
- 緊急連絡先: 未記入

---

# 付録: 用語
- OCR: 画像から文字を読み取る処理
- オーバーレイ: OCRの認識結果を画像上に重ねて表示
- 袋分け: 施設区分・数量に応じた仕分け
