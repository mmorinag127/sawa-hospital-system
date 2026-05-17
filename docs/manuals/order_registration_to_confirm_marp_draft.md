---
marp: true
theme: default
paginate: true
size: 16:9
style: |
  section { font-family: -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Noto Sans JP", sans-serif; font-size: 24px; }
  h1 { font-size: 40px; }
  h2 { font-size: 30px; }
  img { max-height: 430px; object-fit: contain; }
  .shot img { width: 100%; max-height: 500px; }
  .two { display: grid; grid-template-columns: 1.05fr .95fr; gap: 24px; align-items: start; }
  .note { font-size: 20px; }
---

# 注文登録から最終確定まで
## stg live 操作マニュアル

対象: 注文書アップロード、注文一覧、注文詳細 workflow-v2
作成: 2026-05-17 / 最新版

---

# 全体フローチャート

![flow](./order_flowchart.svg)

---

# 作業の基本ルール

- 画面上で数量・施設・週に迷ったら、確定せず該当STEPへ戻る。
- `保存`、`再解析`、`最終確定` は実データを変える操作。押す前に対象注文IDを確認する。
- エラーや不明点がある注文は、日別出力では直さず注文詳細で直す。

---

# 入口: ダッシュボードから注文書アップロードへ

<div class="two"><div>

操作: `注文書アップロードへ` を押す。
使う場面: 新しい注文PDFを登録するとき。

分岐:
- 登録済み注文を確認するだけなら `注文一覧へ`。
- 発送日単位で出力するなら `日別出力へ`。

</div><div class="shot">

![upload link](./action_screenshots_v3/dashboard_upload_link.png)

</div></div>

---

# 入口: ダッシュボードから注文一覧へ

<div class="two"><div>

操作: `注文一覧へ` を押す。
使う場面: 登録後の注文を探して詳細へ進むとき。

分岐:
- 未登録なら先に `注文書アップロードへ`。
- 確定済みの出力確認なら `日別出力へ`。

</div><div class="shot">

![orders link](./action_screenshots_v3/dashboard_orders_link.png)

</div></div>

---

# STEP 0-1: 注文書アップロード画面を開く

<div class="two"><div>

確認: 見出しが `注文書アップロード` になっている。
ここからPDFファイル、施設、対象週、OCR実行有無を指定する。

</div><div class="shot">

![upload overview](./action_screenshots_v3/upload_page_overview.png)

</div></div>

---

# STEP 0-2: PDFファイルを選ぶ

<div class="two"><div>

操作: ファイル選択欄で注文書PDFを選ぶ。
注意: 複数施設・複数週が混ざるPDFは、運用ルールどおり分割済みか確認する。

分岐:
- PDFがない: 登録できない。
- 間違ったPDF: アップロード前に選び直す。

</div><div class="shot">

![file](./action_screenshots_v3/upload_file_input.png)

</div></div>

---

# STEP 0-3: 施設を指定する

<div class="two"><div>

操作: 施設入力欄で対象施設を指定する。
使う場面: OCR推定に頼らず、施設を明示したいとき。

分岐:
- 施設が明確: 指定する。
- 施設が不明: 空欄で登録後、注文詳細STEP1で候補確認する。

</div><div class="shot">

![facility](./action_screenshots_v3/upload_facility_input.png)

</div></div>

---

# STEP 0-4: 対象週を指定する

<div class="two"><div>

操作: 対象週入力欄を指定する。
確認: PDF内の日付範囲と一致していること。

分岐:
- 対象週が明確: 指定する。
- PDFの日付が不明: 注文詳細STEP1で原本を見て確定する。

</div><div class="shot">

![week](./action_screenshots_v3/upload_target_week_input.png)

</div></div>

---

# STEP 0-5: 強制登録の扱い

<div class="two"><div>

操作: 必要な場合だけ `強制` チェックを入れる。
通常はOFF。

分岐:
- 同じPDF/同じ注文を再登録したい: ONを検討。
- 通常登録: OFFのまま。

</div><div class="shot">

![force](./action_screenshots_v3/upload_force_checkbox.png)

</div></div>

---

# STEP 0-6: OCRスキップの扱い

<div class="two"><div>

操作: OCRを走らせない場合だけ `OCRスキップ` をON。
通常はOFF。

分岐:
- PDF登録だけ先に行う: ON。
- OCR候補まで作る: OFF。

</div><div class="shot">

![skip](./action_screenshots_v3/upload_skip_ocr_checkbox.png)

</div></div>

---

# STEP 0-7: アップロード実行

<div class="two"><div>

操作: 入力内容を確認してアップロードボタンを押す。
結果: 注文IDまたは既存注文へのリンクが表示される。

分岐:
- 成功: 注文詳細へ進む。
- 既存注文: 既存注文リンクを開く。
- エラー: PDF、施設、週、重複条件を見直す。

</div><div class="shot">

![submit](./action_screenshots_v3/upload_submit_button.png)

</div></div>

---

# STEP 0-8: 注文一覧へ移動

<div class="two"><div>

操作: `注文一覧へ` を押す。
使う場面: 登録後、対象注文を一覧から探す。

</div><div class="shot">

![to orders](./action_screenshots_v3/upload_to_orders_button.png)

</div></div>

---

# 注文一覧: 画面全体

<div class="two"><div>

確認: 注文ID、施設、週、状態を見て対象注文を探す。
ここで行うことは「探す」「絞る」「詳細を開く」まで。

</div><div class="shot">

![orders overview](./action_screenshots_v3/orders_page_overview.png)

</div></div>

---

# 注文一覧: 検索する

<div class="two"><div>

操作: 検索欄に注文ID、施設名、message_id などを入れる。
使う場面: 登録直後の注文を特定するとき。

分岐:
- 見つかる: 詳細へ進む。
- 見つからない: 日付/状態/アーカイブ条件を見直す。

</div><div class="shot">

![search](./action_screenshots_v3/orders_search_input.png)

</div></div>

---

# 注文一覧: 状態で絞る

<div class="two"><div>

操作: 状態フィルタを選ぶ。
通常は `要確認`、出力確認は `確定` を見る。

分岐:
- `要確認`: まだ作業が必要。
- `確定`: 日別出力の対象。
- `エラー`: OCR/登録で止まっている。

</div><div class="shot">

![status](./action_screenshots_v3/orders_status_filter.png)

</div></div>

---

# 注文一覧: アーカイブを含める

<div class="two"><div>

操作: 必要な場合だけアーカイブ表示をON。
使う場面: 過去に非表示にした注文を確認するとき。

分岐:
- 通常作業: OFF。
- 見つからない注文を探す: ONも確認。

</div><div class="shot">

![archived](./action_screenshots_v3/orders_include_archived.png)

</div></div>

---

# 注文一覧: 詳細を開く

<div class="two"><div>

操作: 対象行の `詳細` を押す。
結果: 注文詳細 workflow-v2 に移動する。

</div><div class="shot">

![detail](./action_screenshots_v3/orders_detail_link.png)

</div></div>

---

# 注文詳細: ざっくり構成

<div class="two"><div>

見る順番:
1. 上部で注文ID・状態を確認
2. STEP1: 施設/週/原本
3. STEP2: OCR候補
4. STEP3: 明細
5. STEP4: 袋分け/出力/最終確定

</div><div class="shot">

![detail overview](./action_screenshots_live/detail_overview.png)

</div></div>

---

# 共通操作: 再読み込み

<div class="two"><div>

操作: `再読み込み` を押す。
使う場面: OCR処理後、保存後、別画面から戻った直後。

分岐:
- 表示が古い: 再読み込み。
- 保存前の入力がある: 先に保存可否を判断する。

</div><div class="shot">

![reload](./action_screenshots_v2/order_reload.png)

</div></div>

---

# 共通操作: 検査画面を開く

<div class="two"><div>

操作: `検査` を押す。
使う場面: OCRやシート状態の内部確認が必要なとき。

</div><div class="shot">

![inspection](./action_screenshots_v2/order_inspection.png)

</div></div>

---

# 共通操作: 一覧へ戻る

<div class="two"><div>

操作: `注文一覧へ戻る` を押す。
使う場面: 対象注文を間違えた、別注文へ移る、確定後に一覧確認する。

</div><div class="shot">

![back](./action_screenshots_v2/order_back_list.png)

</div></div>

---

# STEP1: タブを開く

<div class="two"><div>

操作: `STEP 1` タブを押す。
目的: 原本PDF、施設、対象週を確認する。

分岐:
- 施設/週が正しい: STEP2へ。
- 不明/違う: このSTEPで直してから進む。

</div><div class="shot">

![step1](./action_screenshots_live/step1_tab.png)

</div></div>

---

# STEP1: 原本PDFを開く

<div class="two"><div>

操作: 原本PDFを開くボタン/リンクを押す。
確認: 施設名、日付範囲、注文欄の位置。

例外:
- PDFが開けない: 登録PDFまたは保存先の問題として止める。
- PDFと画面の施設/週が違う: STEP1で修正する。

</div><div class="shot">

![original](./action_screenshots_live/step1_open_original.png)

</div></div>

---

# STEP1: 施設候補を選ぶ

<div class="two"><div>

操作: 正しい施設候補の選択ボタンを押す。
判断: PDF上の施設名と一致する候補だけ選ぶ。

分岐:
- 正しい候補がある: 選択して保存。
- 候補がない/曖昧: 管理者確認。推測で進めない。

</div><div class="shot">

![facility candidate](./action_screenshots_live/step1_facility_select.png)

</div></div>

---

# STEP1: 保存する

<div class="two"><div>

操作: 施設・週を確認して `保存` を押す。
保存後: STEP2へ進む。

例外:
- 保存済みシートがあり前提が変わる: 数量保持/クリアの確認が出る場合がある。
- 前提が大きく違う: 数量クリアしてOCRから見直す。

</div><div class="shot">

![save step1](./action_screenshots_live/step1_week_select.png)

</div></div>

---

# STEP2: タブを開く

<div class="two"><div>

操作: `STEP 2` タブを押す。
目的: OCR候補が正しいか、原本と照合して判断する。

</div><div class="shot">

![step2](./action_screenshots_live/step2_tab.png)

</div></div>

---

# STEP2: 横/縦レイアウトを切り替える

<div class="two"><div>

操作: 見やすいレイアウトボタンを押す。
使い分け:
- 横: PDFとシートを左右比較。
- 縦: 画面幅が狭い時や表を広く見る時。

</div><div class="shot">

![layout horizontal](./action_screenshots_live/step2_tab.png)

</div></div>

---

# STEP2: OCR候補が正しい場合

<div class="two"><div>

操作: `はい` / 正しい側の選択を押す。
結果: OCR候補を明細へ進める判断になる。

分岐:
- 数量・品目が原本と一致: `はい`。
- 一部でも違う: `いいえ` または修正へ。

</div><div class="shot">

![yes](./action_screenshots_live/step2_next.png)

</div></div>

---

# STEP2: OCR候補が違う場合

<div class="two"><div>

操作: `いいえ` / 修正側の選択を押す。
対応: 原本を見てシート修正、または再解析を判断する。

例外:
- 施設/週が違う: STEP1へ戻る。
- OCR画像自体が壊れている: 再解析または復元を検討。

</div><div class="shot">

![no](./action_screenshots_live/step2_prev.png)

</div></div>

---

# STEP2: 再解析する

<div class="two"><div>

操作: `再解析` ボタンを押す。
使う場面: OCR候補が明らかに崩れている、原本反映が不足している。

注意: 副作用あり。既存の確認済み内容がある場合は、押す前に対象注文IDを確認する。

</div><div class="shot">

![reparse](./action_screenshots_live/step2_tab.png)

</div></div>

---

# STEP2: 復元する

<div class="two"><div>

操作: `復元` / 基礎データ復元を押す。
使う場面: 現在の候補が壊れていて、保存済み基礎へ戻したいとき。

分岐:
- 復元で戻る: 再度OCR候補を確認。
- 戻らない: 注文登録またはOCR処理の問題として調査。

</div><div class="shot">

![restore](./action_screenshots_live/step2_tab.png)

</div></div>

---

# STEP2: 明細へ進む

<div class="two"><div>

操作: OCR確認完了後、明細へ進むボタンを押す。
結果: STEP3で明細生成・確認に進む。

禁止: OCR候補が不明なまま進めない。

</div><div class="shot">

![complete](./action_screenshots_live/step2_next.png)

</div></div>

---

# STEP3: タブを開く

<div class="two"><div>

操作: `STEP 3` タブを押す。
目的: 注文明細を生成し、数量異常を確認して保存する。

</div><div class="shot">

![step3](./action_screenshots_live/step3_tab.png)

</div></div>

---

# STEP3: 明細を生成する

<div class="two"><div>

操作: `明細生成` を押す。
使う場面: OCR確認後、注文明細を作るとき。

分岐:
- 生成成功: 明細表を確認。
- 生成失敗: STEP1/STEP2の施設・週・OCR状態を戻って確認。

</div><div class="shot">

![generate](./action_screenshots_live/step3_first_detail_open.png)

</div></div>

---

# STEP3: AI候補を出す

<div class="two"><div>

操作: `AI候補` を押す。
使う場面: OCR値と明細の不一致候補を補助的に出すとき。

注意: AI候補は確定値ではない。原本と表で確認してから適用する。

</div><div class="shot">

![ai](./action_screenshots_live/step3_first_detail_open.png)

</div></div>

---

# STEP3: AI候補を適用する

<div class="two"><div>

操作: 内容を確認して `適用` を押す。
分岐:
- 原本と一致: 適用。
- 判断不能/違う: 適用せず手動修正。

</div><div class="shot">

![apply ai](./action_screenshots_live/step3_first_detail_open.png)

</div></div>

---

# STEP3: 異常チェックを実行する

<div class="two"><div>

操作: `異常チェック` を押す。
見るもの: 極端な数量、空欄、品目ずれ、単位違い。

分岐:
- 異常なし: 確認済みへ。
- 異常あり: 該当セルを修正する。

</div><div class="shot">

![anomaly](./action_screenshots_live/step3_first_detail_open.png)

</div></div>

---

# STEP3: 異常を修正する

<div class="two"><div>

操作: 修正対象を開き、原本に合わせて直す。
戻り先: OCR読み取りが原因ならSTEP2、施設/週が原因ならSTEP1。

</div><div class="shot">

![fix](./action_screenshots_live/step3_first_detail_open.png)

</div></div>

---

# STEP3: シート確認済みにする

<div class="two"><div>

操作: 内容確認後に `シート確認済み` を押す。
条件: 原本、OCR、明細の数量が一致していること。

</div><div class="shot">

![sheet confirmed](./action_screenshots_live/step3_next.png)

</div></div>

---

# STEP3: 保存する

<div class="two"><div>

操作: `保存` を押す。
結果: STEP4の袋分け・出力に使う明細が更新される。

分岐:
- 保存成功: STEP4へ。
- 保存失敗: エラー内容を見て、表の不正値や通信状態を確認。

</div><div class="shot">

![save](./action_screenshots_live/step3_next.png)

</div></div>

---

# STEP4: タブを開く

<div class="two"><div>

操作: `STEP 4` タブを押す。
目的: 袋分け、ラベル、納品書、最終確定を行う。

</div><div class="shot">

![step4](./action_screenshots_live/step4_tab.png)

</div></div>

---

# STEP4: 袋分けを計算する

<div class="two"><div>

操作: `袋分け計算` を押す。
条件: STEP3の明細が保存済みであること。

分岐:
- 計算成功: 袋分け内容を確認。
- 計算失敗: 明細、単位、施設設定を確認。

</div><div class="shot">

![bags](./action_screenshots_live/step4_recalc_loading.png)

</div></div>

---

# STEP4: 袋分けを確認済みにする

<div class="two"><div>

操作: 袋分け内容を確認して `確認済み` を押す。
確認: 食種、数量、袋数、施設単位が妥当か。

</div><div class="shot">

![confirm bags](./action_screenshots_live/step4_next.png)

</div></div>

---

# STEP4: ラベルを出力する

<div class="two"><div>

操作: `ラベル` ダウンロードを押す。
用途: 現場貼付用ラベルを出力する。

分岐:
- 出力成功: ファイルを確認。
- 出力失敗: 袋分け計算と明細保存状態を確認。

</div><div class="shot">

![label](./action_screenshots_live/step5_download_1.png)

</div></div>

---

# STEP4: ラベルをプレビューする

<div class="two"><div>

操作: `プレビュー` を押す。
使う場面: ダウンロード前に内容を画面で確認する。

</div><div class="shot">

![preview](./action_screenshots_live/step5_preview_1.png)

</div></div>

---

# STEP4: 納品書を出力する

<div class="two"><div>

操作: `納品書` ダウンロードを押す。
確認: 施設名、対象日、品目、数量。

</div><div class="shot">

![delivery](./action_screenshots_live/step5_download_2.png)

</div></div>

---

# STEP4: 最終確定する

<div class="two"><div>

操作: 全確認後に `注文を確定` を押す。
条件:
- STEP1 施設/週が正しい
- STEP2 OCR確認済み
- STEP3 明細保存済み
- STEP4 袋分け確認済み

分岐:
- 確定可能: 確定して日別出力対象になる。
- ブロックあり: 表示されたSTEPへ戻って修正。

</div><div class="shot">

![confirm](./action_screenshots_live/step5_confirmed_status.png)

</div></div>

---

# 例外分岐まとめ

- 施設が違う/不明: STEP1で原本PDFと候補を確認。候補が曖昧なら止める。
- 対象週が違う/不明: STEP1で日付範囲を確認。保存済み明細への影響を確認。
- OCR候補が違う: STEP2で `いいえ`、修正、再解析、復元を判断。
- 明細数量が変: STEP3で異常チェック、必要ならSTEP2へ戻る。
- 袋分けが変: STEP4で単位・施設設定・明細を確認。
- 確定できない: ブロック表示のSTEPに戻り、未完了操作を終わらせる。

---

# 完了状態

注文詳細で最終確定が完了すると、注文は日別出力の対象になる。
次の作業は `日別出力マニュアル` を参照する。

---

# 補足: シート編集の操作位置

<div class="two"><div>

操作場所: STEP2 `OCR修正` の中にあるシート編集エリア。
目的: OCR結果を原本と見比べ、右側のシート数量を直してから保存・明細反映する。

重要:
- この画面で直すのは原則として数量。
- 施設/週が違う場合はSTEP1へ戻る。
- 編集できる表がない場合は、OCRページ再取得・再実行または詳細確認に進む。

</div><div class="shot">

![sheet edit overview](./action_screenshots_live/sheet_edit_step2_overview.png)

</div></div>

---

# シート編集: 数字が正しい場合

<div class="two"><div>

操作: `はい / 修正済み` を選ぶ。
使う場面: 左の原本/OCRと右のシート数量が一致しているとき。

分岐:
- 正しい: そのまま明細へ反映。
- 迷う/違う: `いいえ / 迷う` を選び、手修正・再取得・再実行を判断する。

</div><div class="shot">

![apply branch](./action_screenshots_live/sheet_edit_apply_button.png)

</div></div>

---

# シート編集: セルを直接直す

<div class="two"><div>

操作: 編集可能なセルまたは入力欄を選び、原本どおりの数量に直す。
注意: この注文の状態によっては `編集できる表がありません` と表示される。その場合は無理に進めず、OCR再取得/再実行または内部情報を確認する。

分岐:
- セルが編集できる: 数字を直して保存。
- セルが出ない: OCR結果・テンプレート・evidence の不足を確認。

</div><div class="shot">

![cell input](./action_screenshots_live/sheet_edit_cell_input.png)

</div></div>

---

# シート編集: 下書き保存

<div class="two"><div>

操作: `シートを保存（暫定）` を押す。
使う場面: 数字を手で直したあと、明細へ進む前に下書きとして保存する。

分岐:
- 保存成功: `明細へ反映` または `次へ: 明細` へ進む。
- 保存不可: 表がない、OCR evidenceがない、通信/入力エラーを確認する。

</div><div class="shot">

![sheet save](./action_screenshots_live/sheet_edit_save_button.png)

</div></div>

---

# シート編集: OCRを再取得/再実行する場合

<div class="two"><div>

操作: `OCR表示を再取得` または `OCRパイプラインを再実行` を使う。
使う場面: OCRページが取得できない、オーバーレイがない、読み取りが崩れている場合。

注意: `OCRパイプラインを再実行` は副作用がある。保存済み下書きがある場合は、再実行前に対象注文IDと作業状態を確認する。

</div><div class="shot">

![ocr retry](./action_screenshots_live/sheet_edit_step2_overview.png)

</div></div>

---

# シート編集: 明細へ進む

<div class="two"><div>

操作: `次へ: 明細` を押す。
条件: シート数量の正誤判断が終わっていること。

分岐:
- 下書きが明細未反映: 上部の `明細へ反映` を使う。
- 数字に迷いが残る: STEP2で止め、原本・OCR・内部情報を確認する。

</div><div class="shot">

![sheet next](./action_screenshots_live/sheet_edit_next_button.png)

</div></div>
