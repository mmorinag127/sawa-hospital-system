# Mock Contract C0 Baseline

2026-10-05。WT: `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-mock-contracts`、branch: `codex/modernization-c0-mock-contracts-20261005`。
base: `51fb7b609b141a66f90ccbcbb0e181dffc510e21` + 未commit差分。旧mock署名でTypeErrorとなる6件の限定比較であり、親の全体回帰やFAX処理の受入ではありません。

## 6件の判断

| testcase | 現行call/判断 | 編集後の選択試験 |
| --- | --- | --- |
| `test_download_document_falls_back_to_archived_ocr_input_when_canonical_uri_is_missing` | **未変更**。canonical storage URI欠落後、OCRの別input_referenceを原本として200返却する期待は、同一document/version/digestの確認がないまま別正本を採用するNo Fallbackとの衝突。 | persist_cache TypeErrorを保持。成功扱いしない。 |
| `test_download_document_returns_404_when_source_and_ocr_artifacts_missing` | 全資料欠落時404は停止契約。mockにkeyword-only persist_cacheを追加し、対象order IDとFalseをassert。 | pass |
| `test_reparse_order_llm_prompt_includes_previous_saved_candidate_rows` | `_load_existing_first_pass_payload_for_reparse(order_id, template=template_to_use)`へ追従。order ID、施設JSONのcolumns、明示geminiをassert。前回candidateはpromptの補助入力という期待で、正本への無条件採用ではない。 | 既存`error is None`が`llm_full_table_baseline_missing`でfail。 |
| `test_reparse_order_large_structural_projection_requires_manual_review` | 同loader署名。projectionを自動適用せずmanual reviewで停止する期待は維持。 | 既存`sheet_structural_projection_requires_review`期待より前に`llm_full_table_baseline_missing`でfail。 |
| `test_get_ocr_sheet_preserves_authoritative_current_sheet_gate_when_blocked` | `payload_uses_ready_position_fallback(payload, *, template=None)`。渡されたcolumnsを現施設templateと照合。製品predicateは現在常にFalse。mockの旧Trueは意図的な負の入力として保持し、それでも保存済みsheetのapply/confirm禁止が保たれることだけを検証する。fallback実行の承認ではない。 | pass。template付き呼出しあり、元のblockersとcan_apply/can_confirm=Falseを保持。 |
| `test_process_ingest_inline_prefers_payload_ocr_job_id` | create_jobの現callにあるorder_id/uploaded_pdf_id/order_document_id/input_artifact_digestを名前付きで受ける。fixtureどおりidentity 3値はNone、digestはsha-inline、statusはrunning。 | pass。元の明示ocr_job_id/input_referenceのassert保持。OCR成功の証拠ではない。 |

製品の読取箇所: `backend/src/api/orders.py`の`_load_archived_original_document_bytes`/`download_document`、`order_service.py`の`get_ocr_output`/`_load_existing_first_pass_payload_for_reparse`/`reparse_order`/`get_ocr_sheet`、`position_column_mapping_service.py`のready predicate、`ocr_job_service.py:create_job`、`ingest_worker.py:_process_ingest_inline`。製品は変更していません。

## 実行記録

担当WTをcwdとし、`E=tmp/mock-contract-baseline`配下へ各runの`run.log`/`results.xml`/`exit.txt`を保存しました。選択6件でも4ファイルを収集し、残り426件はdeselectedです。

| run | pass | fail | skip | exit |
| --- | ---: | ---: | ---: | ---: |
| before-six | 0 | 6 (すべて指定のTypeError) | 0 | 1 |
| after-six | 3 | 3 (未変更TypeError 1、露出した業務assert 2) | 0 | 1 |
| before-full | 273 | 159 | 0 | 1 |
| after-full | 276 | 156 | 0 | 1 |

fullの432 testcase名は前後一致。対象外426件は273pass/153failのままです。`test_get_ocr_sheet_from_order_lines`の失敗messageにset表示順の差だけがあり、比較TSVには削らず残しました。

| ファイル | before pass/fail | after pass/fail |
| --- | --- | --- |
| `backend/tests/contract/test_orders_ocr_status_api.py` | 34/36 | 35/35 |
| `backend/tests/integration/test_ocr_pipeline.py` | 148/38 | 148/38 |
| `backend/tests/integration/test_ocr_sheet_history.py` | 86/83 | 87/82 |
| `backend/tests/integration/test_uploaded_pdf_recovery_flow.py` | 5/2 | 6/1 |

```sh
E=tmp/mock-contract-baseline
P=/Users/mmorinag/Sawa/2025.12/worktrees/daily-output-label-requests-20260616/backend/.venv/bin/python
bash "$E/run.sh" before-six six
bash "$E/run.sh" before-full
bash "$E/run.sh" after-six six
bash "$E/run.sh" after-full
env -i PATH=/usr/bin:/bin HOME="$PWD/$E/after-six/home" TMPDIR="$PWD/$E/after-six/tmp" XDG_CACHE_HOME="$PWD/$E/after-six/cache" PYTHONDONTWRITEBYTECODE=1 "$P" "$E/check_ast.py" > "$E/ast.log" 2>&1
env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 "$P" "$E/compare.py" > "$E/comparison.log"
git diff --check
```

runnerはDB再使用を拒否します。再実行時は新run名を指定してください。依存venvは読取のみ、env -i、bytecode無効、自WT/backendのPYTHONPATH、run別の新SQLite/HOME/TMP/cache/artifact出力先です。Python audit hookでconnect/bind/sendto/DNSと外部processを拒否し、通信前の拒否probeを実施。cloud credentialsは渡さず、Google credential pathは自tmpの存在しないファイルへ固定、AWS metadataは無効化。OS sandbox基盤は追加していません。

各logにsrc/db/order/ocr-job/position/ingestのimport pathを記録し、自WT由来をassert。AUTH_DISABLED=trueは既存conftestに合わせたcomponent試験で、本番認可の証拠ではありません。初回full起動はmacOS bashの空配列エラーでpytest開始前に停止し、tmp runnerの引数だけ修正してbefore-fullを実行しました。

AST検査は変更5ケースの元24assertions、既存代入/return/literalを保持し、他の全トップレベルASTがbaseと一致することを確認。fixture、期待値、skip、製品、conftest、lock、依存は変更していません。case名単位の全結果は`six-comparison.tsv`/`full-comparison.tsv`、sourceと証拠のSHA256は`artifacts.sha256`に記録します。

## 残件

C2/H2ではarchive原本同一性・canonical欠落時の扱いを先に決定し、reparseの2fixtureでは現行のauthoritative full-table baseline不足を解決する必要があります。ここではbaselineの捏造、旧cacheへの代替、期待値変更をしていません。隣接する`accepts_matching_aux_schema`はdict期待に対するNone、`rejects_stale_aux_schema`はNone期待に対するdict、`test_reparse_order_blocks_llm_reparse_without_first_pass_context`はbaseline不足期待に対するmock由来`main_ocr_failed:gemini`で、fullでは3件とも前後failです。これらは対象外の既存失敗として保持しています。親の全体回帰14 TypeError全件を解決したという記録ではありません。

実OCR、FAX受入、live、ブラウザ、顧客DB、全modernization/C1/stg受入は未実施。stage/commit/merge/push/deployなし。親のレビュー・監視・統合後再検証が別途必要です。
