# OCR/LLM/FAX 処理 全体文脈メモ

作成日: 2026-03-20  
目的: 現状の OCR パイプライン、LLM 再解析、FAX PDF パターン、既知障害、対処履歴、残課題を一つに集約し、外部で深い設計レビューや推論を行うための完全な参照資料にする。  
対象読者: 設計レビュー担当、ChatGPT 等で根本対策を考える人、開発者。

## 1. この文書の立場

この文書は「現在の実装が正しい」という前提では書いていない。  
むしろ以下を前提とする。

- OCR と LLM は不完全である
- 現在のシステムは `局所修正` は多数入っているが、`source of truth` と `採用判定` がまだ複雑
- ユーザーが感じている「毎日どこかが壊れる」は妥当な感覚である
- 個別注文ごとの症状を塞ぐだけでは終わらない

そのため、この文書では以下を分けて記述する。

- 現在の構成
- 現在の設計思想
- 実際に起きた障害
- 入れた対策
- まだ残っている設計上の問題
- 今後の本質対策候補

## 2. エグゼクティブサマリー

現在の注文処理は、概ね次の流れで動く。

1. `注文書アップロード` で PDF を受け取る
2. `worker` が ingest job を作り、`ocr-pipeline` に OCR を投げる
3. `ocr-pipeline` が page correction、full-page `yomitoku`、quantity subgrid second pass などを実行し、OCR 成果物を GCS + cache に保存する
4. backend が初回の `OrderLine` を組み立て、注文として保存する
5. ユーザーは注文詳細で Step1〜5 を進める
6. 必要なら LLM 再解析を行う
7. 確定済みの注文だけが日別出力、袋分け、ラベル、納品書、総量の対象になる

ここで問題なのは、現在のシステムが厳密には一つの真実ソースで動いていないこと。

主なデータ源:

- `ocr-pipeline` の生成果物
  - `pages`
  - `table_raw`
  - `quantity_subgrid_passes`
  - `page_correction`
  - overlay artifacts
- `OrderOcrCache.payload`
- `OrderOcrRevision`
- `OrderLine`
- `facility template`
- `weekly/monthly menu`
- `LLM reparse candidate`

これらが場面ごとに主役を入れ替えている。  
このため、ある経路で少し壊れたものを、別経路が正しいものとして拾ってしまう事故が起こる。

最近の代表障害:

- `ORD032433a2`
  - OCR 本体はあるのに `ocr-pages` 生成で OOM
- `ORD71873bb1`
  - 施設テンプレ自体は合っているのに、`hard_failed` な LLM/projection の数量が `OrderLine` として残り、Step2 がそれを拾って壊れたシートを見せた
- `ORD15b74603`
  - 施設テンプレ不一致寄り。そもそも列 family が怪しい
- `ORD8931bb3e`
  - 再解析保存時の line id collision、overlay 不足時の復旧導線不足、UI 上の flow が悪い

結論だけ先に書くと、現在の最大問題は OCR や LLM の精度そのものより、

- `draft` と `confirmed` の境界
- `order_lines` をいつ真実扱いするか
- 表示に必要な成果物を 언제 blocker とするか
- request path で重い再計算をしていた点

にある。

## 3. 現在の本番構成

### 3.1 現在の入口

Gmail 系は削除済み。現在の受付経路は `PDF アップロード` のみ。

- 実運用入口:
  - `/pdf-upload`
- ingest mode:
  - `manual_upload`

関連コード:

- [backend/src/api/ingest.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/api/ingest.py)
- [backend/src/services/manual_upload_service.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/services/manual_upload_service.py)
- [backend/src/services/intake_mode_service.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/services/intake_mode_service.py)

### 3.2 Cloud Run 現状

2026-03-20 時点の主要サービス:

- `worker-prod`
  - latest revision: `worker-prod-00284-s75`
  - `1 CPU / 1Gi`
  - `minScale=0`
  - `maxScale=3`
  - `containerConcurrency=80`
  - `INGEST_MAX_WORKERS=6`
  - `OCR_PIPELINE_MAX_INFLIGHT=4`
