# 本番コピーの更新

stg共通ヘッダーの「本番コピーを更新」から「本番データからコピーを更新」を実行します。更新中も既存テストDBと以前のコピーを利用できます。完了後は参照DBの「本番再現用（最新）」を選びます。実行中のOCR・シフトと開いた旧画面は開始時のコピーIDを保持し、別世代へ書き込みません。

更新は既存Cloud Tasks/workerを利用します。新しいDB名とファイルprefixに作成し、本番のDBはREAD ONLY/REPEATABLE READのスナップショットからpg_dumpします。pg_restore後の全ユーザースキーマの表について全行・全列のハッシュと正式migration headを比較します。PDFとJSON設定の参照ファイルはgenerationを固定してコピーしSHA256を照合します。数量・採用値・原本参照を書き換えません。

全システムのDB・PDF・設定・immutable manifest・各bucketへの同一manifest配置と読戻し検証が完了してから、中央registryのactiveと成功状態を同一CASで更新します。途中失敗・権限不足・必須設定の不足・schema不一致・設定変更・二重実行・期限切れworkerでは公開しません。失敗した新DBや旧コピーを自動削除しません。

## 本番開設時の同期元設定

初期manifestの病院施設マスターが指す中央stg bucketの `stg-replay/control/sources.json` が同期元の正本です。未設定時は画面に「本番の同期元が未設定」と表示し、更新は実行しません。stg/Testやコード内のサンプルを本番として代用しません。

形式は `schema: 1` と `components`（`order`, `shift`, `school-lunch`）です。各componentに次の項目を設定します。接続文字列・パスワードをこのファイルや画面へ保存しません。

| 項目 | 値 |
|---|---|
| source_service | 正規本番deployで確定した `projects/実project/locations/実region/services/実service-prod` |
| producer_service | 病院だけ必須。正規 `ocr-pipeline-prod` serviceの完全名（DB接続を推測せず、実serving revisionのOCR設定を別に取得） |
| destination_service | 対応する既存stg serviceの完全名（末尾 `-stg`） |
| destination_bucket | 対象stg serviceの現在のmanifest URIと同じbucket |
| settings_keys | 本番serviceのenvから取得する外部JSON設定・保存先名の配列。病院は `FACILITY_MASTER_URI`, `INGEST_POLICY_URI`, `OCR_PIPELINE_STATE_URI` が必須。シフトは `GCS_BUCKET_NAME`。学校は空配列（下記の業務環境設定はこの配列と別に自動取得） |
| expected_migration_heads | 参照するstg版の正式migration headの配列。本番schemaと不一致なら公開せず停止 |
| source_buckets | 本番原本・設定の実bucket名の配列。stg bucketは禁止 |
| local_objects | 本番DB内のローカル原本参照がある場合の、正本ローカル参照→正規GCS原本の明示対応。DBにlocal source receiptがあればURI文字列、なければ `{uri, source_identity, sha256}`（uriはgeneration固定、source_identityは元ファイルのmode/device/inode/size/mtime_ns）。全featureで元receiptとのsize/SHA一致必須。なければ空object |

Cloud Runの実100% traffic revisionを固定し、そこに設定されたDB接続と業務設定を取得します。Secret Manager参照のDB値も既存参照先から取得し、値はメモリ内だけで使います。sourceのCloud SQL instanceは `-prod`、destinationは `-stg` が必須で、既存Test DB名や既存COPY DBへのrestoreは禁止です。

全runtimeは中央の `stg-replay/control/registry.json` だけを共通の公開判断として読み、manifest本体と原本は各自のstg bucketを読みます。中央registryの1オブジェクトを読めない実行IDには、そのオブジェクトだけの読取許可が必要です。別featureのPDFやDBの読取許可は不要です。

本番のJSONマスター・設定は上記envの正規GCS URIで公開してください。正規GCS上ですでに存在しない履歴原本は `missing_at_source` として記録し、欠損状態を維持します。必須設定・ローカル原本の明示対応・アクセス権・generation・SHA256を確認できない場合は推測・古いseed・既存Testへの置換は行わず、更新失敗として現在のコピーを保持します。保存先bucket設定はstg側の隔離bucketへ変換します。

受付APIは `web-stg` の既存API proxyから `worker-stg` へ転送され、既存 `worker-exec-stg` の中央bucket権限でregistryを作成・更新します。frontend実行IDにregistry書込を追加する必要はありません。病院の同期元・同期先serviceはDBと業務設定を持つ `worker-prod` / `worker-stg` を指定します。

既存worker実行ID (`worker-exec-stg`) に本番service/revisionと3つのstg service/revisionの参照、必要な既存secretの参照、本番DB接続・SELECT、本番原本bucket読取、stg側の新DB作成・復元、3つのstg隔離bucketの新規 `stg-replay/replay-*` prefixへの書込・検証読取の権限が必要です。これらの本番開設時権限は現在の共有registry GETとは別で、未設定時は同期を完了扱いにしません。本番DBのmajorと一致するPostgreSQL client（15/16/17/18）を使い、復元先が古いmajorなら公開せず停止します。この実装はIAM grant・SA key・本番service deployを行いません。権限不足は更新失敗として表示されます。

## DB外の業務環境設定

新COPYにはbackend用 `runtime_settings`、producer用 `producer_runtime_settings` と `runtime_settings_provenance: production_environment` を保持します。病院のprovider/model/トークン数/数量判定・ROI・監査・業務制限等はbackendの実revisionから、Yomitoku DPI/device/テンプレート照合/補正等はproducerの実revisionから取得します。学校は `IMPLEMENTATION_PRICE_OCR_DPI`, `IMPLEMENTATION_PRICE_OCR_DEVICE`, `PROCESS_GEMINI_MODEL`, `GEMINI_API_BASE` を固定します。シフトの業務ルールはDBに含まれます。対象の明示whitelistは共通 `staging_data.py` の `RUNTIME_SETTING_KEYS` です。

COPY処理はこの辞書だけを参照します。文字列は正本の値、`null` は本番envが未設定であることを表し、その場合だけ既存コードの同じcallsite既定値を使います。stg/Testのenv値による補完はしません。業務設定がSecret Manager参照の場合は公開を停止し、参照値を設定manifestへ保存しません。API key・DB password・認証・transport・物理保存先は複製せず既存stgを維持します。接続先baseに埋込credentialやqueryがあれば公開を止めます。明示された送り状テンプレート原本は他原本と同様にコピー・SHA検証して利用します。

初期COPYは過去にstgのenvで稼働していたため、正規初期manifest IDとSHAに限定した `legacy_stg_runtime` provenanceを中央registryへ明記し、その既存挙動を維持します。初期コピーのstg設定を本番設定取得済みと表現しません。新しい本番COPYでこの互換経路を使うことはできず、辞書不足や偽legacy指定は停止します。

## 状態API

- `GET /staging-datasets/refresh`：同期元設定済みか、更新状態、最新COPY ID。
- `POST /staging-datasets/refresh`：`{"request_id":"32文字のhex"}`。既存operator認証が必須。同じIDは同じ要求で、送信失敗時に再送できます。進行中に別IDを受け付けません。
- workerは既存の厳密なOIDC認証で `/worker/staging-copy-refresh/{request_id}/{delivery_generation}` を処理します。ブラウザのDB選択から本番/保存先の接続値を受け取りません。

本番DB未開設の実同期は検証済みと報告しません。native PostgreSQL・GCS/Cloud Tasksの契約試験とstg画面の実確認を区別して記録します。
