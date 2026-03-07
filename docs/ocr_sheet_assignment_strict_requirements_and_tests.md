# OCR -> シート代入 厳格要件とテストカタログ

## 目的
- OCR結果をシートに反映する処理で、注文ごとの差異・日付混入・数量欠落を再発させないための要件とテストを固定する。

## 運用方針（2026-03-04 確定）
1. 品質優先で運用する。速度よりも `sheet` の整合性を優先し、品質ゲート失敗時は不採用にする。
2. 再解析失敗時は debug 情報のみ保存し、`order_lines` は更新しない（debug-only fail-safe）。
3. 回帰検証データセットは `backend/tests/fixtures/ocr_sheet_corpus/` を正本として継続利用する。
4. LLM再解析コスト目標は `soft <= 0.10 USD / order`、`hard < 1.00 USD / order` とする。
5. hard 超過時は `llm_cost_limit_exceeded` で再解析結果を不採用とし、debug情報のみ残す。

### 追加設定（LLM再解析コスト）
1. `OCR_REPARSE_COST_SOFT_LIMIT_USD`（既定: `0.10`）
2. `OCR_REPARSE_COST_HARD_LIMIT_USD`（既定: `1.00`）
3. `OCR_REPARSE_COST_ENFORCE_HARD_LIMIT`（既定: `1`）
4. `OCR_REPARSE_COST_GEMINI_FLASH_INPUT_USD_PER_1M`（既定: `0.30`）
5. `OCR_REPARSE_COST_GEMINI_FLASH_OUTPUT_USD_PER_1M`（既定: `2.50`）
6. `OCR_REPARSE_COST_GEMINI_PRO_INPUT_USD_PER_1M`（既定: `1.25`）
7. `OCR_REPARSE_COST_GEMINI_PRO_OUTPUT_USD_PER_1M`（既定: `10.0`）
8. `OCR_REPARSE_COST_OPENAI_INPUT_USD_PER_1M`（既定: `5.0`）
9. `OCR_REPARSE_COST_OPENAI_OUTPUT_USD_PER_1M`（既定: `15.0`）

## 対象
- `get_ocr_sheet`
- `PUT /orders/{id}/lines`
- `POST /orders/{id}/ocr-apply`
- `GET /orders/{id}/ocr-history`

## 用語
- `weekly_menu`: 週メニュー由来の行生成経路
- `order_lines`: DBに保存済みの注文行
- `payload`: OCR出力キャッシュ（`table_rows`/`table_raw` 等）
- `数字救済`: payloadから数量セルのみを補完する処理

## 厳格要件
1. `week_id` が解決でき、週メニューが存在する場合、`date/daypart/menu` は週メニューとテンプレートのみで生成する。
2. 上記条件で `order_lines` が1件以上ある場合、数量セルは `order_lines` のみを正とする。
3. 上記条件で `order_lines` が0件の場合のみ、payload由来の数字救済を許可する。
4. 数字救済は数量列にのみ適用し、`date/daypart/menu/remarks` を変更しない。
5. 数字救済は行追加を禁止し、既存行の数量セル更新のみ許可する。
6. 数字救済は行順・行数・`row_id` を変更しない。
7. `order_lines` があるとき、payload由来の `menu/date/daypart` を採用しない。
8. 週メニュー範囲外の日付（例: `2/1`）は表示しない。
9. 同一 `order_lines` と同一テンプレートなら、注文IDやpayload差が違っても `ocr-sheet` 出力は同一である。
10. `get_ocr_sheet` は副作用を持たない（DB更新しない）。
11. `PUT /lines` 後の `get_ocr_sheet` は更新結果を即時反映する。
12. `ocr-apply` 履歴は保持してよいが、表示優先順位は常に `order_lines` 優先を維持する。
13. 行マッチは `date+daypart+menu` を第一優先とする。
14. フォールバックしても別日付への越境マッピングは禁止する。
15. 数量パースは純数値セルのみを対象とし、自由文中の数字は原則無視する。
16. OCR異常値（例: `3000`/`8000`）は数量として採用しない。
17. `0` は有効数量として保持し、認識された `0` はシート代入で捨てない（表示上の空欄許容は別ポリシー）。
18. テンプレ差分（`qty.*_x` と `qty.*_2f/3f`）でも同一優先規則を適用する。
19. `facility_missing` などのエラーは規定コードで返す。
20. `week_id` 解決は入力順に依存せず決定的である。
21. 月跨ぎ境界（`1/31 -> 2/1`）で stale hint より OCR/line date を優先する。
22. `ocr-apply` と `lines update` の反復後も最終状態は再現可能である。
23. `source` メタ値は実際の分岐と一致する。
24. 最終表示数量の由来を監査可能にする（最低限、履歴/ログで追跡可能）。

