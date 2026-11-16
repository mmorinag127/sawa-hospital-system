# OCR原文編集・原本保持

## 固定範囲

- 備考と数量の認識原文を保持して編集・保存・再読込へ渡す。施設・期間・原本・セル位置の検査とは分離。
- 本番・注文の付け替え・原本参照の自動差替え・予算・IAMの変更なし。
- 全修正後にローカル直列検証、develop統合、正規stg配信、stg実操作で確認。

## 修正一覧と進捗

|項目|状態|内容|
|---|---|---|
|備考の低信頼文字による全体拒否|stg確認済み|captured実データ11件の配置blocker 0。実OCRの備考記号3セルを保持してreadyへ進み、編集・保存・再読込できた|
|数量記号による受付失敗|stg確認済み|実OCRの数量記号24セルを原文保持。1セルを手動訂正し、残り23セルを記号のまま保存・再読込できた|
|重複アップロードによる原本世代消失|stg実確認済み|ORDda5a9803の同一PDF再受付後も実GCS世代1794818318683609不変、注文1件・OCRジョブ1件|
|期間・施設不一致|原因調査済み・既存注文変更なし|下記参照|
|以前の施設名メモが残る不具合|stg実確認済み|ふれあいでメモ入力後に四万十ピアへ変更し、メモが空になることを確認|
|ローカル一括検証|完了|最終backend760・agent482・frontend315成功、skip 0。型チェック・build成功|
|stg確認|完了|専用注文をOCRからシート生成・手動編集・保存・再読込まで実操作し、読取り照合した。確定・出力・本番操作はしていない|
|ネイティブOCR完了後の旧状態更新|修正・stg反映済み|完了状態はworkflow-v2で原子的に確定済み。重複した旧projectionで別接続が自分のingestロックを待つため、native完了から旧projectionを呼ばない。元のlease・upload終端検査を維持し、成功・失敗と旧経路の4分岐を一括検証|

## 不一致の原因

PDF登録APIは施設と対象週の明示選択を要求するが、入力された同じ値を全ファイル・分割後の全ページへ適用する。
原本画像の記入対象月や施設名とは照合せず、原本の自動判定を確定情報として使用していない。
そのため11月記入ページと12月記入ページが別々の注文として同じ選択期間へ登録され得る。
4注文の原本と記入対象期間の不一致、ORD913926b6の原本施設と明示選択の不一致は、この境界で防止されていない。
既存注文の登録元は選択された施設・週であり、ファイル名や推定から付け替えない。

施設名メモはフォームで最初の施設選択時に自動補完されるが、施設を変更しても非空なら更新されない。
これがfacility_name=百々家、facility_hint=春日苑という別の入力メタデータ不一致を作る。
今回、自動補完を廃止して任意入力のメモとして扱い、施設変更時にクリアするよう修正した。
このメモはcanonical facility_selectionとは別で、原本施設がいこいの森プラスであることまで説明するものではない。

原本世代は保存済みPDFの同一hash・同じ名前が同じobject pathになる一方、重複判定前に無条件uploadしていた。
同一内容の再登録が旧世代を失わせるため、原本保存をcreate-onlyかつ同内容再利用へ変更する。
既に消失した本番世代の参照は変更しない。

## 証拠と未確認

追加配信阻害: run43827591571はcandidate producerがOCI index digestだったため、保護タグのLinux/amd64 digestとの比較で停止。
当該失敗時点のstg worker/webは7e9fc8d9のまま。原本・注文には影響なし。事前報告の「backend反映済み」は誤りで訂正済み。
実registryのindex8b127bd6...を読取り、Linux実体00603f5e...との相違を確認。
保護とproducer配信の両方で既存のmanifest検証を通して同じLinux実体へ解決するよう修正。
checkout/sourceラベル/署名/OIDC/IAM/環境設定比較/実Ready digest検査は維持。

調査正本: /Users/mmorinag/Sawa/2025.12/docs/PROD_OCR_20261129_1205_AUDIT_20261116.md
実データの読取り証跡: /Users/mmorinag/Sawa/2025.12/tmp/prod-nov29-audit-20261116/
サブエージェント指定gpt-5.6-terra/lowは現在の利用可能一覧にないため、代替モデルは起動せず主担当で作業。
初回配信成功: run43830443689 / source548ee1ddec8155cff9bb18672a07d1b18829cecc。
この版で専用注文のOCRが完了し、備考・数量の記号を原文保持したready evidenceを作成した。
専用注文ORDda5a9803、受付UPL2bac2d7f、FAC00015、2026-12-01〜12-05。
原本SHA256:e6aa194d0606286e0bf66d8f5011ff4500e713a587016b0c9a0316f41b9206e8。

後続採用操作では、Cloud Run容量待ち429を検出。DB読取りでnative完了後の旧projection接続995600がingestロックを保持し、別接続995624のFOR SHAREを待つ自己待機を確認。採用要求も同じ注文ロックを待っていた。
stgのこの専用注文のmessage_id・business_generation・blocking PIDが一致する待機SELECTだけをpg_cancel_backendで取り消し、旧projectionの未コミット変更をロールバックさせた。OCR evidenceは既にコミット済みで保持。解消後に待機SQLなし、upload completed、OCR doneを確認した。他注文や本番の接続は取り消していない。

最終配信成功: [run43835580245](https://github.com/mmorinag127/sawa-hospital-system/actions/runs/43835580245) / source86f411ba072a7d5017cafb7892c58e75e682020d。
worker-stg-00966-cjm / web-stg-00526-6k9、双方100% traffic。
backend image: sha256:1881f9e9da83e3d86754f393391454713138251e3aebe3514561969fe2a08635。
frontend image: sha256:8097f0016dbfbdaff2ea56d39a03ae5d2980a6190bb9005491e8de40f9b567a4。
最終ローカル証跡: tmp/ocr-editable-native-completion-proof-20261116/。
昇格済み署名記録: tmp/ocr-editable-native-staging-proof-20261116/receipt.json。
今回のActionsは3回（配信前停止1回、stg配信成功2回）。追加予算・IAM変更なし。

## stg編集・保存・再読込の実証

- 修正版86f411baの画面で、選択OCR OEVc4a400dcec9fから40行・400セルのシートを生成した。
- 原本で空欄のF12（数量）とK15（備考）だけを「-」から空欄へ手動訂正。他セルの記号・数量は変更していない。AI補正や候補の自動採用はしていない。
- 通常の異常チェック・シート確認を通し、ODS4e0feb7a5b7046a8として保存。workflow=sheet_saved、revision=10、blockers=[]。
- ページ再読込後に編集欄400セルを読取り照合。手動訂正2セルが保持され、数量記号23セル、備考「-」「---」2セルも編集可能なまま残っている。
- OCR証拠280セルとの全セル比較で、手動訂正2セル以外は認識原文と一致。OCR evidence自体・注文レコード・原本GCS世代は変更前と完全一致。
- 署名付きローカル検証と実画面確認を区別する。最終版で新たなOCRを重複実行せず、同一専用注文の既存認識結果を生成・編集・保存・再読込した。
- 読取り照合: tmp/verify_ocr_editable_live_20261116.py、tmp/ocr-editable-live-proof-20261116.json。
- 再読込画面: tmp/ocr-editable-stg-reloaded-20261116.jpg。全400セル: tmp/ocr-editable-stg-reloaded-cells-20261116.json。
- 期間不一致4件・施設不一致1件・消失済み本番原本世代は付け替えていない。本番の停止注文を再実行・復旧したという意味ではない。
