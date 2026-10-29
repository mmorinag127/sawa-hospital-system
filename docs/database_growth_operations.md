# DB増加と保存・復旧

## 読取専用の容量記録

`scripts/inspect_database_capacity.py` は、PostgreSQLのREAD ONLY transactionで
DB/table/index容量、概算行数、dead tuple、autovacuum、接続数をJSON保存する。
接続URLは既存の環境変数から取得し、表示しない。VACUUM・削除・設定更新は実行しない。

```sh
python scripts/inspect_database_capacity.py --output /private/tmp/sawa-db-capacity.json
```

Cloud SQL設定を添える場合のみ、実際の対象を `--project` と `--instance` で指定する。
接続数予算は `--max-instances --processes --active-engines --pool-size --overflow
--reserved-connections` をすべて実値で指定する。未指定を0や正常と解釈しない。
最大接続数は instance × process × engine × (pool + overflow) + reserved で計算する。
replay engineも含める。追加instanceが同じ接続枠を共有することに注意する。
病院のreplay専用poolは既定size2/overflow0。必要なら専用環境変数
`DB_REPLAY_POOL_SIZE` / `DB_REPLAY_MAX_OVERFLOW` で設定する。
control DBの既存pool設定をreplay先4個すべてへ複製しない。

増加量は同一DBの異なる日付の記録から算出する。一回の容量値から日次増加を推測しない。
tableの行数はpg_stat推定値。実COUNTやSQLの応答時間とは別である。

## 保存期限

病院の既存処理は `backend/src/workers/retention_worker.py`、既定90日・batch200件。
保存済みOCR・sheet履歴・原本参照を保持する。履歴を消して注文削除を通さない。
現状のScheduler地域一覧で未発見であることと、実行経路が存在しないことを混同しない。

定期実行を有効化する前に、次を対象環境ごとに記録する。

| 項目 | 確認内容 |
|---|---|
| 実行主体 | Scheduler/Celery Beat等の具体的なjob/task名と環境 |
| 実行結果 | 最終成功時刻、cutoff、削除件数、失敗理由 |
| 保持 | 注文・保存履歴・原本・job・一時データそれぞれの扱い |
| 参照保護 | FKと業務参照、手動編集・保存履歴の保護 |
| 復旧 | backup/PITRの有効設定、復元先と実復元証跡 |

新しい保存期限・自動削除対象をこの文書で決めたことにはしない。
学校給食・シフトの業務履歴は、期限と復旧方針が確定するまで自動削除を追加しない。

## バックアップと移行

backup enabledとPITR enabledは別。設定取得に欄がない場合は「未確認」とする。
復元の成功・所要時間・許容損失は、別DBへの実復元で記録する。
本番の権限・課金・容量設定変更や復元実行は、この読取ツールから行わない。

エージェントのnative ledger移行は旧SQLite bytesをledger.payloadに保持し、
storage_formatで正本を区別する。通常更新で旧payloadを書き換えない。
旧binaryへのロールバックは通常のdeployロールバックとして扱わず、
native行のバックアップと移行後データの照合を先に行う。古いsnapshotへ戻さない。
native schemaを変更する場合はstorage formatの版と明示的な移行処理を更新する。
SQLite StoreのALTERだけを追加してnative側も更新済みとはしない。起動時の列一致確認は、
未知列の黙殺や未移行列の参照を拒否する。
