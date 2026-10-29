# エージェント業務評価

`agent_business_acceptance.csv` は現在の12業務と確認対象の対応表。
既存試験名は関連箇所を指し、全境界・全業務が実証済みという意味ではない。

## 同じ入力で比較する記録

- 入力原本のhash・施設・期間・対象ID・版、operatorの回答・承認内容。
- provider/modelとexecution_bindingのprompt/task/contract/tool hash。
- tool命令IDとreceipt、承認したreview ID、保存後GET、成果物hash。
- 完了・停止・不確定を分け、エージェントの自己申告で合格にしない。
- 再質問数、訂正回数、tool回数、token/概算費用、業務処理とモデル待ちの時間。

通常の変更では既存MockTransportとnative APIを通すoffline replayを使う。
実モデルの正解率・不要質問・費用評価はこれと別に扱い、未実施は未実施とする。
新規有料APIの実行・支出はこの文書では許可しない。

## 版の違い

新規model実行ではexecution_bindingを保存する。再開時に保存済みbindingと
現在のprompt/task/contract/toolsが違う場合は、異なる契約で続きを自動実行しない。
旧sessionにbindingがない場合、過去のpromptを推測して埋めない。旧記録は由来未確認のまま区別する。

Claude max_list_costはUS cents単位。Geminiの出力token上限は残額の出力単価換算を
超えないよう制限するが、入力token・推論token・単価設定と実請求の差は別に残る。
この制限を請求額の絶対上限保証とは呼ばない。

## 最終検証で追加する項目

native ledger移行、履歴cursorの境界、未回答質問の保持、元のsource eventの保持、
非表示/完了poll停止、契約版不一致、provider別費用境界をまとめて検証する。
実装途中に試験を起動せず、既知の実装差分が揃ってから既存の関連試験へ追加する。
