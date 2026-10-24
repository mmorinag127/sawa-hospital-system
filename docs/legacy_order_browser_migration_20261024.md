# Legacy order browser migration

Original: `b30bb8ad30459b0b2464f10b04c6e38584cd7b1e:frontend/tests/e2e/order_confirm.spec.ts`.
The old route now contains only an SSR redirect to workflow-v2. These40 tests mock retired APIs and wait for retired controls. No test is skipped to obtain a success. Replace them with current browser scenarios and retain current real-DOM/backend coverage below; retired UI is not re-enabled. All replacement browser cases remain unexecuted until the combined local batch. This document is not passing evidence or live verification.

| No. | Original scenario | Current contract / coverage |
|---|---|---|
| 1 | order detail editing flow uses grouped lines and PDF viewer | 現行シートの実入力・保存は workflow_v2_sheet_edit、原本PDF分離は order_confirm (workflow-v2)。 |
| 2 | page summary keeps stale-timeout llm reparse visible even when current sheet remains usable | 旧Step2 LLM再解析UIは廃止。現行の記録済みジョブ表示は native-ocr-owner-dom、AI再試行は modernization-workflow-action-dom。 |
| 3 | page summary surfaces rejected llm reparse outcome instead of treating it as completed | 現行AIの失敗・再試行・所有者失効は modernization-workflow-action-dom。旧LLM outcome文字列への依存は廃止。 |
| 4 | page summary prefers evidence-unavailable workflow truth and exposes OCR rerun action | 現行candidate詳細拒否は workflow-demand-read-contract、artifact欠損は order_confirm (workflow-v2)。 |
| 5 | step2 auto-refreshes OCR preview after OCR rerun completes | order_confirm (workflow-v2) の owned OCR completion。旧preview APIの利用は廃止。 |
| 6 | step2 auto-refreshes OCR preview when rerun completion lands on candidate-choice state | 同上。完了後の候補一覧と選択可否を実ボタンで確認。 |
| 7 | step2 keeps a visible OCR refresh action when preview pages are unavailable | 旧Step2専用refresh UIは廃止。現行の詳細取得・失敗表示は workflow-demand-read-contract。 |
| 8 | page stays in parity with order, workflow, draft-sheet, and ocr-sheet while llm reparse awaits first-pass output | 旧4読取APIの併用は廃止。現行inspection側の整合・遅延失効は modernization-workflow-action-dom / native-ocr-owner-dom。 |
| 9 | order detail does not show OCR artifact in the fax pdf slot when original is missing | order_confirm (workflow-v2) の原本欠損時iframeなし・OCR画像の非流用。 |
| 10 | order detail can archive and unarchive a single order | 旧詳細画面の単件archive UIは廃止。一覧のarchive/復元は orders_index の現行操作試験。 |
| 11 | overlay unavailable shows recovery-only flow and blocks edit actions | overlay欠損だけで手動数量編集を止める旧制約は廃止。現行artifact選択拒否と保存入力保持を別々に検査。 |
| 12 | weekly menu blocked shows explicit stop-state instead of empty editor | 現行の正本・献立blockerは backend canonical_saved_projection_regression / workflow materialization 試験。空の代替シートで成功化しない。 |
| 13 | Step2 keep-current clears unresolved candidate after reload | 旧keep-current専用UIは廃止。現行はOCRを一つ明示選択し、拒否なら選択を保持。 |
| 14 | Step2 keep-current does not stay pending while the background refresh is still loading | 旧keep-current専用pending条件は廃止。現行の独立進捗取得は workflow-demand-read-contract。 |
| 15 | Step2 shows a genuinely new candidate even when current sheet revision stays the same | 同revisionの詳細取得世代失効は workflow-demand-read-contract。候補更新は現行completion browser試験。 |
| 16 | stale failed OCR status does not override the current sheet and LLM defaults stay on Gemini Pro | 旧モデル既定値を現モデルへ強制しない。現行ジョブ所有者・失敗表示は native-ocr-owner-dom。 |
| 17 | authoritative Step2 apply beats an older in-flight refresh so the same OCR candidate does not reappear | 旧apply APIは廃止。現行command/read世代・選択OCR・入力revision競合は modernization-workflow-action-dom。 |
| 18 | Step2 save-and-next reaches Step3 before the post-save refresh settles | 旧Step2 save-and-next APIは廃止。現行選択からStep3遷移は order_confirm (workflow-v2)、編集保存は workflow_v2_sheet_edit。 |
| 19 | LLM extra prompt follows the active facility sheet schema dynamically | 旧施設schemaからの自動追加prompt UIは廃止。現行は正本facility設定と明示promptを使用し、旧prompt文面を復活させない。 |
| 20 | merged-cell OCR issue exposes dedicated AI choice with Gemini Pro preset | 旧merged-cell専用AI選択UIは廃止。現行geometry/expanded-cell-copyの所有者・CASは h2-quad-owner / modernization-workflow-action-dom。 |
| 21 | confirm button shows loading state while confirm is in flight | order_confirm (workflow-v2) の確定pending中disable・重複POSTなし。 |
| 22 | last step primary action confirms and returns to orders without separate training request | order_confirm (workflow-v2) の正常確定・一覧遷移・training POSTなし。 |
| 23 | last step primary action still returns to orders when post-confirm detail refresh would fail | 同上。確定後inspectionが失敗しても不要な再取得を行わない。 |
| 24 | last step confirm uses canonical current sheet revision instead of latest OCR revision | 現行commandは workflow revisionのIf-Matchを送信。browser正常/拒否とDOM command試験。 |
| 25 | last step returns to orders without reconfirming when the canonical order is already confirmed | order_confirm (workflow-v2) のconfirmedは再確定/再生成なしで一覧へ戻る。現行欠落を修正。 |
| 26 | last step primary action does not return to orders when confirm is rejected | order_confirm (workflow-v2) の409/500で画面・実入力123/2/5を保持。 |
| 27 | saved sheet revision keeps current date and daypart while reusing saved quantities | 保存済み手入力・選択source一致は backend canonical_saved_projection_regression / draft_sheet_service。既存保存値の再投影を修正。 |
| 28 | saved sheet context change requires operator choice before rebuilding the skeleton | 現行dirty contextの旧If-Match保持は workflow_v2_sheet_edit、source置換境界は modernization-workflow-action-dom。 |
| 29 | ocr sheet editor supports tab navigation and drag-moving a selected range | 現行Tabとoverlay連動は order_confirm (workflow-v2)。旧範囲drag移動UIは現画面に存在せず復活させない。 |
| 30 | ocr sheet quantity-column tools support bulk fill and layout toggle while preserving save payload | 旧列一括fill UIは現画面に存在しない。現行実セル入力/blur/saveは workflow_v2_sheet_edit と raw payload DOM試験。 |
| 31 | ocr sheet confidence threshold supports bulk and row proposal adoption without leaking overlay data into save/apply/confirm payloads | confidenceで数量を自動採用する旧制約は廃止。現行raw入力/sidecar非流用は modernization-workflow-action-dom。 |
| 32 | structural Step2 row edits invalidate overlay candidates instead of keeping stale row alignment | 旧Step2構造行編集UIは廃止。現行source置換/入力失効は modernization-workflow-action-dom。 |
| 33 | pdf fetch failure on one order does not poison later orders | order_confirm (workflow-v2) の別注文の明示document取得・PDF blob表示。遅延owner失効は既存DOM試験。 |
| 34 | order detail falls back to calendar date when week options are unavailable | 週推測fallbackは禁止。旧calendar fallback期待は廃止し、明示期間と正本contextだけを使用。 |
| 35 | order detail treats month-only week as unresolved when explicit week options exist | 現行の未解決期間拒否は sheet-week/backend context試験。旧month-onlyを具体週へ推測しない。 |
| 36 | step1 keeps the explicit current week when week options still point at the default week | order_confirm (workflow-v2) の明示期間は古いweek-optionsへ戻らない。 |
| 37 | step1 save keeps the explicit exception week through post-save refresh and revisit | 同上。設定保存・refresh・document reloadで明示例外期間を保持。 |
| 38 | focused Step2 sheet row highlights the matching OCR overlay row and switches pages | order_confirm (workflow-v2) の行列highlight/Tab。旧Step2 page-image切替UIは現行単一previewとは別仕様のため復活させない。 |
| 39 | original preview mode also highlights the matching row when page images exist | 現行原本はPDF iframe、overlayは対応画像として分離。旧page-image上への原本highlightは廃止。 |
| 40 | step2 forced recovery actions replace the current sheet with weekly or facility truth | 旧weekly/facility truthによる強制置換fallbackは廃止。現行は明示選択OCR source、CAS・source不一致なら拒否。 |
