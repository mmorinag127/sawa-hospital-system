# エージェント6件の袋詰め・確定

## 固定範囲

ユーザー依頼: 「エージェントの6件も袋詰め・確定まで終わらせろ。春日苑の旧設定の不一致も対応を勝手に決めずに解消して、保存済みシート・手動編集は保持しろ。本番・予算・IAMは変更するな。」

対象は既存試験 `2e4d51b976264951bd1278a341d2be79` の6コピー。
別の通常stg検証コピー2件を、この6件の成功証拠に代用しない。
旧設定・保存シートの上書き、推測での2F/3Fと花/月の対応登録、本番変更、予算・IAM増額は禁止。
完了条件は6件の袋詰めと確定、保存済みシート・手動編集の保持、実状態の再読込一致。

## 現在の実状態

|施設|コピー注文|袋詰め・確定|保存シート|
|---|---|---|---|
|百々家|ORD223be987|確定済みを再読込確認|ODS7983f44260c547c8|
|いこい|ORD35ca861f|確定済みを再読込確認|ODSaed0aa9b504a49ae|
|四万十ピア|ORD3d00284a|確定済みを再読込確認|ODSe04f6b1bc06f44b1|
|春日苑|ORD45810ddb|表示対応登録後、袋詰め・確定・再読込確認済み|ODSc123c37af0ec44ae|
|ふれあい|ORDbd401302|確定済みを再読込確認|ODS94db552870274829|
|湘南|ORDfdb915d5|確定済みを再読込確認|ODSadd04648c86745c8|

- 管理画面から既存試験を `9f8b674e`、今回 `2949bd25` へ引継ぎ。開始時の版・過去結果を保持し、新しい試験は作っていない。
- 最終再照合後のFAX状態は全6件completed。画面も6/6件完了・春日苑注文確定済み。doneという命令状態だけでなく、全12件の袋詰め・確定結果ok trueと、6件のnative state confirmedを確認した。
- 全6件56行の承認済みシート全文のSHA256が、引継ぎ前の既存ローカル保存DB内の各saved draft、および今回の前後読取りと一致。数量・手動編集本文は未変更。
- 証拠: `tmp/six-agent-preservation-before-20261113.json`、`tmp/six-agent-preservation-after-20261113.json`、`tmp/agent-bagging-compact-state-20261113.json`。
- 試験台帳はcap15c、spent15c、reserved0のまま。予算・IAM・本番は変更していない。既存OCR費等を含む総額ではない。

## 春日苑の直接原因と承認内容

現行sandboxのfacility-master SHAとローカル採取資材の一致を確認した。
選択される旧設定のareasは空。一方、保存済みシートと納品書設定は2F/3Fの列IDを持つ。
`label_area_identity_unresolved` は、このエリア名の欠落による。通常stgの新設定がsandboxへ混入して起きたものではない。

原本は花/月。ユーザー回答「この試用コピーだけ2F→花、3F→月を許可」を受領。
旧シートを新OCRで置換したり、確認済み数量を再推定してはならない。
登録は試用注文ID・保存シートID・採用OCR・テンプレート版・シート全文SHAに結び付け、別注文や変更後のシートには流用しない。
旧施設設定版や保存シートは変更せず、表示名だけを隔離コピー内の所有者入力として保持する。
モデルからの実行は禁止し、通常stg/prodでは登録APIを404にする。
実装完了後、ローカル直列一括検証、正規stg配信、既存試験への引継ぎと表示名登録、袋詰め・確定・再読込を実施した。
今回要求された6件の袋詰め・確定・保存本文保持は確認済み。試用全体の帳票完了とは分ける。

## 承認された表示名登録と配信

