# PR のテスト単位の変更影響判定

## 実行方針

- push/PRによる自動試験は起動しない。ローカルbatchのbackend試験で、前回成功した個別テストの実行コード・間接依存・読取入力が同一と確認できた場合だけ再利用する。
- `scripts/local_hospital_test_runner.py --full`、`release/prod-*`、ブランチを識別できない実行は全件対象とする。本番配信gateはimpact receiptを拒否する。
- Node は個別テストが実際に読み取ったファイルと読み込んだモジュールを記録する。トップレベルで読み込むモジュールは同じテストファイルの共有依存になる。
- パラメータ化した Python テストも node ID ごとに判定する。
- 新規・失敗・追跡情報欠落・破損・設定変更は実行する。省略判断に使う記録を PR から公開しない。
- 子プロセス・通信・外部ディレクトリ・追跡できないファイル属性などの入力を使うものは実行する。repo内directoryは全entry名・nsを含むlstat属性・symlink先を記録する。OS標準設定の読取りはroot所有・全親非書込の範囲だけbytehashと元symlink経路を記録し、それ以外の外部ファイルは再実行する。
- lint・型検査・依存監査・インフラ検査は省略しない。期待値と既存テストの指定一覧は変更しない。

## 記録

Python は pytest-testmon 2.2.0 の実行コード追跡と `pytest_impact.py` の入力追跡を併用する。単にカバレッジ外だったという理由でファイル入力を無視しない。共有 fixture の入力は各利用テストへ伝播する。

Node は Node 24 の resolve hook と CommonJS loader のラッパーでモジュール入力を記録し、AsyncLocalStorage で個別テストへファイル入力を対応させる。nested test / hooks を利用するものは省略しない。依存が共通なら両方再実行するため、無変更テストを必ず全件省略できるという保証ではない。

backend履歴は各worktreeのGit管理directory内 `sawa-local-impact/backend` に保存し、lockで同時書込みを拒否する。Python・OS・依存パッケージ・設定・追跡コードを照合する。初回、失敗、中断による履歴不一致、破損時は全件対象へ戻す。失敗runは再利用履歴を公開しない。SQLite追跡DBの論理内容hashを入力履歴に結びつける。

起動ID・ログ用ID・プロセスID・メモリ監視パス・Node worker ID は業務設定と分離する。これらを参照するテストは、環境変数の反射的な参照も含め再実行する。参照しないテストへは起動ごとのID変化を伝播しない。通常のアプリ設定変更は引き続き再実行対象とする。

ローカルの `backend-tests/selection.json` に実行・再利用node ID、理由、比較元、追跡hashを残す。報告hash・source SHA・件数を署名receiptに含め、正規配信gateから既存公開鍵で検証する。通常のログも保持し、再利用を実行成功件数へ加算しない。Node用trackerは別機構として保持しており、今回のbackend選択との接続は行っていない。

## 安全性の検証

`python -m unittest discover -s infra/ci/tests -v`

実際の subprocess でテストを実行し、間接依存・データ・テスト本文の変更に意図的な不具合を入れて、影響するテストが再実行され失敗することを確認する。未変更の同一ファイル内の別テスト、パラメータごとの差、共有 fixture、import 時の入力、壊れた記録、未追跡入力も検査する。Node の ESM と CommonJS の両方を検査する。

公式資料: [pytest-testmon](https://www.testmon.org/)、[Node module hooks](https://nodejs.org/api/module.html#moduleregisterhooksoptions)。
