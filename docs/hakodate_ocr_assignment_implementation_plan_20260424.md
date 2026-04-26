# 箱館方式 OCRセル割当 実装計画と検証改革

日付: 2026-04-24

対象:

- OCR pipeline
- `orders/{id}` 注文詳細
- `ocr-sheet`
- `draft-sheet`
- `workflow-state`
- 施設別FAXテンプレート
- 施設区分由来の数量列定義
- 週次メニュー由来の行ラベル

## 1. 結論

現行方式の主問題は、数字OCR精度ではなく、OCRが推定した表構造をそのまま数量セル割当に使っていることにある。

箱館方式では、yomitokuの表構造を数量割当の正本にしない。施設ごとに1つの固定FAXテンプレートを持ち、表範囲、列境界、行境界、数量列、行番号を施設テンプレートで先に確定する。週次メニューはテンプレート作成には使わず、実行時に固定行番号へメニュー名を差し込むだけにする。OCRは座標付きの数字候補だけを提供する補助入力に下げる。数量候補は、施設テンプレートとFAX画像のアライメント結果へ投影し、正しい数量セルに入ったものだけを採用する。

最初から置き換えるのではなく、`quantity_assignment_strategy = legacy | hakodate | both` で旧方式と箱館方式を同じ注文処理内に共存させる。初期リリースは `both` の監査専用で、旧方式の出力を変えずに差分と信頼度を蓄積する。

## 2. 固定要件

1. 正解シートの骨格はOCRに作らせない。
2. 表構造の正本は施設ごとに1つの `facility fax template` とする。
3. 週次メニューはテンプレート作成に使わず、固定行番号へのメニュー名注入だけに使う。
4. メニュー数、データ行数、朝昼夕構造は週次で変わらない前提にする。
5. 施設区分、数量列定義、列順が変わった場合はテンプレートを stale と判定し、再生成候補を作る。
6. stale テンプレートのまま自動反映しない。人間の確認後に更新する。
7. ユーザーに確認させる内容は、差分と影響だけに絞り、細かい座標編集を通常導線に出さない。
8. OCRは `text + bbox/center + confidence` を持つ候補として扱う。
9. 数量セル割当は、OCR表の列名や行名ではなく、テンプレート上のセル位置と推定格子位置で判断する。
10. yomitokuは使ってよいが、表構造推定は採用しない。
11. テンプレート解決、格子推定、セル割当の信頼度が不足したら、旧方式へ黙って戻さず `review_required` または blocker にする。
12. `draft-sheet`, `ocr-sheet`, `workflow-state` は同じ割当結果を参照する。
13. ユーザー保存済みdraftがある場合は、OCR再実行で無言上書きしない。

## 3. 禁止事項

1. yomitokuの `tables.rows` / markdown表を正解シート行として採用しない。
2. OCR都合で行や列を追加、削除、並べ替えしない。
3. メニュー名、日付、区分をOCR推定で正解シートへ上書きしない。
4. テンプレート未登録、stale、または施設区分と不一致の時に黙ってフォールバックしない。
5. 位置補正失敗時に、見た目だけ整ったraw tableをcurrent sheetとして表示しない。
6. `both` で差分を見ているだけの段階を、精度改善完了として扱わない。
7. 1件だけの局所例外で実装完了にしない。
8. 帳票様式ごとのテンプレート分岐を運用前提にしない。
9. 施設区分変更時に、確認なしで古いテンプレートへ数量を自動反映しない。

## 4. 失敗クラス

名称: `ocr_owned_structure_quantity_misassignment`

内容:

OCRエンジンが推定した表構造、行、列、結合セル、ヘッダー解釈が、正解シートの数量セル割当を支配してしまう。結果として、数字そのものが読めていても、別行、別列、日付列、補助列、備考列へ混入する。

共有決定点:

1. `ocr_pipeline/app/main.py` が yomitoku 出力の `pages`, `tables`, `table_raw`, `quantity_subgrid_passes` を生成する。
2. `backend/src/services/fax_extractor.py` の `rows_from_pipeline_payload()` が pipeline payload から表行を復元する。
3. `backend/src/services/fax_parser.py` の `parse_order_lines()` が表行を注文行へ変換する。
4. `backend/src/services/order_service.py` が `ocr-sheet`, `draft-sheet`, `workflow-state` の表示・保存・再解析で payload と saved draft を合成する。
5. `backend/src/services/position_column_mapping_service.py` が構造メタデータ不足時の位置fallbackを補うが、現状はyomitokuの構造化セルが前提になりやすい。

