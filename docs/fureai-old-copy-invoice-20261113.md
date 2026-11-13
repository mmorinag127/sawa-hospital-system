# ふれあい旧コピーの納品書訂正

## 固定要件

対象は試用 `2e4d51b976264951bd1278a341d2be79` の `ORDbd401302`。
原本 `ORDf021b65f.pdf`、保存シート `ODS94db552870274829`、OCR `OEV11776f190a37`、確定 `OCScd01c156c50f48cf` を保持する。
原本と保存シートは常食・通所・職員の独立列。旧納品書だけが別書式の5数量列を参照している。
原本SHA256: `39a93ea01241d73ebb6b344b8bb4a84b678b9b91cf46b4ead5f63b0086290f2b`。

合算、列の推測対応、施設名・ID付替え、旧設定版・保存シート・手動編集の上書き、本番・予算・IAM変更は禁止。
完了条件は全修正後のローカル直列検証、正規stg反映、同コピーの実納品書出力・再読込と保存本文保持の確認。

## 調査結果

- 画像 `tmp/all-facility-format-audit-20261112/FAC00004-ORDf021b65f-header.png` を目視。常食・通所・職員と禁食/変更欄は別欄。
- 保存シートのfieldは `qty.regular_x` / `qty.daycare_x` / `qty.staff_x`。その他の保存済み禁食・備考欄も変更しない。
- 確定済みmaterializationのoutput_linesもregular/X・daycare/X・staff/Xだけ。新しい合算・食種付替えは必要ない。
- 選択された初期設定版のFAX位置は4/5/6。未日付の参照設定にある5/6/7を流用しない。
- 同版のinvoice_templateはregular/2F・regular/3F・soft/合算・mixer/2F・mixer/3Fであり、正当な停止 `delivery_quantity_mapping_unresolved` を起こす。

## 修正一覧

|項目|状態|
|---|---|
|原本・選択版・保存シート・確定済み数量の照合|完了|
|原本の一対一列検証を共通化し、欠落・重複・合算を拒否|実装済・未検証|
|同コピーの原本SHA・保存本文SHA・確定入力に結び付く訂正記録|実装済・未検証|
|納品書HTML・Excel・日次出力で同じ訂正を使用|実装済・未検証|
|所有者だけの訂正登録・保存本文再照合・修正版引継ぎ後の再開|実装済・未検証|
|関連fixture・不足試験の一括更新、ローカル直列検証|追加済・未実行|
|develop統合・正規stg配信・実納品書出力確認|未開始|

訂正は旧configured_inputを変更せず、隔離コピー1注文の出力用注記として保存する。
通常stg/prodでは登録不可とし、他注文・シート変更・原本変更へ暗黙に流用しない。

## 統合元の確認

専用WT `worktrees/prod-preparation-20261105`、branch `codex/agent-memory-recovery-20261111`、起点 `2c8cb7347ebdbc002355bafc94aa0c27f15fcf94`。
remote developも同一。稼働worker `00962-ghk` / web `00522-jwz` のimage digestは前回配信2949bd25と一致し、起点との差分は確認記録だけ。
workspaceのjj作業コピーはemptyで、古いprod branchを今回の統合元にしない。
別branchのaa603b04/8ec8c8b6は10月のOCR幾何・認識修正、565b0a82は旧read receipt修正であり、今回の納品書訂正への未統合差分として流用しない。
