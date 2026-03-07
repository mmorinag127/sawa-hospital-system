# LLM OCR 3軸考察とマルチステージ実験（2026-03-03）

## 1. 目的
- 対象:
  - 1) プロンプト側のノイズ低減
  - 2) テスト戦略
  - 3) 本番前の必須ゲート
- 要件:
  - 施設依存ではなく汎用に効く方針を定義する。
  - プロンプトは多段（2-pass）を含めて実測比較する。

## 2. 外部調査（研究・実装・公式）

### 2.1 公式ドキュメントからの採用ポイント
1. Gemini Structured Output は JSON Schema 準拠を強制できる。
   - 参照: https://ai.google.dev/gemini-api/docs/structured-output
2. Gemini の PDF/文書処理では、単一ページ時は「ページの後にテキストプロンプト」を推奨。
   - 参照: https://ai.google.dev/gemini-api/docs/document-processing
3. Gemini Prompting Strategies は、複雑タスクを分割し chain prompts で順次処理することを明示。
   - 参照: https://ai.google.dev/gemini-api/docs/prompting-strategies
4. OpenAI の Structured Outputs は strict schema で「スキーマ一致率」を高める設計（constrained decoding）。
   - 参照: https://openai.com/index/introducing-structured-outputs-in-the-api/
5. OpenAI の prompt best practices は、明確指示・先頭配置・形式指定・低温度（抽出系）を推奨。
   - 参照: https://help.openai.com/en/articles/6654000-best-practices-for-prompt-engineering-with-the-openai-api

### 2.2 研究知見（多段検証）
1. Chain-of-Verification: 「初回回答 -> 検証質問 -> 最終回答」で hallucination を低減。
   - 参照: https://arxiv.org/abs/2309.11495
2. Self-Consistency: 複数経路の整合を取ると推論性能が改善。
   - 参照: https://arxiv.org/abs/2203.11171

### 2.3 実装例
1. `llm_aided_ocr` は OCR 後処理を 2-step（補正 -> 整形）で実装し、retry/token 調整を明示。
   - 参照: https://github.com/Dicklesworthstone/llm_aided_ocr

### 2.4 表構造評価・ベースライン（追加調査）
1. PubTables-1M（CVPR 2022）
   - 行・列・セル（空セル含む）の構造アノテーションを提供。
   - 参照: https://arxiv.org/abs/2110.00061
2. Table Transformer（Microsoft）
   - table detection / table structure recognition の実装と重みを公開。
   - 参照: https://github.com/microsoft/table-transformer
3. GriTS（ICDAR 2023）
   - 表構造の類似度評価指標（Topology / Content / Location）。
   - 参照: https://github.com/microsoft/table-transformer

## 3. 実験設計（本リポジトリで実施）

### 3.1 入力データ
1. `ORDb266d5d9`（FAC00008）
2. `ORDbabf3c73`（FAC00009）

### 3.2 比較バリアント
1. `baseline_current_prompt`
2. `strict_no_continuity_infer`
3. `span_first_decomposition`
4. `two_pass_verify`

### 3.3 評価指標
1. `row_recall`: 数量あり行の再現率
2. `cell_recall`: 数量セル一致率（既存 `ocr-sheet` 比）
3. `cell_precision`: 出力数量セルの適合率
4. `missed_rows_with_expected_qty`
5. `pred_rows`, `pred_nonempty_cells`
6. 応答特性: `finish_reason`, `attempt_count`, `elapsed_sec`, `usage`

### 3.4 生成物
- `tmp/prompt_eval/results/prompt_variant_metrics.json`
- `tmp/prompt_eval/results/prompt_variant_metrics.md`
- 各バリアントの生レスポンス JSON

## 4. 実験結果サマリ

### 4.1 ORDb266d5d9
- `baseline_current_prompt`:
  - `row_recall=1.0`, `cell_recall=0.7089`, `cell_precision=0.6667`
- `strict_no_continuity_infer`:
  - `row_recall=0.9821`, `cell_recall=0.6962`（微悪化）
- `span_first_decomposition`:
  - `cell_recall=0.3544`, `cell_precision=0.3333`（大幅悪化）
- `two_pass_verify`:
  - baseline と同等（改善なし）

### 4.2 ORDbabf3c73
- `baseline_current_prompt`:
  - `row_recall=0.873`, `cell_recall=0.8519`, `cell_precision=0.9758`
- `strict_no_continuity_infer`:
  - `row_recall=0.6667`（大幅悪化）
- `span_first_decomposition`:
  - `row_recall=0.6667`（大幅悪化）
- `two_pass_verify`:
  - `row_recall=0.8889`, `cell_recall=0.873`, `cell_precision=0.9821`
  - baseline 比で改善（取りこぼし行を 8 -> 7 へ減少）

## 5. 3軸での考察

### 5.1 プロンプト
1. 「推論禁止を強めるだけ」は再現率を落としやすい。
2. 1-pass では、行取りこぼし（末尾行欠落）を完全には防げない。
3. 2-pass 検証（CoVe型）は難ケースで改善余地がある。
4. ただし token/cost は増える（`prompt_tokens` が増加）。
5. 実運用は以下が現実解。
   - 通常: 1-pass
   - 品質ゲート違反時のみ: 2-pass 再検証