守るべき不変条件:

施設ごとの単一 `facility fax template` が行列骨格を所有し、OCRはその骨格上の数量セル候補へ投影されるだけである。週次メニューは固定行番号へメニュー名を付ける補助情報であり、行数やセル座標を決める正本ではない。OCRの表構造は監査情報であり、割当の正本ではない。

## 5. 現行パイプライン

主な流れ:

```text
FAX PDF
  -> page correction / template classification
  -> yomitoku
  -> markdown / structured tables / table_raw / quantity_subgrid_passes
  -> fax_extractor.rows_from_pipeline_payload
  -> fax_parser.parse_order_lines
  -> order_service current sheet bootstrap
  -> ocr-sheet / draft-sheet / workflow-state
```

現行方式の強み:

1. 既存APIとUIに統合済み。
2. yomitokuの表抽出結果が良いPDFでは、少ない実装で表形式を得られる。
3. `position_column_mapping_service` により、構造不足時のfallback基盤が一部存在する。

現行方式のボトルネック:

1. yomitokuが作った表構造がズレると、後段がそのズレを正本として扱いやすい。
2. FAXの非一様な歪み、横方向伸縮、列ごとのズレに対し、1つの表bbox補正だけでは不足する。
3. ヘッダーOCRや列名OCRに依存すると、`常食`, `肉禁`, `魚禁`, `変更` の列判定が不安定になる。
4. 数字候補が読めていても、その数字が正しい数量セル内にあるかを独立に検証していない。
5. `main_ocr_provider` と数量割当方式が混線しており、OCRエンジン選択とセル割当戦略を独立に差し替えにくい。

## 6. 箱館方式のパイプライン

主な流れ:

```text
FAX PDF
  -> render image
  -> facility fax template resolution
  -> facility template signature check
  -> deskew / scale / translation / piecewise x-y alignment
  -> template cell centers and cell polygons transformed to actual FAX
  -> OCR token extraction
  -> numeric token center-in-cell assignment
  -> canonical sheet row_id + quantity_field mapping
  -> ocr-sheet / draft-sheet / workflow-state
```

責務分離:

1. `ocr_engine`: 数字候補を読む。yomitoku, Tesseract, PaddleOCRなどを差し替え可能にする。
2. `structure_source`: 施設・週・テンプレート・メニューから正解骨格を生成する。
3. `facility_template_resolver`: 施設ごとの単一テンプレートを取得し、施設区分との一致を検証する。
4. `alignment_engine`: FAX画像を施設テンプレート座標系へ近づける。
5. `grid_estimator`: 実FAX上の罫線は補助信号として使い、セル境界とセル中心の正本は施設テンプレートから変換する。
6. `quantity_assigner`: 数字候補を正解シートの数量セルへ投影する。
7. `quality_gate`: 自動採用、review、blockerを決める。

## 7. yomitokuの扱い

yomitokuを完全に捨てる必要はない。ただし、使い方を限定する。

採用してよいもの:

1. OCRテキスト。
2. 単語または文字列のbbox。
3. 数字候補の中心座標。
4. confidenceが取れる場合の候補スコア。

採用しないもの:

1. yomitokuが推定した表の行数、列数。
2. yomitokuが推定したヘッダー列名。
3. yomitokuのセル結合解釈。
4. yomitokuのmarkdown表を正解シート行として使うこと。

代替OCR:

1. Tesseractはローカル運用しやすく、数字候補のbbox取得に向く。
2. PaddleOCRはOSSローカル運用が可能で、角度や検出に強い可能性があるため比較対象にする。
3. docTRやEasyOCRは候補だが、日本語FAXと数字bboxの安定性を実測してから採用判断する。
4. OCRエンジンの比較軸は文字認識精度ではなく、数量候補bboxの安定性、速度、ローカル運用性、再現性とする。

## 8. 位置補正方針

FAXは印刷、再スキャン、FAX送受信、縦横伸縮、局所歪みにより、単純な平行移動や一様スケールでは合わない。補正は段階化し、各段階で根拠を記録する。

補正段階:

1. ページ向き補正。
2. deskew。
3. 全体bboxによる粗いscale / translation。
4. テンプレート罫線と実FAX罫線の対応点抽出。
5. 横方向piecewise補正。
6. 必要なら縦方向piecewise補正。
7. セル境界検出後、各列・各行の残差を評価。

重要な判断:

1. 補正が完璧である前提を置かない。
2. 補正結果は `alignment_quality` として数値化する。
3. 列ごとの残差が閾値を超えた場合、その列の自動採用を止める。
4. セル中心と数字中心の距離、セル内包含率、隣接セル境界までの余白を信頼度に入れる。
5. FAXにテンプレート枠を重ねたoverlayを必ず成果物として保存し、人が確認できるようにする。

## 8.1 施設テンプレートのライフサイクル

施設テンプレートは、施設ごとに1つだけ持つ。週次、メニュー内容、帳票様式名では分岐しない。メニュー数とデータ行数は週次で変わらない前提なので、テンプレートはセル地図だけを持つ。

テンプレートに含めるもの:

1. 表全体の基準矩形。
2. 列境界、列中心、列ロール。
3. 行境界、行中心、ヘッダー行数、データ行数。
4. 固定行番号。例: `day_index`, `daypart`, `meal_row_index`。
5. 数量列マッピング。例: `qty.regular`, `qty.no_meat`, `qty.no_fish`。
6. セルID。例: `r023:c007` または `day0_lunch_2:qty.no_meat`。
7. `template_signature`。

テンプレートに含めないもの:

1. 週次メニュー名。
2. OCRで読んだ日付、区分、メニュー名。
3. 実FAXごとの平行移動、伸縮、歪み補正結果。
4. 帳票様式ごとの分岐キー。

`template_signature`:

施設テンプレートが現在の施設区分と一致しているかを判断する署名。少なくとも以下を正規化してhash化する。

1. 施設ID。
2. 施設区分、数量列定義、数量列順。
3. データ行数。
4. 列ロールの並び。
5. 行ロールの並び。

stale判定:

1. 現在の施設区分から再計算したsignatureが保存済みテンプレートと一致すれば使用可能。
2. 一致しなければ `template_stale_due_to_facility_category_change` として自動反映を止める。
3. stale時は、既存テンプレートの幾何情報を引き継ぎ、新しい施設区分に合わせた再生成候補を作る。
4. 再生成候補は自動保存しない。人間が確認して保存する。

施設区分変更時の再生成方針:

1. 行数と表位置は既存テンプレートから引き継ぐ。
2. 数量列の意味と列順だけを現在の施設区分から再構成する。
3. 列数が増減する場合は、既存列境界から候補を生成し、確認UIで差分を強調する。
4. 確認済みになるまで `hakodate` の自動反映は blocker にする。

## 8.2 テンプレート確認UI/UX

目的は、ユーザーに座標編集をさせることではなく、「この施設テンプレートを使ってよいか」を短時間で判断してもらうこと。

通常ユーザーに見せる確認項目:

1. 施設区分が変更されました。
2. 数量列がどう変わったか。例: `常食, 肉禁, 魚禁` から `常食, 肉禁, 魚禁, 刻み`。
3. 新しいテンプレート候補の枠がFAXに重なっているか。
4. サンプル数量セル数個に数字が正しく入っているか。
5. `承認して保存` または `保留して手修正` の2択。

通常ユーザーに見せない項目:

1. 生の `grid_column_edges`。
2. 生の `grid_row_edges`。
3. OpenCVパラメータ。
4. OCR token JSON。
5. bbox座標の直接編集。

詳細調整は管理者向け折りたたみUIに限定する。通常導線では、overlay上の「表がずれている」「列が違う」「行が違う」の3つの理由を選べれば十分にする。

確認完了後の保存:

1. 承認時に `template_signature` を現在の施設区分で更新する。
2. 保存者、保存日時、元テンプレートsignature、再生成理由を履歴に残す。
3. 保存後に同じ注文で比較APIを再実行し、blockerが消えたことを表示する。

## 9. 戦略カプセル化

`main_ocr_provider` とは別に、数量セル割当戦略を追加する。

設定案:

```text
quantity_assignment_strategy = legacy | hakodate | both
```

`legacy`:

現行方式。yomitoku/pipeline payload の表構造と既存fallbackを使う。

`hakodate`:

箱館方式だけを使う。yomitoku表構造は割当に使わない。品質ゲートに落ちたら `review_required` または blocker にする。

`both`:

旧方式と箱館方式を両方実行し、旧方式の出力は変えずに差分、信頼度、overlayを保存する。初期導入はこのモードを必須にする。

選択優先順位:

1. 注文単位の明示指定。
2. 施設設定。
3. 環境変数。
4. デフォルトは当面 `legacy`。

## 10. 出力スキーマ案

pipeline payload に追加するトップレベルキー:

```json
{
  "quantity_assignment_strategy": "both",
  "hakodate_assignment": {
    "version": "1",
    "status": "audit_only",
    "facility_id": "FAC00001",
    "template_id": "fax_layout_regular_forbidden_v1",
    "week_id": "2026-W18",
    "page_index": 1,
    "ocr_engine": "yomitoku_word_bbox",
    "structure_source": "facility_week_template_menu",
    "alignment": {
      "method": "table_bbox_plus_piecewise_grid",
      "deskew_angle_deg": 2.0038,
      "x_line_residual_px_max": 3.0,
      "x_line_residual_px_median": 0.0,
      "y_line_residual_px_max": null,
      "quality": "review"
    },
    "grid": {
      "x_lines": [],
      "y_lines": [],
      "cell_count": 0,
      "quantity_cells": []
    },
    "assignments": [],
    "rejected_candidates": [],
    "metrics": {},
    "warnings": []
  }
}
```

`assignments` の最小項目:

```json
{
  "row_id": "2026-04-27:lunch:menu-001",
  "date": "2026-04-27",
  "daypart": "昼",
  "menu_name": "サワラの揚げ浸し",
  "field": "qty.regular",
  "value_text": "30",
  "value_normalized": 30,
  "ocr_bbox": [1390, 3600, 1420, 3630],
  "ocr_center": [1405, 3615],
  "cell_bbox": [1292, 3581, 1530, 3662],
  "cell_center": [1411, 3621],
  "distance_px": 8.5,
  "confidence": 0.91,
  "decision": "assigned"
}
```

`quality_gate` の判定値:

1. `auto_assignable`: 自動採用可能。
2. `review_required`: overlay付きで人の確認が必要。
3. `blocked`: テンプレート、週、格子、または補正が不十分。
4. `audit_only`: `both` モードで比較だけ実施。

## 11. 実装フェーズ

### Phase 0: 基準データと計測の固定

目的:

現在の問題と改善幅を測る基準を固定する。

作業:

1. 4/26-4/30 全施設FAXを検証コーパスに登録する。
2. 各PDFに `facility_id`, `template_id`, `week_id`, `page_index` を紐付ける。
3. 正解ラベルは「数字値」ではなく「どの数量セルへ入るべきか」を中心に作る。
4. `Document (2).pdf` のような局所歪みケースを代表サンプルとして固定する。
5. 旧方式、structure-guided、箱館方式候補の比較メトリクスを同一形式で出す。

完了条件:

1. 全施設の対象PDFリストが固定されている。
2. 少なくとも各施設1件以上に、数量セル割当の期待ラベルがある。
3. 旧方式の現状スコアが再現可能に出る。

### Phase 1: OCR token adapter

目的:

OCRエンジンとセル割当を分離する。

作業:

1. yomitoku word bbox を `OcrToken` に正規化する。
2. Tesseract token bbox adapter を追加する。
3. PaddleOCR adapter は実験オプションとして追加する。
4. 数字候補抽出を共通化し、純数値、全角数字、OCR混入文字の扱いを固定する。

完了条件:

1. どのOCRエンジンでも `text`, `bbox`, `center`, `confidence`, `page_index` が同じ形で出る。
2. OCR表構造なしで数字候補一覧を得られる。

### Phase 2: template skeleton resolver

目的:

シート骨格をOCRから独立させる。

作業:

1. 施設ごとの単一 `facility fax template` から `SheetSkeleton` を生成する。
2. 各数量セルに `row_id`, `day_index`, `daypart`, `meal_row_index`, `field`, `template_cell_id` を付ける。
3. 週次メニューは `row_id` に対する `date` と `menu_name` の注入だけに使う。
4. `template_signature` を現在の施設区分、数量列定義、列順から再計算する。
5. 保存済みテンプレートとsignatureが一致しなければ stale blocker にする。
6. stale時は再生成候補を作るが、自動保存・自動反映はしない。
7. 保存済みdraftがある場合は、現行ルールどおり手動選択なしに上書きしない。

