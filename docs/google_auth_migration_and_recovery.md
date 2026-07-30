# Google認証への移行・復旧手順

このシステムの人向け認証はGoogleログインのみです。共有Basic認証、共有パスワード、緊急用Basic認証は使用しません。

利用者、管理者、CI検証サービスアカウントの有効状態・ロール・システム権限はDBのユーザー管理データだけを正本とします。`ALLOWED_EMAILS`、`ADMIN_EMAILS`、`ADMIN_SERVICE_ACCOUNTS`などの環境変数を認可の代替やバイパスに使用しません。

## stg / prod移行前チェック

1. 環境ごとの管理画面で、Googleログインに使うメールアドレスを利用者として登録します。
2. `status=active` とし、必要なシステム（病院注文、シフト、学校給食）だけを明示的に付与します。新規ユーザーに自動付与はありません。
3. stgとprodそれぞれに、異なるGoogleアカウントの管理者を最低2名登録します。
4. GitHub Actionsが使用する環境別のCI検証サービスアカウントも、`active` な利用者として登録し、`hospital` だけを明示的に付与します。サービスアカウントへ管理者権限や他システム権限は付与しません。
5. 2名の管理者がGoogleログイン、統合トップ、管理画面、各付与済みシステムへの遷移を確認します。
6. 未登録、停止中、権限未付与のテスト利用者が拒否されることを確認します。
7. GitHub Actionsのstgデプロイ後に上記を再確認してから、承認付きprod workflowへ進みます。ローカルからデプロイしません。

デプロイ検証はGitHub WIFでその場限りのGoogle OIDC IDトークンを発行し、登録済みCI検証サービスアカウントとして実行します。トークンをGitHub Secret、ファイル、ログへ保存しません。未認証拒否の確認に加え、既存のOCR品質・workflow・worker/web整合性検査もBearer認証付きで継続します。

stgとprodのユーザーおよび権限データは共有しません。各環境で登録・確認します。

## Googleアカウントの復旧準備

- 管理者2名以上に、Googleの2段階認証を設定します。
- 各管理者はバックアップコード、予備のセキュリティキー、またはGoogleが提供する別の復旧要素を安全な別保管先に用意します。
- 1名が端末を失っても、もう1名が管理画面から利用者の登録、停止、権限変更を行える状態を維持します。

## 全管理者がログインできない場合

1. まずGoogleアカウントの正式な復旧手段を使用します。
2. 復旧できない場合は、変更内容と対象環境をレビュー可能なコードまたは管理用migrationとして作成します。共有パスワードやBasic認証を追加してはいけません。
3. GitHubのEnvironment approvalで別の承認者が内容を確認します。
4. GitHub Actionsから対象環境だけに適用します。stgで確認後、prodは別承認で実行します。
5. 復旧後、監査ログ、登録ユーザー、システム権限を確認します。

秘密情報をチャット、コミット、workflowログへ出力しません。緊急時もローカルデプロイや共有認証への切り戻しは行いません。

## Secret Manager IAMの2段階移行

project-wideの`roles/secretmanager.secretAccessor`を一度に削除してはいけません。stg、prodを個別に、承認付きGitHub Actionsから次の2回のTerraform applyで移行します。ローカルからapplyしません。

1. 第1回applyでは`retain_legacy_project_secret_accessor=true`を維持します。これにより旧project-wide権限を残したまま、Cloud Runサービスが実際に参照するSecretだけへper-secret IAMが追加されます。
2. apply後、各サービスのSecret参照、起動、Googleログイン、CI検証を確認します。Terraform planでper-secret grantが存在することも確認します。
3. 確認結果をGitHub Actionsのenvironment approval記録へ残します。
4. 第2回applyで対象環境だけ`retain_legacy_project_secret_accessor=false`に変更します。ここで初めて旧project-wide権限を削除します。
5. 再度サービス起動・Secret参照・認証・CI検証を確認します。

変数の既定値とtfvars例は安全側の`true`です。第1回と第2回を同じapplyへまとめたり、検証前に`false`へ変更したりしてはいけません。
