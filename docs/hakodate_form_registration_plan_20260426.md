# 箱館方式 Form Registration 実装・検証・テスト計画

## 目的
OCRが作った構造ではなく、テンプレートPDF画像とFAX PDF画像の罫線格子を対応付けることで、テンプレート上の数量セルをFAX上の同じ row_index / col_index のセルへ写像する。

## 固定要件
- 構造合わせでは OCR 文字、メニュー名、数字、Yomitoku 構造、Tesseract word bbox を使わない。
- テンプレートPDF画像とFAX画像から、外枠、縦罫線、横罫線、交点、太線を抽出して合わせる。
- 全数量セルは、テンプレート側の row_index / col_index を維持したままFAX上のセル polygon または bbox に写像する。
- 非一様歪みを前提にする。ただし現段階の auto 成功経路では、根拠が弱い piecewise 補間を使わない。まず検出線の順序一致、次に外枠へテンプレートedgeを投影する Form Registration を使い、ズレは overlay で止める。
- 行列がズレている overlay/result を成功扱いしない。

## 成功条件
- FAX overlay 上で、全数量セルの枠と中心が実セル内に収まる。
- 縦罫線・横罫線・交点の対応が row_index / col_index の単調性を保つ。
- 検出または復元した row_edges / column_edges の数がテンプレート期待数と一致する。
- 行・列ごとのedge数、edge source、外枠投影の採用有無、局所歪み量が evidence に残る。
- ローカル検証対象PDFで、人間目視でも行列ズレがない overlay を生成できる。

## 実装計画
1. テンプレート構造PDF画像とFAX画像を同一DPIでレンダリングする。
2. 両画像から表外枠を検出し、FAXをテンプレート表領域へ粗合わせする。
3. 表領域内の罫線を縦横別に高感度検出する。
4. 横罫線は文字が少ない数量列・備考列帯を複数使って抽出し、検出線を統合する。
5. 縦罫線は全体投影と罫線交点密度から抽出する。
6. テンプレートの期待 row_edges / column_edges 数を制約に、検出線列とテンプレート線列を順序保持で対応付ける。
7. 欠落線は、近接する検出線とテンプレート比率から補完する。ただし補完箇所を alignment_evidence に残す。
8. 罫線が十分に取れる場合は検出線へ合わせる。劣化FAXで内部線が欠ける場合は、検出済みFAX表外枠へテンプレートedge列を縦横スケール投影して、写像根拠を `structure_table_box_projection` として残す。
9. 全数量セルに row_id, date, daypart, field, sheet_cell, fax_cell_bbox, fax_cell_center, alignment_confidence, alignment_evidence を付与する。
10. OCRは写像後の数量セルcropにだけ実行する。

## 検証計画
- 対象はまず `FAC00014` / `4月26日～4月30日` / `tmp/stg_fac14_compare/stg_corrected.pdf`。
- 以下を必ず保存する。
  - 入力FAX画像
  - テンプレート構造画像
  - 検出した外枠、縦罫線、横罫線
  - 採用した線、棄却した線、補完した線
  - 交点対応
  - 全数量セル bbox / center
  - セルcrop
  - セル単位OCR結果
  - 重ね合わせ overlay
  - registration metrics
- overlay は、実罫線、テンプレート写像セル、セル中心、補完線を色分けする。
- ローカルで行列ズレが残る場合は成功扱いしない。

## 評価指標
- table_box_error_px
- row_edge_count_expected / row_edge_count_actual
- col_edge_count_expected / col_edge_count_actual
- row_edge_rmse_px
- row_edge_max_error_px
- col_edge_rmse_px
- col_edge_max_error_px
- intersection_rmse_px
- intersection_max_error_px
- cell_center_inside_rate
- min_cell_margin_ratio
- local_row_scale_variation
- local_col_scale_variation
- complemented_row_edge_count
- complemented_col_edge_count

## テスト計画
- ユニットテスト: OCR token 抽出を monkeypatch で失敗させても registration 経路が動くこと。
- ユニットテスト: row_edges / column_edges がテンプレート期待数と一致しない場合、補完または明示的な failure になること。
- ユニットテスト: row_index / col_index の単調性が崩れた制御点は拒否されること。
- ユニットテスト: セル単位OCRのみが呼ばれ、ページ全体OCR token は呼ばれないこと。
- ローカル実PDFテスト: `FAC00014` の overlay で全数量セルが実セル内に収まること。
- ローカル実PDFテスト: `FAC00001` の劣化FAX `Document (2).pdf` で、内部線欠落時も外枠投影により数量セル中心が実セル内に収まること。
- 回帰テスト: `detected_anchor_piecewise` のような弱い補間経路を残さないこと。

## 実装上の禁止事項
- OCR文字や数字位置を構造合わせの根拠にしない。
- 特定施設・特定PDF・特定注文だけの例外補正を入れない。
- 旧方式や token bbox 方式へ黙って fallback しない。
- 目視でズレている overlay を成功扱いしない。