- `ocr-pipeline-prod`
  - latest revision: `ocr-pipeline-prod-templatefix`
  - `2 CPU / 8Gi`
  - `minScale=0`
  - `maxScale=5`
  - `containerConcurrency=1`
  - `OCR_YOMITOKU_DPI=200`
- `web-prod`
  - latest revision: `web-prod-00157-vvg`
  - `1 CPU / 512Mi`
  - `minScale=0`
  - `maxScale=3`

重要な意味:

- OCR パイプラインは現在 `minScale=0` なので常駐しない
- 週 1 回まとめて PDF を流す運用ならコスト面では妥当
- ただし最初の OCR 数件は cold start の影響を受ける

## 4. データモデル上の主な登場人物

### 4.1 注文本体

- `Order`
- `OrderLine`
- `OrderDocument`

`OrderLine` は現在、依然として「確定済み真実データ」と「OCR/LLM 由来の暫定データ」が接触しやすい。

### 4.2 OCR キャッシュ

- [backend/src/models/order_ocr_cache.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/models/order_ocr_cache.py)

役割:

- `ocr-pipeline` の成果物や補助 payload を注文ごとに 1 レコードで保持
- `payload` に多くの情報が載る

問題:

- JSON 一発で何でも載せているので、責務分離は弱い
- ただし現状では一番実用上の中間成果物置き場として使われている

### 4.3 OCR revision

- [backend/src/models/order_ocr_revision.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/models/order_ocr_revision.py)
- [backend/src/services/ocr_revision_store.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/services/ocr_revision_store.py)

役割:

- Step2 のシート編集履歴
- `sheet_save_only`
- `before/after digest`
- revision rows

これは Stage 1/2/3 の `draft-first` 系 hardening の土台になっている。

### 4.4 OCR job

- [backend/src/models/ocr_job.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/models/ocr_job.py)
- [backend/src/services/ocr_job_service.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/services/ocr_job_service.py)

役割:

- OCR / reparse の job 状態管理
- `status`
- `metrics`
- `error_message`

最近の重要点:

- stale job 判定
- `hard_failed` / `draft_ready_blocked` / `stalled`
- `processing_stage`

## 5. 現在の OCR パイプライン詳細

### 5.1 初回 ingest

主入口:

- [backend/src/api/ingest.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/api/ingest.py)
- [backend/src/workers/ingest_worker.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/workers/ingest_worker.py)

概略:

1. upload された PDF を保存
2. ingest job 作成
3. worker が順に処理
4. OCR pipeline を起動
5. OCR 成果物を回収
6. 初回の lines を保存
7. 必要条件が揃えば自動 LLM reparse を追加で起動

補足:

- `1 PDF = 1 ingest job`
- 複数 PDF upload でも内部は巨大一括バッチではなく fan-out

### 5.2 OCR pipeline 本体

主コード:

- [ocr_pipeline/app/main.py](/Users/mmorinag/Sawa/2025.12/workspace/ocr_pipeline/app/main.py)
- [ocr_pipeline/app/page_correction.py](/Users/mmorinag/Sawa/2025.12/workspace/ocr_pipeline/app/page_correction.py)
- [ocr_pipeline/app/quantity_subgrid.py](/Users/mmorinag/Sawa/2025.12/workspace/ocr_pipeline/app/quantity_subgrid.py)

概略:

1. PDF 読み込み
2. template 候補解決
3. page correction
4. full-page `yomitoku`
5. overlay / markdown / structured tables/cells 作成
6. quantity subgrid second pass
7. artifacts / payload 保存

### 5.3 quantity subgrid second pass

背景:

- 左側のノイズ、補助記号、二段ヘッダの影響を減らしたい
- 特に数量列だけを読み直したい

実装:

- quantity-only crop を抽出
- その crop に `yomitoku` を再実行
- `で/て/N/2.` などの典型誤読を文脈付きで正規化

関連コード:

- [ocr_pipeline/app/quantity_subgrid.py](/Users/mmorinag/Sawa/2025.12/workspace/ocr_pipeline/app/quantity_subgrid.py)

意味:

- full-page OCR より構造を安定化させる効果がある
- ただし「悪い手書き数字そのもの」を万能に読めるわけではない

