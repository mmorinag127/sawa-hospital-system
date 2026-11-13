# 原本に基づく施設設定修正

## 固定範囲

原本で確定した不一致だけを修正する。合算、施設名・施設IDの対応は変更しない。
保存済み数量、手動編集、過去の設定版を保持する。本番変更は禁止。
全実装後にローカル直列検証、develop統合、正規Actionsでstg反映、納品書出力を確認する。

## 対象と進捗

|対象|原本・不一致|対応状況|
|---|---|---|
|FAC00004 ふれあい|原本は常食・通所・職員。納品書は別施設形式の5列|stg専用コピーでOCR・保存・袋詰め・納品書HTML表示、実Excel受領と再読込まで実施。3列を確認|
|FAC00003 春日苑松茂|原本は花・月。設定は2F・3F|花・月の新規IDへ変更。stg専用コピーでOCR・原本照合・保存・再読込・袋詰め・納品書HTML/Excelを確認。HTMLの内部ID表示も共通修正してstg再確認済み|
|FAC00011 ケアハウス四万十|OCR常食1列、納品書5列|原本未確認。FAC00015四万十ピアの原本を流用しない|
|FAC00008/9/10|軟菜を2F・3Fから合算|業務上の合算判断は変更しない|
|FAC00013/16|原本名と別登録名の対応不明|改称・統合・ID付替えをしない|

## 履歴の扱い

納品書設定も注文のconfigured_inputに保存されるため、現行JSONだけを直しても
保存済み注文の納品書は訂正されない。古いFacilityTemplateVersionの直接更新、
設定digestの偽装、最新設定の暗黙流用は行わない。
訂正版を正規の設定版として保存し、出力に使う版と保存済み数量の由来を明示する経路が必要。
設定版の追加は既存のPOST /facilities/{facility_id}/versionsを使用する。
旧注文の選択版とOCR・数量は付け替えない。新設定版を選択した注文で出力を確認し、
旧注文の納品書が自動的に訂正されたとは扱わない。

春日苑はfuture版内のfax_templateとfax_template_override、およびmain_ocr_row_fieldsを
同時に変更する。花=hana、月=tsukiという新規IDで、旧階名へのaliasは追加しない。
納品書の既存の軟菜1列合算は変更しない。

## 共通出力処理の追加原因

固定の主菜・副菜枠があるExcelでは、設定列名の反映処理が呼ばれず、
元ブックの見出しをそのまま使っていた。固定枠にも設定列名を反映し、
見出し帯内の古い階名と結合を除去する。数量セル・施設名・帳票上部は変更しない。
見出しと数量セルにまたがる結合は推測で解除せず明示的に拒否する。
備考のように右隣まで広がる見出し結合は、見出し帯内に限って全幅を扱う。
元のXLSXパッケージにも結合解除を反映し、再読込後に花・月が別セルで保持されることを確認した。

## stg正本の相違

ふれあいは未日付参照設定の数量列source_indexが5/6/7だが、日付選択される
保存済み初期設定版は4/5/6だった。stgの設定版も4/5/6。
未日付参照の位置を設定版へ流用せず、正規の日付選択結果に対して
納品書だけを訂正する。FAXの読取位置・原本・数量は変更しない。

## 証拠

`tmp/all-facility-format-audit-20261112/report.json` と同ディレクトリの原本PDF・見出し画像。
11施設21原本を目視。未取得の原本は確認済みにしない。

## 最新の実行証跡

- 稼働コード: `999e58ccc9c3d6ead869b8d36c125bd3b3e005a8`。
- 正規stg配信: `43223605032` success。先行run `43221814602` はビルド中に取消し、配信は未実施。
- worker: `worker-stg-00960-88d`、web: `web-stg-00520-7r4`。
- 最終ローカル一括検証: backend 446、agent 461、frontend 240成功、型チェック・ビルド成功。
- 署名付き記録: `tmp/fax-invoice-original-release-proof-20261112/receipt.json`。
- 原本テンプレート固定試験: `backend/tests/fixtures/invoice-original-e0edc384.xlsx`、SHA256 `e0edc3849dacfaabbeb81eb9329cea7baa4cd994c16ef69e187716981bb274ad`。
- ローカル実ファイル: `tmp/fureai-reviewed-invoice-example-20261112.xlsx`、`tmp/kasuga-reviewed-invoice-example-20261112.xlsx`。stg実出力の代用にはしない。

