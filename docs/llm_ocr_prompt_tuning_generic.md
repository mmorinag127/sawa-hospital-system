# 汎用 LLM OCR プロンプト改善メモ（yomitoku 精度目標）

## 1. 目的
- 特定施設依存ではなく、全施設で再利用できる OCR プロンプトと出力制約を固定する。
- 最低条件として「yomitoku 相当以上」を継続的に満たすまで反復する。

## 2. Web 調査で採用した方針（公式ドキュメント）
1. Gemini は画像入力時に **画像を先、テキスト指示を後** に置く。  
   - https://ai.google.dev/gemini-api/docs/files?lang=python
2. OpenAI 画像入力は **高詳細 (detail=high)** を使う。  
   - https://platform.openai.com/docs/guides/images
3. 指示は「曖昧さを減らす・出力形式を厳密化・反復で評価して調整」の形で定義する。  
   - https://platform.openai.com/docs/guides/prompt-engineering

## 3. プロンプト要件（汎用）
1. 返却形式は JSON 1オブジェクト固定（`facility_name/date_strings/rows`）。
2. 行は表本体を上から順に 1 行ずつ出力。
3. 隣接セル・前行からの推測補完を禁止。
4. 数量列（`qty.*`）は数字のみ許可し、それ以外は空文字。
5. 日付は `M/D`、朝昼夕は `朝/昼/夕` のみ許可。
6. 表外要素（見出し、凡例、合計、ページ番号）は除外。

## 4. 実装済み変更
1. `backend/src/services/gemini_ocr_service.py`
   - プロンプトを上記ルールへ更新。
   - Gemini リクエストの順序を `image -> text` に変更。
   - 出力の正規化（数量数字化・日付正規化・朝昼夕正規化）。
2. `backend/src/services/openai_ocr_service.py`
   - 同等の汎用プロンプトへ更新。
   - 画像入力を `detail=high` に固定。
   - truncation 時の再試行（max tokens 増加）を追加。
   - 出力の正規化（数量数字化・日付正規化・朝昼夕正規化）。
3. `backend/src/services/config_service.py`
   - `openai/gemini` の truncation 再試行設定キーを施設設定から引き回し。
4. `backend/src/services/config_validator.py`
   - 上記設定キーの型検証を追加。

## 5. テスト固定
1. `backend/tests/integration/test_gemini_ocr_service.py`
   - truncation 再試行
   - `image -> text` 順序
   - 数量/日付/朝昼夕の正規化
2. `backend/tests/integration/test_openai_ocr_service.py`
   - truncation 再試行
   - `detail=high`
   - 数量/日付/朝昼夕の正規化
3. `backend/tests/integration/test_config_validator.py`
   - retry 設定キーの受理/型エラー検証

## 6. 反復運用ループ（継続）
1. 失敗注文を追加して再解析。
2. `provider_debug` の `finish_reason` / `recovered_truncated_json` / `attempts` を確認。
3. `rows` の欠落・日付逸脱・数量非数字が出たケースを回帰テスト化。
4. テストを通した変更だけを本番反映。

## 7. 受け入れ基準（暫定）
1. known failure 注文で、行欠落・日付逸脱・数量非数字が再発しない。
2. LLM 出力が truncation の場合、再試行または安全フォールバックが必ず働く。
3. 追加した回帰テストが CI で常時グリーンを維持する。

## 8. 追加実験ログ
- 3軸考察 + 多段プロンプト実験: [llm_ocr_prompt_multistage_experiment_20260303.md](./llm_ocr_prompt_multistage_experiment_20260303.md)
- Round2/3 の追加試行結果:
  - `tmp/prompt_eval/results/prompt_variant_metrics_round2.md`
  - `tmp/prompt_eval/results/prompt_variant_metrics_round3_v2.md`