### 5.4 初回 lines 生成

主コード:

- [backend/src/services/order_service.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/services/order_service.py)
- `create_order_from_ingest`

重要:

- 現在の初回注文化は完全に `ocr-pipeline` だけではない
- まだ `extract_fax_data() + parse_order_lines()` 由来の legacy parser 要素が残る
- つまり「OCR 成果物パイプライン」と「注文 line 化」はまだハイブリッド

このハイブリッド性が、後の事故原因にもなっている。

## 6. 現在の Step2/Step3/Step4 の設計思想

現在の注文詳細は、最近 `draft-first` にかなり寄せている。

関連 docs:

- [docs/ocr_draft_first_stage1_plan_20260316.md](/Users/mmorinag/Sawa/2025.12/workspace/docs/ocr_draft_first_stage1_plan_20260316.md)
- [docs/ocr_draft_first_stage2_plan_20260317.md](/Users/mmorinag/Sawa/2025.12/workspace/docs/ocr_draft_first_stage2_plan_20260317.md)
- [docs/ocr_draft_first_stage3_plan_20260317.md](/Users/mmorinag/Sawa/2025.12/workspace/docs/ocr_draft_first_stage3_plan_20260317.md)

意図:

- Step2 は `下書きシート` を整える場
- Step3 で `明細`
- Step4 で `袋分け`
- Step5 で完了

改善済み事項:

- stale 編集ガード
- `draft_ready_blocked` / `hard_failed` 分離
- `confirmed_lines_retained`
- `シートだけ保存` と `明細へ反映` の役割整理
- 技術情報の折りたたみ

ただし本質問題はまだ残る。

## 7. 現在の LLM 再解析

### 7.1 自動 LLM 再解析

最近の変更として、ingest 後に条件が揃っていれば自動で LLM reparse を走らせる経路を追加済み。

関連コード:

- [backend/src/workers/ingest_worker.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/workers/ingest_worker.py)

意味:

- 初回 OCR 完了後、first-pass OCR が揃っていれば追加で LLM 補完を走らせる
- ただし多重起動は抑止

### 7.2 手動 LLM 補完再解析

関連コード:

- [backend/src/services/order_service.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/services/order_service.py)
- `reparse_order`

最近の実装方針:

- first-pass OCR がある注文では、毎回 `yomitoku` を最初からやり直さない
- 既存の `yomitoku` 出力を再利用する
- first-pass OCR が無いなら `first_pass_ocr_missing` で止める
- `yomitoku結果 + 現在シート + 前回 LLM candidate` を prompt に入れて再推論する

この設計で改善したこと:

- Gemini/OpenAI の手動再解析が以前より速くなった
- 無駄な full OCR 再実行が減った

ただし根本的にはまだ:

- LLM candidate をどう採用するか
- projection がどこまで許されるか
- `hard_failed` と `draft` の境界

が難所である。

## 8. 現在の「真実ソース」問題

現時点の最大問題はここ。

現在、実質的に真実候補が複数ある。

1. `ocr-pipeline` の生成果物
2. `ocr-sheet`
3. `OrderLine`
4. `facility template`
5. `weekly menu`
6. `LLM reparse result`

そして現実には、

- Step2 で `OrderLine` を使うことがある
- Step2 で `ocr_payload` を使うことがある
- Step2 で `weekly_menu` を土台にすることがある
- LLM が quantity-only のつもりでも structural projection が入ることがある

このため、

- 壊れた `OrderLine`
- 正しい `ocr_payload`
- 未確定の LLM candidate

のどれを UI が見ているのかが、注文ごとに変わり得る。

最近の修正でかなり抑えたが、設計として完全に一本化はできていない。

## 9. FAX PDF のパターン整理

既存調査 docs:

- [docs/fax_pdf_inventory_20260225.md](/Users/mmorinag/Sawa/2025.12/workspace/docs/fax_pdf_inventory_20260225.md)
- [docs/fax_pdf_inventory_system_2026_02.md](/Users/mmorinag/Sawa/2025.12/workspace/docs/fax_pdf_inventory_system_2026_02.md)