## stg設定更新と保持確認

保存APIは実行アカウントのGCS書込権限不足で保存できなかった。正本generation不変を確認し、APIの再送はしていない。
ユーザーが既存利用者権限によるJSON1件だけの更新を明示承認したため、正規コードの設定検証・履歴検証・世代CASを通して更新した。
IAM追加、Cloud Runのローカル配信、本番操作、DB・数量の更新は行っていない。通常の設定保存APIの書込権限自体は変更していない。

- 対象: `gs://sawahospitalsystem-stg-templates/modernization/bootstrap-20261014-80e985f3e26a/canonical-master.json` のみ。通常のtestデータ設定であり、本番再現コピーや試用6件の専用コピーには流用していない。
- generation: `1792023308187897` → `1794489125605053`。
- ふれあい新設定版: `invoice-correction-b1d47d046a0b88792dcb476d42216841e7ac1d6dc596d87060bffd0c0d183179`。
- 春日苑新設定版: `invoice-correction-58c71526370d5ade21a0a5c2f141ee2e450ae2ccee19fb4466be088a12cc84c4`。
- 新版の開始日は2026-11-12。旧版の適用終了日だけ2026-11-11とし、旧版のfacility内容は通常stg APIの再取得で完全一致を確認した。
- 更新証拠: `tmp/stg-invoice-settings-applied-20261112.json`。
- 通常stg API応答: `tmp/fureai-stg-resolved-after-20261112.json`、`tmp/kasuga-stg-resolved-after-20261112.json`、`tmp/fureai-stg-version-history-after-20261112.json`、`tmp/kasuga-stg-version-history-after-20261112.json`。

## 残件

1. FAC00011の正本FAXを確認して設定を確定する。追加のstg読取り調査でも該当注文0件・アップロード0件。証拠 `tmp/shimanto-stg-original-search-20261112.json`。FAC00015の原本を代用せず、原本提示待ち。
2. トマトソースの実用一人量は未確定。通常stgの1gはユーザー承認の仮値であり、本番へ流用しない。既存6件エージェント試験の春日苑は旧エリアIDのため別途未完了。

既存注文の選択版・保存済み数量は付け替えていないため、今回の新版追加を、過去注文の納品書が自動訂正された証拠にしない。全体完了とはしていない。

## 2026-11-13 専用コピーでの実確認

- ユーザー承認: 「2件の検証注文作成・OCRを許可」。予算・IAM・本番は変更しない。
- 通常アップロードは `ingest_order_facility_selection_changed` で拒否。同じ施設・週の旧注文が選ばれ、新設定版との不一致を拒否した。検査解除や旧注文の版付替えはしていない。OCR開始前の拒否であり、アップロード成功の証拠にはしない。
- 既存の非破壊コピー処理で、ふれあい `ORD156414a8` → `ORD15b8bf3e`、春日苑 `ORD7ceeb0b1` → `ORDc348d8ac` を作成。
- 既存コピー処理には文書版・取込証跡がないため、コピー2件に限り、原本を参照する新文書・版・取込情報を登録。共通の版登録・PDF証跡検査を使用し、ダイジェスト検査は省略していない。通常アップロードの検証とは区別する。
- 原本SHA-256: ふれあい `39a93ea01241d73ebb6b344b8bb4a84b678b9b91cf46b4ead5f63b0086290f2b`、春日苑 `27b30e1dde2bd9e83c5b9f97f8adbb17fc38bb4e8337f35c2f85a67decbe70a2`。
- コピー作成と各追加入力の前後で、元注文・元数量の一致を確認。保存シート・OCR実行履歴・選択状態はコピーしていない。
- 作成・検査記録: `tmp/stg-invoice-copy-receipt-20261113.json`。実行資材: `tmp/create_approved_invoice_copies_20261113.py`。再実行によるコピー追加はしない。
- 既存6件試験の専用コピーだけに、ユーザー承認済みのトマトソース仮一人量1gを登録し、正規読取りで一致を確認。`trial_provisional_menu_amount` 記録あり。実運用の値は未確認。試験費用は確認時点で$0.15、予約$0のまま。6件全完了とはしていない。