完了条件:

1. OCRなしで空数量の正解シート骨格が生成できる。
2. 週次メニューなしでも行列構造が生成できる。
3. 週次メニューを与えると固定行番号にメニュー名だけが注入される。
4. 施設区分変更時に stale blocker と再生成候補が出る。
5. 既存 `ocr-sheet`, `draft-sheet`, `workflow-state` が同じ骨格IDを参照できる。

### Phase 2.5: facility template regeneration workflow

目的:

施設区分変更時に、テンプレートの再生成と人間確認をシンプルに行えるようにする。

作業:

1. 施設区分、数量列、列順の変更から `template_signature` 不一致を検出する。
2. 既存テンプレートの表範囲、行境界、列境界を引き継いだ再生成候補を作る。
3. 数量列の追加、削除、名称変更、順序変更を差分として要約する。
4. 注文詳細または施設設定に、再生成候補のoverlay確認UIを追加する。
5. 通常UIでは `承認して保存` と `保留して手修正` に判断を絞る。
6. 承認時にテンプレート、signature、履歴を保存する。
7. 保留時は `template_regeneration_pending` としてOCR自動反映を止める。

完了条件:

1. 施設区分変更後、古いテンプレートでhakodate自動反映されない。
2. 再生成候補が自動作成され、ユーザーは差分とoverlayだけで判断できる。
3. 承認後、同じ注文で再比較すると stale blocker が消える。
4. 保留した場合、legacy表示は維持しつつhakodate反映はblockされる。

### Phase 3: alignment and grid estimator

目的:

FAX画像上の数量セル位置を推定する。

作業:

1. 施設テンプレートから基準罫線、セル境界、セル中心を読み込む。
2. 実FAX画像から罫線をOpenCV morphologyで抽出する。
3. 罫線対応から全体補正とpiecewise補正を行う。
4. 施設テンプレートのセル境界、セル中心、セルbboxを実FAX座標へ変換する。
5. 実FAX側の罫線検出は、テンプレート変換結果の検証と残差計測に使う。
6. overlay画像を保存する。

完了条件:

1. `alignment_quality` が数値で出る。
2. 列ごとの横残差、行ごとの縦残差が出る。
3. セル境界の正本が実FAX検出結果ではなく施設テンプレート由来である。
4. 信頼度不足時は `review_required` または `blocked` になる。

### Phase 4: quantity assigner

目的:

数字候補を正解数量セルへ投影する。

作業:

1. OCR数字中心が推定セルpolygonに含まれるか判定する。
2. 数量列以外のセル内数字は rejected とする。
3. 同一セルに複数候補がある場合のルールを固定する。
4. セル境界近傍、隣接セル衝突、列残差大の候補は review に落とす。
5. `row_id + field` を主キーとして assignment を出す。

完了条件:

1. 数字そのものの正誤とは独立に、どのセルへ載せるかを判定できる。
2. 数量列外の数字混入を明示的に除外できる。

### Phase 5: backend integration

目的:

旧方式と箱館方式を選択可能にする。

作業:

1. `quantity_assignment_strategy` を設定として追加する。
2. `legacy`, `hakodate`, `both` の分岐を1箇所に集約する。
3. `both` では旧方式のユーザー表示を変えず、箱館結果を監査情報として保存する。
4. `hakodate` では箱館結果だけを current sheet quantity overlay に使う。
5. `draft-sheet`, `ocr-sheet`, `workflow-state` のpayload参照を揃える。

完了条件:

1. 戦略切替が注文単位または施設単位でできる。
2. 旧方式の挙動を壊さず `both` の比較結果を保存できる。
3. `hakodate` 選択時にyomitoku構造表へ戻る危険fallbackがない。

### Phase 6: UI and operator review

目的:

自動採用できないケースを人が確認できるようにする。

作業:

1. FAX画像に推定セル中心と数量候補を重ねたoverlayを表示する。
2. `assigned`, `review_required`, `rejected`, `blocked` を区別して表示する。
3. operator が候補を採用、修正、破棄できるようにする。
4. 手動確定結果は `evidence_run_id` と紐付けて監査可能にする。
5. 施設区分変更によるstale時は、通常ユーザーには数量列差分とoverlayだけを表示する。
6. 通常ユーザーの主操作は `承認して保存` と `保留して手修正` に絞る。
7. 詳細な列境界、行境界、OpenCVパラメータは管理者向け詳細UIに閉じる。