## テスト戦略
1. ユニット: 週解決、行マッチ、数量パース、テンプレ解釈、優先順位の純粋ロジックを検証する。
2. 統合: `create_order -> payload保存 -> get_ocr_sheet` の実データ経路を検証する。
3. API契約: `PUT /lines`, `POST /ocr-apply`, `GET /ocr-sheet`, `GET /ocr-history` の整合を検証する。
4. 回帰: 既知障害（`2/1混入`, `2/15朝20欠落`, `注文ごとの差分`）を固定ケースとして常設する。
5. 性質テスト: 行順変更・ノイズ注入に対する不変条件を検証する。
6. 反復更新テスト: 複数回操作後の最終優先順位と履歴整合を検証する。

## 詳細テストケース（カタログ）
### 優先順位・混入防止
- `TC-001` `weekly_menu + order_lines + payload競合` で数量は `order_lines` 値になる。
- `TC-002` 同条件で `menu/date/daypart` は週メニューのまま。
- `TC-003` 同条件で payload内の別月行（例: `2/1`）は表示されない。
- `TC-004` 同条件で OCRノイズメニュー名は表示されない。
- `TC-005` `weekly_menu + order_linesなし + payloadあり` で数字救済が動く。
- `TC-006` 上記で非数量列は不変。
- `TC-007` `weekly_menu + order_linesなし + payloadなし` で空数量の週メニュー行のみ返る。

### エラー契約
- `TC-008` `week_unresolved` を返す。
- `TC-009` `facility_missing` を返す。
- `TC-010` `sheet_template_field_invalid` を返す。
- `TC-011` `sheet_quantity_columns_missing` を返す。
- `TC-012` `sheet_quantity_column_unmapped` を返す。

### 更新反映・履歴
- `TC-013` `PUT /lines` 後の `GET /ocr-sheet` で更新数量が即時反映される。
- `TC-014` `PUT /lines` 後に payload値へ戻らない。
- `TC-015` `POST /ocr-apply` 後も `order_lines` があれば数量は `order_lines` 優先。
- `TC-016` `ocr-apply -> lines update -> ocr-apply -> lines update` の最終値が最後の `lines update` 値。
- `TC-017` 上記で `ocr-history.revisions` が期待件数保存される。

### 注文間一貫性
- `TC-018` 同一 `order_lines` の注文A/Bで `ocr-sheet` 出力が一致する。
- `TC-019` 注文A/Bでpayloadノイズ差があっても出力一致。
- `TC-020` `week_code` が null/ありでも出力一致。

### 週解決・月跨ぎ
- `TC-021` payload行順を逆順にしても `week_id` 解決が不変。
- `TC-022` 月跨ぎ `1/31, 2/1` で line date=`2/1` がある場合 `2026-02` 解決。
- `TC-023` 上記で stale hint=`2026-01` でも `2026-02` を維持。
- `TC-024` 上記で入力順依存がない。

### テンプレ差分
- `TC-025` `qty.regular_2f/3f` テンプレでも優先順位が正しい。
- `TC-026` `qty.regular_x` テンプレでも同じ優先順位が正しい。
- `TC-027` area欠落時に `X` 列フォールバックが仕様どおり。
- `TC-028` 同diet列1本時の単一列フォールバックが正しい。
- `TC-029` 同diet列複数で曖昧時は `unmapped` 扱い。