大まかな family:

- `P1`: 常食 / 軟菜 / ミキサー
- `P2`: 常食2F/3F + 軟菜2F/3F + ミキサー2F/3F
- `P3`: 常食 + 禁食
- `P4`: 常食 + 糖尿 or 独自禁食系
- `P5`: 常食 + 職員 + 通所
- その他独自 family:
  - 肉禁
  - 魚禁
  - 職員
  - 通所
  - 魚焼(常食) のような派生

### 9.1 施設別に見た傾向

例:

- `FAC00001 / FAC00003 / FAC00005`
  - 2F/3F のフロア分割型
- `FAC00006 / FAC00009 / FAC00010`
  - 単一軸の常食/軟菜/ミキサー型に近い
- `FAC00002 / FAC00012`
  - 常食 + 禁食型
- `FAC00013`
  - 糖尿など特殊列

### 9.2 3/22 週で見つかった問題 family

新しい 3/22 週の束では、特に次が問題化した。

- 左側補助記号混入型
  - `高`, `金`, `習`, `主A`, `副`, `HKD` などが date/daypart 周辺に混ざる
- 二段ヘッダ型
  - `禁食 -> 肉禁/魚禁`
  - `常食 -> 2F/3F`
- block quantity 型
  - 一つの数量が複数メニュー行にぶら下がる
- 特殊列 family 型
  - `職員`, `通所`, `ゴマアレルギー` などが混ざる

これが `quantity subgrid` 実装の背景である。

## 10. 最近実際に起きた代表障害

### 10.1 `ORD032433a2`

症状:

- `ocr_status=done`
- しかし `/ocr-pages` は失敗
- UI は `原本PDFへフォールバック`

直接原因:

- `ocr-pages` 生成時に request path で重い grid 自動検出をしていた
- facility template の grid metadata が足りない
- corrected PDF のページ寸法が異常に大きいケースと重なり、render/detect で `worker-prod` が OOM

本質:

- 表示に必須な成果物が壊れているのに `done` 扱いしていた
- request path で重い再計算をしていた

対処:

- request path で PDF ベースの重い grid 検出をやめた
- pixel cap / DPI 上限を導入
- partial payload で pages artifacts が消えないよう保護

### 10.2 `ORD71873bb1`

症状:

- 施設区分は合っていそう
- しかし Step2 のシート行が明らかに壊れ、同じ数量が広く投影される

直接原因:

- 以前の LLM/projection による bad `OrderLine` が保存済み
- `hard_failed` なのに Step2 がその保存済み lines を拾っていた

観測:

- `ocr_result_state=hard_failed`
- `structural_row_projection` で大量 row/cell コピー
- `sheet_column_anomaly`

対処:

- `hard_failed + large structural projection` では保存済み `order_lines` を Step2 で suppress
- `ocr_payload` 側へフォールバック
- 今は live で `source=weekly_menu+ocr_payload`
- warning: `sheet_order_lines_suppressed_reparse_failed`

### 10.3 `ORD15b74603`

症状:

- OCR 自体はそこそこ読めていそう
- しかし施設区分やシート列の解釈が怪しい

推定原因:

- 施設テンプレート不一致寄り
- つまり OCR が悪いというより、どの列 family として解釈すべきかがズレている

重要:

- これは `ORD71873bb1` のような bad lines suppress では自動では直らない
- 施設テンプレ / template resolution の問題

### 10.4 `ORD8931bb3e`

症状:

- LLM 再解析が長い、途中で壊れる、overlay 周りで degraded

過去の実障害:

- line id collision で `order_lines_pkey` 衝突
- overlay が無い時の復旧導線不足
- `保存 -> 反映` 後も stale state が残る

対処:

- line id 生成強化
- stale guard
- `復旧を試す` / `yomitokuを再実行` 導線
- Step2 CTA 整理

### 10.5 `ORD71873bb1` overlay の幾何補正

症状:

- overlay の傾き補正 / warp が不自然

原因:

- OCR 本体で選ばれた template と、page correction で使った template がズレていた

対処:

- 補正用 template が OCR 本体と一致しない時は `template_warp` を使わない

