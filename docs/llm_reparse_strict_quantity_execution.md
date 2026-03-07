# LLM例外推論OCR: 数量厳格化 実装実行記録

## 1. 要件定義（テキスト化）
1. `reparse_order` の LLM経路（`ocr_provider=openai/gemini` または `llm_assist=true`）では、数量判定を strict モードにする。
2. strict モードでは `0` を有効値として保持し、`zero_as_empty=false` を強制する。
3. strict モードでは数量セルは純数値セルのみ許可し、混在文字列（例: `副23`）は数量として採用しない。
4. strict モードでは異常値を上限で除外する（`OCR_SHEET_MAX_QTY`、既定150）。
5. strict モードは reparse の一次経路だけでなく、markdown/table_rows フォールバック経路にも一貫適用する。
6. 非LLM経路の既存挙動（互換性）は維持する。

## 2. 実装計画
1. `fax_parser.parse_order_lines` に strict 数量ルール拡張を追加する。
2. `order_service.reparse_order` で LLM条件時のみ strict ルールを生成・注入する。
3. `_build_sheet_lines_from_ocr_payload` に `quantity_rules` 引数を追加し、フォールバック経路へ同ルールを伝播する。
4. 回帰テストを追加し、`0` 保持・混在文字拒否・異常値拒否・非strict互換を固定する。
5. 既存の `ocr_sheet` 回帰/契約テストを再実行し副作用がないことを確認する。

## 3. 実装結果（計画に対する実施）
1. 完了: `fax_parser._parse_number` を strict 対応化し、`strict_numeric_quantity_cell` と `max_quantity_abs` を導入。
2. 完了: `parse_order_lines` で strict ルールを解釈し、数量/訂正数量の両列で適用。
3. 完了: `order_service._build_reparse_quantity_rules` を追加し、LLM経路で `zero_as_empty=false` + strict ルールを強制。
4. 完了: `reparse_order` の一次/markdownフォールバック/payloadフォールバックすべてで同一 `quantity_rules` を適用。
5. 完了: `_build_sheet_lines_from_ocr_payload` に `quantity_rules` を追加し再利用可能化。

## 4. テスト計画と結果
### 4.1 追加テスト
1. `test_reparse_order_openai_enforces_strict_quantity_rules`
2. `test_parse_order_lines_strict_numeric_keeps_zero_and_rejects_noise`
3. `test_parse_order_lines_non_strict_still_parses_mixed_numeric_text`

### 4.2 実行コマンド
1. `uv run pytest tests/integration/test_ocr_pipeline.py -q`
2. `uv run pytest tests/integration/test_ocr_sheet_history.py -q`
3. `uv run pytest tests/contract/test_orders_ocr_sheet_history_api.py -q`

### 4.3 実行結果
1. `tests/integration/test_ocr_pipeline.py`: `4 passed`
2. `tests/integration/test_ocr_sheet_history.py`: `50 passed`
3. `tests/contract/test_orders_ocr_sheet_history_api.py`: `7 passed`

## 5. 完了判定
1. 要件テキスト化: 完了
2. 実装計画作成: 完了
3. 計画準拠で実装: 完了
4. テスト実行と成功確認: 完了