### 数量パース品質
- `TC-030` 全角数字を正しくパースする。
- `TC-031` カンマ・空白混在を正規化してパースする。
- `TC-032` 自由文（例: `副23`）から数量を誤抽出しない。
- `TC-033` 異常値（例: `3000`）を採用しない。
- `TC-034` `0` の扱いを全経路で一致させる。
- `TC-035` 負数入力の扱いを仕様どおりに固定する。
- `TC-036` 小数数量の表現・丸めを固定する。

### マッピング安全性
- `TC-037` date不一致の行に row-index で誤マップしない。
- `TC-038` daypart違いに誤マップしない。
- `TC-039` menu部分一致が閾値未満なら不採用。
- `TC-040` menu完全一致があれば row-index より優先。

### 出力整合
- `TC-041` `row_ids` 数と `rows` 数が常に一致。
- `TC-042` `fields/header/rows` の列数整合。
- `TC-043` `source` メタが実際の分岐と一致。
- `TC-044` `legacy_available` が仕様どおり。
- `TC-045` APIレスポンスが契約スキーマ準拠。

### 履歴・監査
- `TC-046` `GET /ocr-history` 初期状態は空履歴。
- `TC-047` `latest` が最後の `ocr-apply` 内容と一致。
- `TC-048` 履歴上限超過時に古い履歴が適切に切り捨てられる。
- `TC-049` 同一入力再実行で同一出力（冪等性）。
- `TC-050` 更新直後に古いキャッシュを返さない。
- `TC-051` 並行操作で破損しない。
- `TC-052` 複数注文同時更新で相互汚染しない。
- `TC-053` ノイズ多量payloadでも非数量列が不変。
- `TC-054` 非表データ尾部から誤数量採用しない。
- `TC-055` 週メニュー欠損時フォールバックが仕様どおり。
- `TC-056` フォールバック時も別月混入を抑制。
- `TC-057` 施設テンプレ変更後に既存注文表示が崩れない。
- `TC-058` `lines_updated_at` と表示整合が取れる。
- `TC-059` `order_lines_update` 監査イベントが記録される。
- `TC-060` 既知障害フィクスチャで再発しない。

## 最低運用ゲート（CI必須）
1. `TC-001` から `TC-020` を必須ゲート化する。
2. 障害再発の都度、対応するテストIDを増補する。
3. 本文書の要件変更とテスト変更は同一PRで扱う。

## 現状実装との比較（2026-03-02）
### 判定凡例
- `OK`: 要件を満たしている
- `PARTIAL`: 一部満たすが例外・抜けがある
- `NG`: 要件を満たしていない