### 実確認の更新

- 通常stg初期設定に欠けていた `daily_label_comparable_diet_types` は、採取済み正本の同施設に登録された値を継承。ふれあいは `change_1/no_fish/no_fried/no_meat`、春日苑は `mixer/no_fish/soft`。新たな食種判断や合算は追加していない。
- 同一の承認済みJSON1件へ新版を追記し、generation `1794489125605053` → `1794499960021856`。旧版facility内容保持・世代CAS・再取得一致を確認。証拠 `tmp/stg-invoice-policy-applied-20261113.json`。本番・IAM変更なし。
- 新版: ふれあい `invoice-policy-31b9c8b93df5ed9c8d35dc8c5fc95728422b21c9601a752dc083fef62402f185`、春日苑 `invoice-policy-011b4b53e12529caa434ac7961558a3112554890b398fb16d63d99fc2f2fe7ed`。
- ふれあい: 新版OCRは6分05秒、`OEV5c89b1f9a666`。56行を原本照合し、数量欄の空欄・斜線を誤認した64セルだけ空欄へ手動修正。数値セルと備考は変更していない。保存シート `ODS5bcab6d2e4a64d7e`、袋詰め `OBG5e3882ddaf624e0b`、出力 `OOB771d58dcc762428e`。
- ふれあい納品書HTMLと実受領 `/Users/mmorinag/Downloads/ORD15b8bf3e_delivery.xlsx` の見出しが常食・通所・職員。HTML画像 `tmp/fureai-delivery-html-20261113.png`。Excelの他施設テンプレートシートは元ブックのまま残るため、これらの内容が当注文の出力であるとは扱わない。
- FAX読取シートExcelは別出力であり、`saved sheet source workbook not configured` の400。納品書Excel成功とは区別する。
- 春日苑: 新版OCRは11分39秒、`OEV7a4cedb49b51`。原本の花・月を56行目視照合。空欄上の線を誤認した17セルと、原本7を1と誤認した2セルを、検証コピーだけで手動修正。保存シート `ODS96788b067fd04083`。修正記録 `tmp/spring-source-reviewed-operator-edits-20261113.json`。
- 春日苑の出力停止原因は、workerログ2026-11-12T16:48:30Zの `derived_accessory_amount_unresolved`。通常stgに添えトマトソースの一人量がない。エージェント専用環境の仮値を共有設定へ無断で流用していない。
- 保存済み旧シート保持と両コピーの選択版・保存状態は、読取り専用照合 `tmp/stg-invoice-copy-state-20261113.json` に記録。
- 両コピーとも画面の再読込後にStep3を再表示し、56行の入力値が原本照合後の保存内容と完全一致。証拠 `tmp/fureai-saved-reload-20261113.json`、`tmp/spring-saved-reload-20261113.json`。ふれあいの受領Excelは対象施設の7日分すべてで3列を保持し、11/17・11/18・11/20夕の原本空欄も空欄であることを確認。

### 既存6件エージェント試験

- 現時点の袋詰め・確定成功は5/6件。春日苑 `ORD45810ddb` は未確定であり、全6件成功とはしない。
- 最新の耐久化DBと必要成果物9件をハッシュ照合してローカル採取し、同一保存シートの袋詰め失敗を再現。ライブ書込み・OCR・AI呼出しなし。
- 根本の拒否理由は `label_area_identity_unresolved`。旧コピーの2F・3Fと、新設定の花・月が異なるため、エリア正本へ結び付けられない。対応を推測するaliasは追加しない。通常stg新コピーの一人量不足とは別原因。
- 診断ログ `tmp/agent-spring-bag-diagnosis-20261113.log`。5件の成功・春日苑失敗のコンパクト証拠 `tmp/agent-bagging-compact-state-20261113.json`。
- $0.15はこの専用エージェント試験の台帳額だけ。別途行った通常OCR・数量異常チェックのAI・Cloud Run費用を含む総費用ではない。

