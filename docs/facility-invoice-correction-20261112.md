# 原本に基づく施設設定修正

## 固定範囲

原本で確定した不一致だけを修正する。合算、施設名・施設IDの対応は変更しない。
保存済み数量、手動編集、過去の設定版を保持する。本番変更は禁止。
全実装後にローカル直列検証、develop統合、正規Actionsでstg反映、納品書出力を確認する。

## 対象と進捗

|対象|原本・不一致|対応状況|
|---|---|---|
|FAC00004 ふれあい|原本は常食・通所・職員。納品書は別施設形式の5列|ローカル出力・再読込成功。stg新設定版を保存し、通常APIで3列の適用を確認。stg実納品書出力は未確認|
|FAC00003 春日苑松茂|原本は花・月。設定は2F・3F|ユーザー承認により今後の設定を花・月の新規IDへ変更。ローカル出力・再読込成功。stg新設定版の適用と旧版内容保持を通常APIで確認。stg実納品書出力は未確認|
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

1. stgの新設定版で実納品書を出力し、受領したファイルの内容・再読込を確認する。操作用ブラウザがフォーカス制御タイムアウトになり、別タブ・通常セッション確認でもクリック操作がHTTP要求へ到達しない。Macでこのチャットとブラウザを表示するよう依頼済み。APIの通常ナビゲーションによる読取りは成功している。
2. FAC00011の正本FAXを確認して設定を確定する。追加のstg読取り調査でも該当注文0件・アップロード0件。証拠 `tmp/shimanto-stg-original-search-20261112.json`。FAC00015の原本を代用せず、原本提示待ち。

既存注文の選択版・保存済み数量は付け替えていないため、今回の新版追加を、過去注文の納品書が自動訂正された証拠にしない。全体完了とはしていない。