## 11. これまでに入れた主要対策

### 11.1 Gmail 廃止

- Gmail watch / scan / ingest 系はコード・GCP・API から削除
- 入口は `PDFアップロード` に統一

### 11.2 quantity subgrid

- 数字列だけ second pass OCR
- 典型誤読の文脈付き補正

### 11.3 draft-first

- `draft_ready_blocked`
- `hard_failed`
- `confirmed_lines_retained`
- stale reparse hardening

### 11.4 overlay / ocr-pages hardening

- request path 重処理を減らす
- synthetic preview に上限
- corrected PDF 周りの正常化

### 11.5 bad lines suppress

- `hard_failed + structural projection` の時は Step2 で保存済み lines を使わない

### 11.6 自動 LLM 再解析

- ingest 後に自動 LLM reparse
- 手動再解析では first-pass 再利用

## 12. それでも残っている本質問題

### 12.1 `source of truth` が一本化されていない

これは最重要。

本来は:

- OCR evidence
- editable draft
- confirmed lines

を完全に分けるべき。

しかし現状は:

- `OrderLine` が Step2 の材料になることがある
- `ocr_payload` が Step2 の材料になることがある
- weekly menu が土台になる
- LLM candidate が間接的に lines を污染することがある

### 12.2 施設 template / layout resolution が独立 stage 化されていない

現状:

- OCR の途中
- `get_ocr_pages`
- Step2 シート化

の複数箇所で template が影響する

理想:

- `template_resolved`
- `template_mismatch`

を OCR 後すぐ固定し、その後の経路では再解決しない

### 12.3 overlay/pages がまだ完全な必須成果物になり切っていない

最近かなり改善したが、理想は:

- OCR 完了時に overlay/pages を確定保存
- 無ければ `done` にしない
- UI で request 中再計算しない

### 12.4 LLM がまだ “patch candidate only” に完全にはなっていない

現在も実質、

- quantity-only を目指しているが
- structural projection により行構造へ広く影響し得る

理想:

- LLM は patch 候補のみ
- 採用 gate は別
- `OrderLine` へ直接影響させない

## 13. 現在の設計原則として正しい部分

現行設計の中でも、方向として良いものはある。

- OCR evidence を厚く残す
- quantity-only second pass
- draft-first
- stale conflict guard
- recoverable / blocked / failed の分離
- automatic vs manual reparse の分離

つまり全部が悪いわけではない。  
ただし、これらが完全に一貫した state machine に整理されていない。

## 14. 今後の本質修正候補

ここから先は `個別不具合修正` ではなく `構造修正` の候補。

### 14.1 OCR evidence を唯一の入力証拠に固定

OCR 完了時に必ず確定保存:

- corrected PDF
- overlay pages
- table_raw
- quantity_subgrid
- template_id
- grid metadata

その後の request path では再生成しない。

### 14.2 Step2 は `draft_sheet` のみを見る

Step2 は常に:

- weekly menu
- facility template
- OCR evidence
- current draft revision

だけで作る。  
`confirmed order_lines` は参照しない。

### 14.3 `confirmed_lines` は Step3/確定以降だけのものにする

つまり:

- Step2 で壊れた lines が UI に戻らない
- `ORD71873bb1` 型事故を構造的に防げる

### 14.4 template resolution を独立 stage 化

OCR 直後に:

- `template_resolved`
- `template_confidence`
- `template_mismatch`

を持つ。  
不一致なら自動採用や overlay warp を止める。

### 14.5 LLM を patch candidate 専用に限定

入力:

- OCR evidence
- current draft
- previous candidate

出力:

- `row X col Y = value Z` の patch 候補

禁止:

- 非数量フィールドの自由生成
- 行構造の勝手な再編成

### 14.6 apply gate を一元化

blockers 例:

- overlay/pages missing
- template mismatch
- hard_failed
- large structural projection
- sheet_column_anomaly
- stale conflict

これらは経路ごとに別判断せず、一箇所の gate で決める。

## 15. ChatGPT に深い推論を依頼するなら見てほしい論点

以下の問いを解いてほしい。

### 15.1 いまの最大の設計欠陥は何か