- 実装ソース: `2949bd25d11f1ef10b7fbb3d77aab869a7a35e8d`。developへ統合し、正規stg run `43348430591` 成功。
- worker: `worker-stg-00962-ghk`、100%、backend digest `6a185ada2a531fe6284eec30723f79aa77f27526c335d87aaad9a07c5c4a6c01`。
- web: `web-stg-00522-jwz`、100%、frontend digest `f68a868eb497c7194efb00d7eeed4c6889be4041547c9f59923468af12494e0a`。
- 最終ローカル直列検証: backend 492、agent 463、frontend 241件成功、型チェック・build成功。全量試験を再実行したという意味ではない。
- 証跡: `tmp/agent-area-release-proof-verified-20261113/`、stg成功を結び付けた署名記録 `tmp/agent-area-staging-proof-20261113/receipt.json`。
- 対象コピー1件の所有者操作 `owner-areas-e0d63e4f44fa200ca38df0a29201e7ddde6ba30754ec8808586a67df2828f07b` はHTTP200・ok true、監査イベントも保存済み。
- 登録内容は `2F: 花, 3F: 月`。対象は `ORD45810ddb` / `ODSc123c37af0ec44ae` / `OEV8651c74d4868` のみ。
- 隔離データの `hospital/owner-area-names/ORD45810ddb.json` SHA256: `95838a2e60b8f701165fea5c5a5a91e9542758ed0d40ba9f24266980c18ca22a`。
- 登録時の保存シートcompact JSON SHA256 `204452558313b8a72b6855d94ef7a6b757d723afd3824ecd485863684fbabc21` は、変更前SQLiteの同じ保存シートから計算した値と一致。
- 証跡 `tmp/six-agent-area-registration-20261113.json` は読取り採取。施設masterの上書き・数量IDの付替え・OCR再実行はしていない。
- 中継は270秒で503になったが、登録要求を再送せず、継続したサーバー処理の実成功を台帳・保存ファイル・監査イベントで確認した。
- 本番・予算・IAMは未変更。検証用ローカルPostgreSQLは停止し、今回作成した一時DBディレクトリを削除済み。Dockerは起動していない。

## 確定と出力の実証跡

|コピー注文|確定スナップショット|確定後revision|
|---|---|---|
|ORD223be987|OCS895440fc52794dd5|12|
|ORD35ca861f|OCS37aad5119fe546d5|12|
|ORD3d00284a|OCS9b1543ff92494869|12|
|ORD45810ddb|OCS6d459f3d98fc4b1f|12|
|ORDbd401302|OCScd01c156c50f48cf|12|
|ORDfdb915d5|OCS84af325900424be5|12|

- `tmp/agent-bagging-compact-state-20261113.json` の最新6確定結果は全てstatus done / ok true / native_state confirmedで、保存シートIDも上表の既存IDのまま。
- 確定操作後、ネイティブAPIからシート・原本・版を再取得する既存after_write照合を通過。さらに業務結果再照合で6件completedを再取得した。
- `tmp/six-agent-preservation-after-20261113.json` の全6件について、変更前とorder ID、56行、fields、全文SHA256一致を再確認。施設masterも採取済みbaselineのSHA256と一致。
- 春日苑ラベルの実出力: `tmp/six-agent-spring-labels-20261113.xlsx`、artifact `72b2acfd1bad4123a54d0b86cb575f5e`、17366 bytes、SHA256 `2b552f3757b24aa466b436f3fb0efc67a4bddaf079959b3fa9899f6f74159337`。
- 圧縮保存された実ファイルを読取り採取し、SHAとサイズを照合して実XLSXを再読込。朝花・朝月・昼花・昼月・夕花・夕月があり、2F/3F表記はない。設定表示だけを出力成功の証拠にしていない。
- 画面証跡: `tmp/six-agent-six-confirmed-20261113.png`。6/6件完了、注文確定済み、使用額$0.15の表示を確認。

## 別の未完了

ふれあいの旧コピーは袋詰め・確定自体は成功したが、旧納品書設定でのexportが
`delivery_quantity_mapping_unresolved` のため、試験全体の帳票出力完了とはしていない。
今回要求の袋詰め・確定とは分けて記録し、正常な別コピーの出力を代用しない。
