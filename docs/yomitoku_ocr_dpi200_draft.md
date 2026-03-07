# yomitoku OCR DPI=200 現状メモ（たたき台）

## 目的
FAX PDFのOCR精度が低いケースがあり、どの施設・どのテンプレで崩れるのかを整理し、改善方針をChatGPTに相談するための下書き。

## 現状のOCRフロー（ローカル検証）
1. `input_example/*.pdf` を対象に `yomitoku-test/run_yomitoku_batch.py` を実行。
2. `pypdfium2` でPDFをDPI指定で画像化。
3. `yomitoku.DocumentAnalyzer` でOCR＋レイアウト解析。
4. `results/<safe_name>/dpi_<dpi>/page_XX/` に以下を出力。
   - `page.md`（OCRのMarkdown）
   - `ocr_overlay.png`（OCRオーバーレイ）
   - `layout_overlay.png`（レイアウトオーバーレイ）
   - `figures/*.png`（切り出し画像）
5. `yomitoku-test/yomitoku_config.json` は現状デフォルト（空）で調整なし。

再現コマンド（DPI=200のみ例）:
```bash
cd /Users/mmorinag/Sawa/2025.12/yomitoku-test
task yomitoku-run -- --dpi 200
```

## DPI=200 出力対応表
| PDF | 出力ディレクトリ（dpi_200） | 評価 | メモ |
| --- | --- | --- | --- |
| `input_example/11.fax000312975 1214~.pdf` | `yomitoku-test/results/11.fax000312975_1214_412ca3dc/dpi_200` | ◎ | 表ヘッダ・数値が比較的安定（山城グループホーム） |
| `input_example/12.fax000311981 1214~.pdf` | `yomitoku-test/results/12.fax000311981_1214_2876fed5/dpi_200` | ○ | 表は出るがヘッダ崩れ（四万十ピア） |
| `input_example/13.fax000313450 1214~.pdf` | `yomitoku-test/results/13.fax000313450_1214_83782a2e/dpi_200` | ○ | 列名の崩れが目立つ（グランフォレスト） |
| `input_example/14.fax000313791 1214-1.pdf` | `yomitoku-test/results/14.fax000313791_1214-1_ad9492a2/dpi_200` | ○ | 施設名の誤読あり、列名も揺れ |
| `input_example/5.fax000312396 1214~.pdf` | `yomitoku-test/results/5.fax000312396_1214_a090d0ee/dpi_200` | ○ | 表は出るが列名の誤読が多い |
| `input_example/6.fax000312394 1214-1.pdf` | `yomitoku-test/results/6.fax000312394_1214-1_e920faaa/dpi_200` | ○ | 表は出るが列名の誤読が多い |
| `input_example/9.fax000312489 1214-2.pdf` | `yomitoku-test/results/9.fax000312489_1214-2_26bb706b/dpi_200` | ○ | 数値が英字に化ける箇所あり |
| `input_example/1.fax000313361 1214~.pdf` | `yomitoku-test/results/1.fax000313361_1214_e9c28b21/dpi_200` | △ | ヘッダ/値とも崩れが大きい |
| `input_example/4.fax000301980 1214~.pdf` | `yomitoku-test/results/4.fax000301980_1214_cf8ce4ff/dpi_200` | △ | 列名・値が大きく崩れる |
| `input_example/7.fax000310425 1214~.pdf` | `yomitoku-test/results/7.fax000310425_1214_82d3f7d6/dpi_200` | △ | 列名が別物に化ける |
| `input_example/16.fax000314621 1214~.pdf` | `yomitoku-test/results/16.fax000314621_1214_784e8dcd/dpi_200` | △ | 表ヘッダがほぼ崩壊 |
| `input_example/17.fax000315280 1214-3.pdf` | `yomitoku-test/results/17.fax000315280_1214-3_5afb69be/dpi_200` | △ | 文字の繰り返しが激しく判読困難 |
| `input_example/Fureai Order Form.pdf` | `yomitoku-test/results/Fureai_Order_Form_c1dedcd0/dpi_200` | △ | テンプレが違い、表認識が崩れる |
| `input_example/12.28~発注書.pdf` | `yomitoku-test/results/12.28_2085cd11/dpi_200` | 参考 | 複数ページで形式が異なる（別用途扱い） |

## 読み取りが厳しいケース（DPI=200の抜粋）
各例は `page.md` の冒頭＋表ヘッダのみ抜粋。

**17.fax000315280 1214-3.pdf**
`yomitoku-test/results/17.fax000315280_1214-3_5afb69be/dpi_200/page_01/page.md`
```text
グルールールホーム 春花花花花花花花花花花花花花花花花花花花花花花花花...
|日 付||区 分|献立|常食||軟革||ミキサー||魚探(常食)|備考欄|
```

**16.fax000314621 1214~.pdf**
`yomitoku-test/results/16.fax000314621_1214_784e8dcd/dpi_200/page_01/page.md`
```text
【発 注 迎 絡 忠】
||||||白 $ =|||変更(1)|変型2|備考欄|
```

**7.fax000310425 1214~.pdf**
`yomitoku-test/results/7.fax000310425_1214_82d3f7d6/dpi_200/page_01/page.md`
```text
TED ブルーブホーム
|山 付|# 区 分||献立|常演||教菜||ミギサー||備考欄|
```

**4.fax000301980 1214~.pdf**
`yomitoku-test/results/4.fax000301980_1214_cf8ce4ff/dpi_200/page_01/page.md`
```text
うこ いの森 プラス
|出 付|区 分||献立|常良|納 一尿|桃食||Ty(1)|変更(2)|備考欄|
```

**1.fax000313361 1214~.pdf**
`yomitoku-test/results/1.fax000313361_1214_e9c28b21/dpi_200/page_01/page.md`
```text
ゆうゆう(株) 白々家
||日 付 区 分||献立|凌食|小|禁食【常食・小口】||変更1|変更2|備考欄|
```

**Fureai Order Form.pdf**
`yomitoku-test/results/Fureai_Order_Form_c1dedcd0/dpi_200/page_01/page.md`
```text
介護老人保健施設:3れあいの丘
|1|区 分||献立|有食|価格|(税 員|禁食||変更の|変更(2)|備考欄|
```

## 観察された傾向
- 表の罫線が薄い/かすれていると列名・罫線が崩れて認識される。
- 施設名がスタンプや手書きで載っている場合に、文字の繰り返しや誤認識が発生。
- 数値列が英字に化けるケースが多い（例: `A`, `Al`, `ED`）。
- テンプレが異なるFAX（Fureai Order Form系）は現在の前提と合わず崩れやすい。

## 相談したいこと（ChatGPTに聞きたい点）
1. yomitokuの `text_detector` / `text_recognizer` / `layout_parser` / `table_structure_recognizer` のチューニング候補（推奨パラメータや学習済みモデルの選択肢）。
2. 事前処理の有効策（自動傾き補正、コントラスト強調、二値化、線強調など）。
3. テンプレごとに最小限の自動補正で済ませる現実的な運用案。
4. 認識不良時のフォールバック（列検出だけを画像処理で補強する等）。