完了条件:

1. 補正が正しかったかを人が画像で確認できる。
2. 手動確定後、reloadしても同じdraftが表示される。
3. 施設区分変更時のテンプレート再生成確認が、座標編集なしで完了できる。

## 12. 検証テスト改革

現状のテストは、OCR出力から最終シートがそれらしく生成されるかに寄りやすい。箱館方式では、セル割当を独立した検証単位にする。

### テスト階層

1. Geometry unit tests。
2. OCR token adapter tests。
3. Template skeleton contract tests。
4. Assignment unit tests。
5. Pipeline integration tests。
6. Corpus regression tests。
7. UI review flow tests。
8. Live/staging parity tests。

### Geometry unit tests

検証内容:

1. deskew角度が既知の画像で正しい。
2. x方向piecewise補正後、対応罫線残差が閾値内。
3. y方向piecewise補正後、対応罫線残差が閾値内。
4. 一部罫線欠損時に、推定不能セルを review に落とす。
5. 横方向だけ合って縦方向が崩れた場合、自動採用しない。

### OCR token adapter tests

検証内容:

1. yomitoku word bbox から `OcrToken` が生成される。
2. Tesseract TSV から `OcrToken` が生成される。
3. 全角数字が正規化される。
4. `#10`, `11月`, `青魚3` のような混入文字は数量候補として扱う条件を固定する。
5. token中心がページ座標系に正規化される。

### Template skeleton contract tests

検証内容:

1. 施設テンプレートだけでOCRなしの骨格ができる。
2. 週次メニューなしでも行数、行ID、列ID、数量セルIDが決まる。
3. 週次メニューを渡すと、固定行IDに `date` と `menu_name` だけが注入される。
4. 週次メニュー内容が変わっても、行数、列数、セル座標、数量列マッピングは変わらない。
5. 数量列の `field` が施設区分定義と一致する。
6. `template_signature` が現在の施設区分と一致する場合だけ使用可能になる。
7. 施設区分、数量列、列順が変わった場合、stale blocker になる。
8. 保存済みdraftがある場合、OCR再実行で無言上書きされない。

### Facility template regeneration tests

検証内容:

1. 施設区分変更時に `template_stale_due_to_facility_category_change` が出る。
2. stale時に既存テンプレートの表範囲、行境界、列境界を引き継いだ再生成候補が作られる。
3. 数量列追加時に、追加列と影響セル数が差分として返る。
4. 数量列削除時に、削除列と影響セル数が差分として返る。
5. 数量列順変更時に、旧列順と新列順が差分として返る。
6. 再生成候補は自動保存されない。
7. 承認APIを呼ぶまで `hakodate` 自動反映は blocked のまま。
8. 承認後、signatureが更新され、同じ注文で stale blocker が消える。
9. 保留時は `template_regeneration_pending` が残り、legacy表示は維持される。
10. UIは通常導線で `承認して保存` と `保留して手修正` の2操作だけで完了できる。

### Assignment unit tests

検証内容:

1. 数字中心が数量セル内にある場合、その `row_id + field` に割り当たる。
2. 数字中心が日付列、区分列、メニュー列、備考列にある場合は rejected になる。
3. 数字中心がセル境界近傍の場合は review になる。
4. 同一セルに複数数字候補がある場合は review になる。
5. 同一数字候補が複数セルに近い場合は review になる。
6. 行方向ズレで隣接メニュー行に近い場合は review になる。
7. 列方向ズレで数量列境界を跨ぐ場合は review になる。

### Pipeline integration tests

検証内容:

1. `legacy` では現行出力が変わらない。
2. `both` では旧方式出力を変えず、箱館結果とdiffが保存される。
3. `hakodate` では箱館assignmentだけが数量overlayに使われる。
4. 箱館が blocked の場合、yomitoku構造表へ黙って戻らない。
5. `ocr-sheet`, `draft-sheet`, `workflow-state` が同じstrategyとassignment_idを返す。
6. 施設区分変更でテンプレートがstaleの場合、`both` は比較情報だけを返し、`hakodate` 反映は blocked になる。
7. テンプレート再生成承認後、同じ注文の比較APIで stale blocker が消える。