### 通常stgの仮一人量登録

- ユーザー回答「stgだけ仮値登録を許可」を受け、通常testのメニューマスターへトマトソース1gを登録。分類に `stg仮値・未確認` と明記した。実用量が確定した意味ではなく、本番に流用しない。
- 登録前に同名を検索し、単体トマトソースは未登録であることを確認。既存のチキンカツ・白身魚のフライ本体の100g設定は変更しない。
- 登録証拠 `tmp/stg-tomato-provisional-menu-20261113.json`、同名PNG。保存済みシートを再生成・変更せず、出力だけ再試行。
- 春日苑の袋詰め `OBG851bc0c5bbf74350` と出力 `OOBb50cc1d06c1e4fd3` は生成成功、状態 `output_review`。選択OCRと保存シートIDは不変。
- 納品書HTMLは表示できたが、見出し再構成が登録済み表示名を捨てて内部area_idを表示していた。captured facility内のarea名をpreviewへ渡し、集計IDを保持したままHTML表示に使用する修正を追加。未配信の修正をstg確認済みとはしない。

### 表示名修正の最終確認

- 実装修正・関連試験追加後に一括直列検証。backend 483件、agent 461件成功、スキップ0。frontend試験・型チェック・ビルドも成功。証拠 `tmp/invoice-area-release-proof-20261113/commands.json` とログ/JUnit。
- 試験記録の自動生成は公開鍵環境変数未指定で停止したため、実成功ログとJUnitのハッシュ・同一clean sourceを照合して署名を生成。元の配信済みversion10 baselineを使用し、試験再実行・未実施の成功化はしていない。署名付き記録 `tmp/invoice-area-release-proof-20261113/receipt.json`、記録処理 `tmp/finalize_invoice_area_receipt_20261113.py`。
- develop統合・配信ソース `9f8b674ed0a57be07b1f34069ffe947ca320ac21`。正規stg run `43335089074` success。今回のworkflow起動は1回。本番・予算増額・IAM追加なし。
- worker `worker-stg-00961-qhn`、100% traffic、image `backend@sha256:4931d7b52538b477d6fc98850b06f3323429567b5ca264d9eff7d094c87b71d4`。
- web `web-stg-00521-bsm`、100% traffic、image `frontend@sha256:d1b050723378a0b067e4a47258235e06a4e6dc506cdb0e7fed69b78767cf25e3`。実画面footerも9f8b674ed0a5。
- 春日苑HTMLは常食 花/月・軟菜・ミキサー 花/月を表示。修正前後の56行は見出し以外すべて完全一致。証拠 `tmp/spring-delivery-html-before-area-label-20261113.json`、`tmp/spring-delivery-html-after-area-label-20261113.json`、対応PNG。
- 配信後に実受領した `/Users/mmorinag/Downloads/ORDc348d8ac_delivery (1).xlsx` は、配信前受領Excelと全シートの全セル値が一致。対象施設7日分の見出しが常食 花/月・軟菜・ミキサー 花/月であることも確認。
- workflow画面の再読込後も選択OCR `OEV7a4cedb49b51`、保存シート `ODS96788b067fd04083`、袋詰め `OBG851bc0c5bbf74350`、出力 `OOBb50cc1d06c1e4fd3` を保持。今回の2件納品書確認を、旧エージェント6件の全確定成功とは扱わない。
- Dockerは使用せず、一時ローカルPostgreSQLを使用。終了後に停止・削除し、診断用の約315MB DB複製もSHA照合後に削除。診断ログと元の既存ローカルデータは保持。