### 5.2 テスト
1. 現状テストは「構文・正規化・リトライ」の網羅は進んでいる。
2. 不足は「品質回帰」系。
3. 追加必須テスト:
   - `row_index` 連続性（末尾欠落検知）
   - `pred_rows / expected_rows` の下限
   - `suspicious_blank_row` の再現fixture
   - プロンプト変更時の golden 比較（注文固定）

### 5.3 必須ゲート
1. 現状ゲートは warnings/filled_ratio/max_qty/spike で有効。
2. 追加すべきゲート:
   - `row_coverage_ratio`（例: >= 0.98）
   - `missing_tail_rows == 0`（週メニュー行末尾欠落を禁止）
   - provider ごとの `attempt_count`・timeout 監視
3. 運用提案:
   - first pass fail -> second pass verify -> still fail なら保存拒否

## 6. 現状実装との比較

### 6.1 実装済み
1. 数量専用モード（`llm_quantity_only_mode`）
2. strict numeric 正規化
3. JSON schema 応答（Gemini）
4. 再解析デバッグ保持
5. 保存前 canonical validation

### 6.2 ギャップ
1. 2-pass verify は本番フローに未統合（今回実験はローカル評価）。
2. row coverage を直接見る品質ゲートが未実装。
3. prompt 変更の品質回帰テスト（実注文固定）が未実装。

## 7. 結論
1. 汎用戦略としては「1-pass baseline + 条件付き 2-pass verify」が最も妥当。
2. 「推論禁止の強化のみ」はむしろ欠落を増やすため主戦略に不適。
3. 再発防止の主因はプロンプト単体ではなく、
   - row coverage テスト
   - row coverage デプロイゲート
   - 失敗時 2-pass 再検証
   の3点セットである。

## 8. 追加試行（Round2: 行欠落対策）

### 8.1 追加バリアント
1. `baseline`（再測定）
2. `row_count_constrained`
3. `two_pass_repair`

### 8.2 追加結果（要点）
1. `ORDbabf3c73`
   - baseline: `pred_rows=56/64`, `missing_tail_rows=8`
   - row_count_constrained: `pred_rows=64/64`, `missing_tail_rows=0`
   - two_pass_repair: `pred_rows=64/64`, `missing_tail_rows=0`
2. `ORDb266d5d9`, `ORD1defabff`
   - もともと row coverage は `1.0` のため、行欠落改善効果は限定的。

### 8.3 観測
1. `row_count` 拘束は「末尾行欠落」に対して有効。
2. ただし難ケースでは「欠落行を空行で補う」方向に寄るため、数量セルの改善は別対策が必要。

## 9. 追加試行（Round3: 列ずれ抑制）

### 9.1 試行内容
1. `row_count_constrained_v2`
   - 量列キーを明示
   - 左右順（left-to-right）を明示
   - key swap を禁止
2. `positional_slots_qty.c*`
   - 量列を `qty.c0..` の位置インデックスで抽出し、後段で実列へマップ。
3. `ord1_column_conservative`
   - 通常空欄列（soft/mixer）は明示的に保守化。

### 9.2 結果サマリ
1. `ORDbabf3c73`
   - `row_count_constrained_v2` で大幅改善:
     - `row_recall=1.0`, `cell_recall=0.9841`, `cell_precision=0.9688`
2. `ORDb266d5d9`
   - `row_count_constrained_v2` でも `cell_recall=0.3544` と低迷。
   - `positional_slots_qty.c*` でも改善せず（`cell_recall=0.3418`）。
3. `ORD1defabff`
   - `row_count_constrained_v2` は過剰出力が増え `cell_precision` 低下。
   - `ord1_column_conservative` は precision 微改善だが recall 低下。

### 9.3 原因仮説
1. 行欠落問題と列対応問題は別問題。
2. 列対応はプロンプトだけで安定しない注文が存在する。
3. 特に `ORDb266d5d9` は表ヘッダの読みにくさで列意味づけが揺れている可能性が高い。

## 10. 追加調査で得た示唆（評価と運用）
1. 行欠落は `row_count` 拘束で実運用上ほぼ抑制可能。
2. 列ずれは prompt 単体より後段検証（列分布・履歴整合）で止める設計が必要。
3. したがって運用の主軸は以下。
   - 1st pass: row_count拘束プロンプト
   - Gate: row_coverage + column_anomaly（列分布異常）
   - 2nd pass: gate fail 時のみ verify/repair
   - それでも fail: 保存拒否（現行 fail-fast 方針）

## 11. 実験ログ（追加ファイル）
1. Round1
   - `tmp/prompt_eval/results/prompt_variant_metrics.json`
   - `tmp/prompt_eval/results/prompt_variant_metrics.md`
2. Round2（行欠落対策）
   - `tmp/prompt_eval/results/prompt_variant_metrics_round2.json`
   - `tmp/prompt_eval/results/prompt_variant_metrics_round2.md`
3. Round3（列ずれ抑制）
   - `tmp/prompt_eval/results/prompt_variant_metrics_round3_v2.json`
   - `tmp/prompt_eval/results/prompt_variant_metrics_round3_v2.md`
4. 個別追加試行
   - `tmp/prompt_eval/results/ORDb266d5d9_x4_positional_slots_metrics.json`
   - `tmp/prompt_eval/results/ORD1defabff_x5_column_conservative_metrics.json`