### Corpus regression tests

対象:

1. 4/26-4/30 全施設FAX。
2. `Document (2).pdf`。
3. `19.fax000364233_0426_0501_.pdf`。
4. きれいなスキャン。
5. 横伸縮が強いFAX。
6. 非一様歪みがあるFAX。
7. 罫線が薄いFAX。
8. 複数ページFAX。

主メトリクス:

1. `cell_assignment_precision`: 採用した数量候補が期待セルに入った率。
2. `cell_assignment_recall`: 期待数量セルのうち候補が入った率。
3. `wrong_cell_rate`: 数字を間違った数量セルへ入れた率。
4. `non_quantity_rejection_rate`: 非数量セル内数字を除外できた率。
5. `review_rate`: 自動採用せず人確認へ落とした率。
6. `blocked_rate`: テンプレート、格子、補正不足で止めた率。
7. `alignment_residual_px_p95`: 罫線対応残差の95パーセンタイル。
8. `processing_time_sec`: 1ページあたり処理時間。

合格基準の初期案:

1. `wrong_cell_rate` は旧方式より明確に低いこと。
2. `cell_assignment_precision` を最優先し、recallより優先する。
3. 自動採用できない場合は review/blocker に落ちること。
4. 処理時間は初期目標として1ページ30秒以内、監査バッチは1ページ60秒以内を目安にする。
5. 速度よりも wrong cell を出さないことを優先する。

## 13. `Document (2).pdf` の現時点実測

対象:

`/Users/mmorinag/Downloads/Document (2).pdf`

ローカル成果物:

`/Users/mmorinag/Sawa/2025.12/workspace/backend/tmp/document2_structure_compare_20260424/`

旧方式に近いyomitoku構造化:

1. table: 58行、11列。
2. non-empty cells: 164。
3. pure numeric cells: 34。
4. 数量列以外にも `000`, `245`, `315`, `49`, `88` などの数字ノイズが混入。

structure-guided初期:

1. table: 58行、11列。
2. non-empty cells: 151。
3. pure numeric cells: 29。
4. 横方向が合わず、列割当が悪化するケースがあった。

piecewise X補正後:

1. 補正前の縦罫線対応誤差は最大67px、median 35.5px。
2. piecewise X補正後の縦罫線残差は `[-3, 0, 1, -1, 0, 0, 0, 0, 0, 0, 0, 0]`。
3. 横方向の列位置は大きく改善した。

推定セル中心方式:

1. 検出x_lines: 12本。
2. 検出y_lines: 59本。
3. 推定セル中心: 638個。
4. OCR数字候補: 48。
5. 推定セル内: 47。
6. review: 1。
7. 数量列扱い候補: 32。
8. 数量列外の数字候補: 16。

解釈:

この結果は、数字OCR精度が上がった証拠ではない。正しいセル領域に入った数字だけを見ることで、数量セル候補と非数量ノイズを分離できる可能性があるという証拠である。

未解決:

1. y方向piecewise補正の評価がまだ不足。
2. 複数施設、悪いFAX、罫線欠損FAXで同じ品質が出るか未検証。
3. 処理時間の正式計測が不足。
4. 正解ラベル付きの全施設評価が未完了。

## 14. 品質ゲート

自動採用条件:

1. facility, week, template が一意に解決済み。
2. skeleton の行数、数量列、row_id が生成済み。
3. 罫線残差が閾値内。
4. 数量列セルpolygonが生成済み。
5. 数字中心が1つの数量セル内に安定して入る。
6. 同一セル内の候補衝突がない。
7. `draft-sheet`, `ocr-sheet`, `workflow-state` のassignment参照が一致する。

review条件:

1. 数字中心がセル境界に近い。
2. 列残差または行残差が高い。
3. 同一セルに複数候補がある。
4. yomi, Tesseract, PaddleOCR の候補位置が大きく違う。
5. 数量セル内だが数値として異常値の可能性がある。

blocker条件:

1. facilityが解決不能。
2. weekが解決不能。
3. 施設テンプレートが未登録。
4. template_signature が現在の施設区分と一致しない。
5. テンプレート再生成候補が未承認。
6. skeletonが生成できない。
7. 罫線検出が不足し、テンプレートとのアライメント品質を検証できない。
8. FAX画像とテンプレートの対応が成立しない。
9. 保存済みdraftとのcanonical context衝突があり、operator choiceが未確定。

