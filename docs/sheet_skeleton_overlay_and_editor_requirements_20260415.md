# シート骨格・OCR数量overlay・編集操作 要件固定

日付: 2026-04-15  
対象: 注文詳細 Step2/Step3、`draft-sheet`、`ocr-sheet`、`workflow-state`、OCR数量投影、シート編集UI

## 1. 目的

この仕様の目的は、OCR精度の最大化ではない。

目的は次の 2 つである。

1. ユーザーが最低限必ず編集できるシートを壊さないこと
2. OCRの成否や精度に関係なく、正解シートの骨格を安定して維持すること

ここでは「完璧な自動化」ではなく、「最低限機能するシステム」を優先する。

## 2. 用語

- 正解シート
  - その注文で編集・保存・反映の基準になる唯一のシート
- 骨格
  - `date / daypart / menu` の行構造と、template が定める列構造
  - 数量セルの値そのものは含まない
- 表示シート
  - Step2 でユーザーに見せるシート
- 保存シート
  - ユーザーが保存ボタンで保存するシート
- OCR数量overlay
  - OCR出力から数量を抽出し、既存の正解シートの数量セルへ best-effort で重ねる処理

## 3. 非交渉の原則

1. `正解シート = 表示シート = 保存シート` とする。
2. 正解シートの骨格は OCR と独立に先に作る。
3. 骨格生成後、ユーザーの手動操作以外で行や列を追加・削除してはならない。
4. OCR は数量セルへ値を置く補助であり、骨格の所有者ではない。
5. OCR と整合しなくても、骨格を壊すのではなく warning 付きで骨格に合わせて数量を置く。
6. OCR数量投影に失敗しても、空の骨格シートは必ず表示する。
7. シートが編集不能になる状態は、数量精度が低い状態より悪い。

## 4. 正解シートの生成手順

正解シートは次の順で生成する。

1. 注文に対する canonical な `facility` を確定する
2. 注文に対する canonical な `week` を確定する
3. その `facility + week` に対応する template と menu を解決する
4. template の列定義と weekly menu の行定義から、数量空欄の骨格シートを生成する
5. その骨格シートを current draft として扱う
6. すでにユーザー保存済みのシートがある場合だけ、その保存済みシートを current draft として優先する

重要:

- OCR evidence の有無は、骨格生成の前提条件ではない
- OCR evidence が壊れていても、`facility + week + template + menu` が決まるなら骨格は表示する
- OCR evidence が理由で骨格生成が失敗してはならない

## 4.1 保存済みシートがある場合の扱い

すでにユーザーが保存したシートがある場合、その保存済みシートを正解シートとする。

この状態では、system-generated skeleton より保存済みシートが優先される。

ただし、`week` や他の canonical context が変わった場合は、無言で自動変換してはならない。

必須動作:

1. operator にポップアップで解決方法を聞く
2. silent mutation をしない
3. 選択されるまで current saved sheet を勝手に壊さない

最低限必要な選択肢:

1. 数字部分を保持したまま、保存済みシートを新しい週次へ移す
2. 数字部分をクリアして、新しい週次の骨格へ切り替える

このときの原則:

- `date / daypart / menu` の canonical context 変更は operator choice が必要
- 数量の保持・破棄も operator choice が必要
- 勝手に数量を消してはならない
- 勝手に旧週の保存済みシートを新週へ流用してはならない

## 5. 骨格不変ルール

骨格に対してシステムが自動で行ってよいことは、数量セルの更新と warning 付与だけである。

禁止:

- OCR都合で行を消す
- OCR都合で行を追加する
- OCR都合で列を追加する
- OCR都合で列を削除する
- OCR都合で `date / daypart / menu` を別内容へ置き換える
- OCR都合で別の generic sheet や raw table へ表示を差し替える
- canonical context 変更時に、保存済みシートを無言で別週へ変換する
- canonical context 変更時に、保存済み数量を無言で消す

許可:

- 数量セルを空のまま残す
- 数量セルへ OCR 値を best-effort で入れる
- 数量セルへ warning / low confidence / manual review required を付ける

## 6. OCR数量overlayの責務

OCR数量overlay の責務は限定する。

1. 既に存在する正解シートの数量セルへ値を置く
2. 対応先セルが不明なら、そのセルを空のまま残す
3. 対応先の confidence が低いなら warning を出す
4. 必要なら候補や理由を出す