### 要件別判定
1. `REQ-001`: `OK`  
`_build_sheet_menu_entries` は週メニューがあれば必ず `weekly_menu` を採用し、`_build_rows_from_menu_entries` が `date/daypart/menu` を構成している。
2. `REQ-002`: `OK`  
`weekly_menu` かつ `order_lines` ありでは `order_lines` 由来の数量マッピングのみ実行し、payload数量は適用しない。
3. `REQ-003`: `OK`  
`weekly_menu` で `order_lines` が空の場合のみ `_apply_payload_quantities_numeric_only` が呼ばれる。
4. `REQ-004`: `OK`  
`_apply_payload_quantities_numeric_only` は数量列のみ更新する。
5. `REQ-005`: `OK`  
数字救済は既存行に対するセル更新のみで、行追加しない。
6. `REQ-006`: `OK`  
`base_rows` をクローンして値更新する設計で、行順・行数・`row_id` は不変。
7. `REQ-007`: `OK`  
`weekly_menu` + `order_lines` ありで payload の menu/date/daypart を採用しない。
8. `REQ-008`: `OK`  
行生成は週メニュー起点で、範囲外日付のpayload行はシート行として採用されない。
9. `REQ-009`: `OK`  
同一 `order_lines` で注文間出力一致の回帰テストあり。
10. `REQ-010`: `OK`  
`get_ocr_sheet` は `get_ocr_output(..., persist_cache=False)` を使い、読み取り時にキャッシュ更新しない。
11. `REQ-011`: `OK`  
`update_lines` 後の `get_ocr_sheet` 反映は回帰テストで確認済み。
12. `REQ-012`: `OK`  
`ocr-history` 保持と `order_lines` 優先は反復ケースで回帰テスト済み。
13. `REQ-013`: `OK`  
第一優先は `date+daypart+menu`。`date+menu` フォールバックは `daypart` 列が存在しないテンプレート時に限定される。
14. `REQ-014`: `OK`  
payload row-index フォールバックは date不一致を拒否している。
15. `REQ-015`: `OK`  
`weekly_menu` / `ocr_table` とも `get_ocr_sheet` 経路の数字救済は純数値セルのみを採用し、自由文トークン補完は無効化した。
16. `REQ-016`: `OK`  
`OCR_SHEET_MAX_QTY` ガードで異常値を除外。
17. `REQ-017`: `OK`  
`order_lines` / payload直接数量セル / row-index数量代入の各経路で `0` を保持する回帰テストを追加済み（`preserves_zero` 系）。
18. `REQ-018`: `OK`  
`qty.*_x` / `qty.*_2f/3f` 両方で回帰テスト済み。
19. `REQ-019`: `OK`  
API側で `sheet_week_dates_incomplete` と `sheet_quantity_column_unmapped` を 400 にマップ済み。
20. `REQ-020`: `OK`  
`_resolve_sheet_week_id` の決定は入力順依存を排除する実装とテストあり。
21. `REQ-021`: `OK`  
月跨ぎ境界で stale hint より line/OCR由来月を優先するテストあり。
22. `REQ-022`: `OK`  
`ocr-apply` / `lines update` 反復後の最終優先をテスト済み。
23. `REQ-023`: `OK`  
`source` は `weekly_menu` / `weekly_menu+ocr_payload` / `ocr_table+ocr_payload` に分岐反映される。
24. `REQ-024`: `OK`  
`ocr-sheet` 応答に `trace.rows` を追加し、セル単位の由来（`order_lines`/`ocr_payload`/`weekly_menu` 等）を返す。

### 現時点の主要ギャップ
1. 主要ギャップなし（再発時は該当TCを追加してゲート化）。

## LLM例外推論OCR（reparse）追補
### 追加要件（厳格）
1. `reparse_order` で `ocr_provider=openai/gemini` または `llm_assist=true` の場合、数量ルールは strict モードを強制する。
2. strict モードでは `zero_as_empty=false` とし、`0` は有効数量として保存する。
3. strict モードでは数量セルは純数値のみ許可し、`副23` など混在文字は不採用とする。
4. strict モードでは `OCR_SHEET_MAX_QTY` 上限（既定150）を適用し、異常値（例: `3000`）を不採用とする。
5. 上記 strict ルールは reparse のフォールバック経路（markdown/table_rows）にも一貫適用する。

### 実装具体化
1. `fax_parser.parse_order_lines` に `strict_numeric_quantity_cell` と `max_quantity_abs` ルールを追加する。
2. `order_service.reparse_order` で LLM条件時のみ strict ルールを構築して `parse_order_lines` に渡す。
3. `_build_sheet_lines_from_ocr_payload` に `quantity_rules` 引数を追加し、reparse フォールバックでも strict ルールを引き回す。

### テスト具体化
1. `test_reparse_order_openai_enforces_strict_quantity_rules`  
`reparse_order(ocr_provider=openai)` で `zero_as_empty=false` / `strict_numeric_quantity_cell=true` が渡ることを検証。
2. `test_parse_order_lines_strict_numeric_keeps_zero_and_rejects_noise`  
strict モードで `0` を保持し、`副23` と `3000` を不採用にすることを検証。
3. `test_parse_order_lines_non_strict_still_parses_mixed_numeric_text`  
非strict モードの既存互換（`副23 -> 23`）を維持することを検証。