## 15. 実装候補ファイル

新規または拡張候補:

1. `ocr_pipeline/app/hakodate_assignment.py`
2. `ocr_pipeline/app/ocr_token_adapter.py`
3. `ocr_pipeline/app/grid_estimator.py`
4. `ocr_pipeline/app/alignment_quality.py`
5. `backend/src/services/hakodate_assignment_service.py`
6. `backend/src/services/quantity_assignment_strategy_service.py`
7. `backend/tests/fixtures/ocr_assignment_corpus/`

接続点:

1. `ocr_pipeline/app/main.py`: `hakodate_assignment` artifact をpayloadへ追加する。
2. `backend/src/services/ocr_pipeline_service.py`: pipeline output の追加スキーマを受け取る。
3. `backend/src/services/fax_extractor.py`: `rows_from_pipeline_payload()` でhakodate payloadをraw table化しない。
4. `backend/src/services/order_service.py`: strategy選択、sheet overlay、workflow parity を集約する。
5. `backend/src/services/draft_sheet_service.py`: current draft とassignmentの関係を保存する。
6. `backend/src/services/workflow_state_service.py`: `hakodate_assignment` の品質状態を表示状態へ反映する。
7. `frontend/src/pages/orders/[id].tsx`: overlayとreview UIを追加する。

## 16. ロールアウト計画

Stage 1: local batch audit

全施設FAXに対して `legacy` と `hakodate` を比較する。ユーザー表示は変えない。

Stage 2: staging `both`

ステージングで `both` を有効化し、注文詳細からoverlayとdiffを確認できるようにする。

Stage 3: facility opt-in

施設単位で `hakodate` を有効化する。ただし初期は review required を多めに許容し、wrong cell を出さない。

Stage 4: default candidate

十分なコーパスで `wrong_cell_rate` が旧方式より低く、review/blockerの運用が成立した施設からデフォルト候補にする。

Stage 5: legacy retirement decision

旧方式は監査比較用に残し、危険fallbackとして自動採用されないように段階的に縮退する。

## 17. 未解決リスク

1. 罫線が薄い、欠けている、FAXノイズで切れている場合のgrid推定。
2. 行方向の局所歪みが強い場合のmenu行割当。
3. テンプレートPDF生成時のプリンタ依存の縦横伸縮。
4. OCR token bbox自体がズレる場合の扱い。
5. 処理時間が注文処理に耐えるか。
6. 複数ページFAXで対象ページを安定選択できるか。
7. 保存済みdraft、candidate evidence、current evidence の混線。
8. 人確認UIがない状態で `review_required` が増える運用負荷。

## 18. 実装開始前の必須チェックリスト

1. 対象施設と対象週を固定する。
2. テンプレート正本を固定する。
3. 旧方式のbaselineを保存する。
4. 箱館方式の出力スキーマを固定する。
5. `legacy | hakodate | both` の選択場所を1箇所に決める。
6. `both` のユーザー表示非変更をテストで固定する。
7. blocker時に旧方式へ黙って戻らないことをテストで固定する。
8. overlay画像の保存先と保持期間を決める。
9. 処理時間の計測ログを必ず入れる。
10. `draft-sheet`, `ocr-sheet`, `workflow-state` のparityテストを先に書く。

## 19. 完了条件

実装全体の完了条件:

1. 全施設の対象FAXで、旧方式と箱館方式の比較レポートが出る。
2. `quantity_assignment_strategy` で処理を切り替えられる。
3. `both` では旧方式の表示を変えず、箱館方式の結果、diff、overlayを保存できる。
4. `hakodate` ではyomitoku表構造に依存せず、OCR数字候補を正解シートの数量セルへ投影できる。
5. `hakodate` の品質不足時は `review_required` または blocker になり、危険fallbackでシートを作らない。
6. `ocr-sheet`, `draft-sheet`, `workflow-state` が同じassignment結果を返す。
7. reported case と sibling case と blocker case のテストが常設される。
8. 速度、precision、recall、wrong cell、review、blocked のメトリクスがCIまたはバッチで再現可能に出る。

この条件を満たすまでは、箱館方式は本番の正方式ではなく、監査または施設限定opt-inとして扱う。