OCR数量overlay がやってはならないこと:

- 骨格そのものを再生成する
- 正解シートを別 row set に置き換える
- row matching に失敗したからといって current sheet を empty にする
- quantity-only OCR を理由に structural row を発明する
- raw OCR table を current editable sheet の代わりにする

## 7. 月跨ぎ・週次日付表示ルール

月を跨ぐ場合でも、表示する日付は常に「週次で指定した日付だけ」とする。

例:

- 週次が `2026-04-29` から `2026-05-05` なら、シートに出してよい日付はその 7 日だけ
- `2026-05` の月次メニュー由来だからといって、その週外の日付を混ぜてはならない
- `4/30` や `5/1` が含まれるのはよいが、`5/6` 以降や前週日付を出してはならない

優先順位:

1. ユーザーが保存した `week`
2. その `week` に含まれる日付集合
3. その日付集合に一致する menu row

OCRや stale hint は、週の外側へ表示を拡張する権限を持たない。

## 8. 表示・保存・再読込の契約

1. Step2 は常に current draft を表示する
2. Save はその current draft をそのまま保存する
3. Reload 後も同じ current draft を表示する
4. バックグラウンド refresh は、ユーザーが保存した current draft を勝手に別シートで置き換えてはならない
5. canonical context 変更が保存済みシートと衝突する場合は、operator choice が確定するまで current saved sheet を保持する

つまり:

- 表示 path
- 保存 path
- 保存後再読込 path

は、同じシートを扱わなければならない。

## 9. 編集UI要件

シート編集UIは、数量修正を現実的にこなせる操作性を持つ必要がある。

最低要件:

1. 範囲選択
2. 選択範囲の一括コピー
3. 選択範囲の一括移動
4. ドラッグによるセル内容の移動または複写
5. 連続入力時の明確な移動規則
6. 右移動だけでなく下移動もキーボードでできること

具体要件:

- `Tab`: 右へ移動
- `Enter` または別の明示キー: 下へ移動
- `Shift+Tab`: 左へ移動
- `Shift+Enter`: 上へ移動
- 矢印キー: 隣接セル移動
- 複数セル選択時、貼り付けは矩形で反映
- 範囲移動時、意図しない骨格列の破壊を防ぐ

将来要件:

- Excel/Spreadsheet に近い操作感
- 数量列中心の高速入力モード
- 複数セルの delete / clear
- 複数セルの fill down / fill right

## 10. エラー時の優先順位

優先順位は次の通り。

1. 骨格が表示される
2. ユーザーが編集できる
3. 保存しても戻らない
4. OCR数量がある程度合う
5. OCR数量が完璧に合う

したがって、次は許容される。

- 数量セルが空
- 一部数量セルが warning 付き
- 低信頼数量をユーザーが手修正する

次は許容されない。

- シートが空
- generic raw sheet に差し替わる
- 骨格が途中で変わる
- reload で行構造が変わる
- 月跨ぎで週外日付が混入する

## 11. 実装判断ルール

実装で迷った場合は、次の原則で判断する。

1. 骨格を守る方を選ぶ
2. OCRの見栄えより編集可能性を優先する
3. quantity overlay を止めても、骨格表示は止めない
4. 骨格と数量overlayは別責務として実装する
5. preview や fallback rendering を fix と見なさない

## 12. 今後の変更で守るべき不変条件

1. current sheet の structural rows は OCR failure で 0 行にならない
2. user-saved draft がある場合、構造はそれを維持する
3. system-generated skeleton は OCR によって row/col count を変えない
4. week outside date は current sheet に現れない
5. `draft-sheet / workflow-state / order detail` は同じ current sheet を参照する

## 13. 完了条件

この方針に沿った実装が完了したと言えるのは、次が満たされた時だけである。

1. 施設と週次だけで骨格シートが生成される
2. OCRが失敗しても骨格シートは表示される
3. OCRは数量セルだけに影響する
4. ユーザー保存済みシートは reload で崩れない
5. 月跨ぎでも週次で指定した日付だけが表示される
6. 保存済みシートに対する週次変更は operator popup で解決される
7. 編集UIが大量修正に耐える

---

この文書は、OCR精度改善より前に守るべきシステム契約を固定するための仕様である。