候補:

- source of truth の多重化
- Step2 と Step3 の責務混線
- LLM candidate の扱い
- template resolution の不安定さ

### 15.2 どういう状態モデルに再設計すべきか

候補状態:

- `ocr_evidence_ready`
- `template_resolved`
- `draft_ready`
- `draft_blocked`
- `confirmed_ready`
- `confirmed`
- `recovery_required`

### 15.3 OrderLine / OrderOcrCache / Revision の責務をどう分けるべきか

特に、

- 何が永続的真実か
- 何が暫定か
- 何が表示用 cache か

の境界整理。

### 15.4 template mismatch をどの stage でどう止めるべきか

`ORD15b74603` 型に対する本命設計。

### 15.5 quantity-only LLM/path をどうすれば本当に安全にできるか

特に structural projection の扱い。

## 16. 参考に見るべき主コード

### 16.1 backend

- [backend/src/api/ingest.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/api/ingest.py)
- [backend/src/workers/ingest_worker.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/workers/ingest_worker.py)
- [backend/src/api/orders.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/api/orders.py)
- [backend/src/services/order_service.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/services/order_service.py)
- [backend/src/services/ocr_job_service.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/services/ocr_job_service.py)
- [backend/src/services/ocr_revision_store.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/services/ocr_revision_store.py)
- [backend/src/services/grid_detector.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/services/grid_detector.py)
- [backend/src/services/pdf_render.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/services/menu_service.py)

### 16.2 ocr-pipeline

- [ocr_pipeline/app/main.py](/Users/mmorinag/Sawa/2025.12/workspace/ocr_pipeline/app/main.py)
- [ocr_pipeline/app/page_correction.py](/Users/mmorinag/Sawa/2025.12/workspace/ocr_pipeline/app/page_correction.py)
- [ocr_pipeline/app/quantity_subgrid.py](/Users/mmorinag/Sawa/2025.12/workspace/ocr_pipeline/app/quantity_subgrid.py)

### 16.3 model

- [backend/src/models/order_ocr_cache.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/models/order_ocr_cache.py)
- [backend/src/models/order_ocr_revision.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/models/order_ocr_revision.py)
- [backend/src/models/ocr_job.py](/Users/mmorinag/Sawa/2025.12/workspace/backend/src/models/ocr_job.py)

### 16.4 既存分析 docs

- [docs/fax_pdf_inventory_20260225.md](/Users/mmorinag/Sawa/2025.12/workspace/docs/fax_pdf_inventory_20260225.md)
- [docs/fax_pdf_inventory_system_2026_02.md](/Users/mmorinag/Sawa/2025.12/workspace/docs/fax_pdf_inventory_system_2026_02.md)
- [docs/llm_ocr_drift_root_cause_analysis.md](/Users/mmorinag/Sawa/2025.12/workspace/docs/llm_ocr_drift_root_cause_analysis.md)
- [docs/llm_ocr_drift_countermeasures.md](/Users/mmorinag/Sawa/2025.12/workspace/docs/llm_ocr_drift_countermeasures.md)
- [docs/ocr_draft_first_stage1_plan_20260316.md](/Users/mmorinag/Sawa/2025.12/workspace/docs/ocr_draft_first_stage1_plan_20260316.md)
- [docs/ocr_draft_first_stage2_plan_20260317.md](/Users/mmorinag/Sawa/2025.12/workspace/docs/ocr_draft_first_stage2_plan_20260317.md)
- [docs/ocr_draft_first_stage3_plan_20260317.md](/Users/mmorinag/Sawa/2025.12/workspace/docs/ocr_draft_first_stage3_plan_20260317.md)

## 17. 最後に

このシステムの現在地を一文で言うと、

`OCR evidence はかなり厚くなったが、draft / confirmed / template / LLM candidate の境界がまだ完全には分離されておらず、その境界漏れが個別事故として現れている`

である。

したがって、今後の問いは「OCR の精度をさらに 5% 上げる」より、

- 何を真実とするか
- どこで止めるか
- どこまで自動採用してよいか
- どの状態で UI に何を見せるべきか

をどう整理するかにある。
