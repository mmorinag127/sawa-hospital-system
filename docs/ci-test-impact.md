# PR のテスト単位の変更影響判定

## 実行方針

- 通常の push と PR は、前回成功した個別テストについて、実行した Python コードと間接依存、読み取ったファイル、設定、依存パッケージが変わっていなければ再実行しない。
- 手動実行の `full_tests` と `release/prod-*` は全件実行する。
- Node は個別テストが実際に読み取ったファイルと読み込んだモジュールを記録する。トップレベルで読み込むモジュールは同じテストファイルの共有依存になる。
- パラメータ化した Python テストも node ID ごとに判定する。
- 新規・失敗・追跡情報欠落・破損・設定変更は実行する。省略判断に使う記録を PR から公開しない。
- 子プロセス・通信・ディレクトリ列挙・ファイル属性など、変更の有無を保証できない入力を使うものは実行する。
- lint・型検査・依存監査・インフラ検査は省略しない。期待値と既存テストの指定一覧は変更しない。

## 記録

Python は pytest-testmon 2.2.0 の実行コード追跡と `pytest_impact.py` の入力追跡を併用する。単にカバレッジ外だったという理由でファイル入力を無視しない。共有 fixture の入力は各利用テストへ伝播する。

Node は Node 24 の resolve hook と CommonJS loader のラッパーでモジュール入力を記録し、AsyncLocalStorage で個別テストへファイル入力を対応させる。nested test / hooks を利用するものは省略しない。依存が共通なら両方再実行するため、無変更テストを必ず全件省略できるという保証ではない。

キャッシュは suite と実行イメージ（`ImageOS`・`ImageVersion`）ごとに分離し、成功した develop push のみ保存する。初回・キャッシュ失効時は全件実行する。Python/Node のバージョン、設定や追跡ツールの変更でも再計測する。実行イメージを識別できない場合はキャッシュを共有せず、CIを失敗させる。

起動ID・ログ用ID・プロセスID・メモリ監視パス・Node worker ID は業務設定と分離する。これらを参照するテストは、環境変数の反射的な参照も含め再実行する。参照しないテストへは起動ごとのID変化を伝播しない。通常のアプリ設定変更は引き続き再実行対象とする。

Actions の `test-impact-*` / `frontend-test-selection` artifact に実行・再利用したテスト ID を残す。通常の JUnit / テストログも維持する。再利用した Node テストは理由付き skip として表示されるが、その根拠は個別 ID の成功履歴と依存ハッシュである。

## 安全性の検証

`python -m unittest discover -s infra/ci/tests -v`

実際の subprocess でテストを実行し、間接依存・データ・テスト本文の変更に意図的な不具合を入れて、影響するテストが再実行され失敗することを確認する。未変更の同一ファイル内の別テスト、パラメータごとの差、共有 fixture、import 時の入力、壊れた記録、未追跡入力も検査する。Node の ESM と CommonJS の両方を検査する。

公式資料: [pytest-testmon](https://www.testmon.org/)、[Node module hooks](https://nodejs.org/api/module.html#moduleregisterhooksoptions)。
