# sawa-food.com 独自ドメイン接続手順

## 目的

いま動いている会社ホームページ:

- `https://sawa-food.com/`

はそのまま Xサーバーで運用しつつ、このシステムを同じ親ドメイン配下で公開します。

公開したいURL:

- 本番: `https://hospital-app.sawa-food.com/`
- 開発: `https://dev-hospital-app.sawa-food.com/`

この手順書は、次の前提で書いています。

- あなたが Xサーバーのアカウントにログインできる
- あなたが Google Cloud のプロジェクトにログインできる
- Google ログインは残す

## 最初に重要なこと

このアプリを **Xサーバーに移す必要はありません**。

正しい形はこれです。

- `sawa-food.com` の既存サイトは Xサーバーのまま
- このシステム本体は Google Cloud Run のまま
- Xサーバーでは主に **DNS 設定** を行う

つまり、Xサーバーは「サイト本体を置く場所」というより、今回の作業では主に

- ドメイン設定
- DNS レコード設定

に使います。

## 今回の完成イメージ

- 会社サイト: `https://sawa-food.com/`
- 本番アプリ: `https://hospital-app.sawa-food.com/`
- 開発アプリ: `https://dev-hospital-app.sawa-food.com/`
- Google 審査向け公開ページ:
  - `https://hospital-app.sawa-food.com/about`
  - `https://hospital-app.sawa-food.com/privacy`
  - `https://hospital-app.sawa-food.com/terms`

## 現在のシステム情報

本番サービス:

- Web: `web-prod`
- API / worker: `worker-prod`
- OCR: `ocr-pipeline-prod`

現在の本番URL:

- Web: `https://web-prod-avlnzjjrca-dt.a.run.app`
- API: `https://worker-prod-avlnzjjrca-dt.a.run.app`

開発側の名前:

- Web: `web-dev`
- API / worker: `worker-dev`

公開ページはすでにあります:

- [about.tsx](/Users/mmorinag/Sawa/2025.12/workspace/frontend/src/pages/about.tsx)
- [privacy.tsx](/Users/mmorinag/Sawa/2025.12/workspace/frontend/src/pages/privacy.tsx)
- [terms.tsx](/Users/mmorinag/Sawa/2025.12/workspace/frontend/src/pages/terms.tsx)

## DNS を切り替える前に必要なアプリ側修正

独自ドメインに切り替える前に、アプリ側で直す必要がある箇所があります。

### 1. フロントの canonical host

対象:

- [middleware.ts](/Users/mmorinag/Sawa/2025.12/workspace/frontend/src/middleware.ts)

現状:

- `web-prod-avlnzjjrca-dt.a.run.app` を canonical host として扱っています

必要な修正:

- 本番 canonical host を `hospital-app.sawa-food.com` に変更
- 開発側も `dev-hospital-app.sawa-food.com` を許可

### 2. フロントの API 接続先判定

対象:

- [apiClient.ts](/Users/mmorinag/Sawa/2025.12/workspace/frontend/src/services/apiClient.ts)

現状の注意点:

- カスタムドメインで開いた時、`/api` ではなく worker の直接URLを使う可能性があります

必要な修正:

- `hospital-app.sawa-food.com`
- `dev-hospital-app.sawa-food.com`

の両方で `/api` を使うようにする

### 3. CORS 設定

対象:

- [prod main.tf](/Users/mmorinag/Sawa/2025.12/workspace/infra/terraform/envs/prod/main.tf)
- [dev main.tf](/Users/mmorinag/Sawa/2025.12/workspace/infra/terraform/envs/dev/main.tf)

追加が必要な origin:

- `https://hospital-app.sawa-food.com`
- `https://dev-hospital-app.sawa-food.com`

### 4. Google OAuth 設定

対象の env:

- `GOOGLE_OAUTH_CLIENT_ID`
- `GOOGLE_OAUTH_CLIENT_IDS`

必要な作業:

- 新しい独自ドメインを Google OAuth の許可 origin に追加

## Step 0: DNS の管理先が本当に Xサーバーか確認する

最初に、`sawa-food.com` の DNS を本当に Xサーバーで管理しているか確認します。

### Xサーバーでの確認

1. `XServerアカウント` にログイン
2. `サーバー管理` を開く
3. 対象サーバーの `サーバーパネル` を開く
4. `ドメイン` -> `DNSレコード設定` を開く

確認したいこと:

- `sawa-food.com` が DNS レコード設定の対象として表示されるか

もし表示されないなら:

- DNS 管理は Xサーバーではありません
- その場合は、実際の DNS 管理会社側で同じ DNS 設定を行います

## Step 1: GCP 側で公開の受け口を作る

おすすめ構成:

- 外部 HTTPS Load Balancer を 1 つ作る
- グローバル固定IPを 1 つ使う
- Google 管理証明書を使う
- ホスト名ごとに振り分ける

振り分け:

- `hospital-app.sawa-food.com` -> `web-prod`
- `dev-hospital-app.sawa-food.com` -> `web-dev`

## Step 1-1: グローバル固定IPを予約する

Google Cloud Console の場所:

- `ネットワーク サービス` -> `IP アドレス`

作業:

1. `外部 IP アドレスを予約`
2. 種別を `グローバル`
3. 名前の例: `hospital-app-lb-ip`

この値を控えます。

- `LB_IP = ______`

## Step 1-2: Cloud Run サービスを確認する

Google Cloud Console の場所:

- `Cloud Run`

確認:

- `web-prod` が存在する
- `web-dev` が存在するか

もし `web-dev` がまだ無いなら:

- 先に本番だけ進める
- `dev-hospital-app.sawa-food.com` は後で追加する

## Step 1-3: Load Balancer を作る

Google Cloud Console の場所:

- `ネットワーク サービス` -> `ロードバランサ`

作業:

- 外部 `Application Load Balancer` を作成
- バックエンドに Cloud Run の serverless backend を設定

バックエンド:

- 本番: `web-prod`
- 開発: `web-dev` があれば追加

## Step 1-4: SSL 証明書を作る

Google 管理証明書で次を登録します。

- `hospital-app.sawa-food.com`
- `dev-hospital-app.sawa-food.com`

## Step 1-5: Host rule を設定する

設定:

- Host: `hospital-app.sawa-food.com`
  - backend: `web-prod`
- Host: `dev-hospital-app.sawa-food.com`
  - backend: `web-dev`

## Step 2: Xサーバーで DNS を設定する

GCP 側で Load Balancer と固定IPができてから行います。

### Step 2-1: DNS 設定画面を開く

Xサーバーの場所:

1. `XServerアカウント`
2. `サーバー管理`
3. `サーバーパネル`
4. `ドメイン` -> `DNSレコード設定`
5. `sawa-food.com` を選択

### Step 2-2: A レコードを追加する

追加するレコード:

| 用途 | ホスト名 | 種別 | 値 |
|---|---|---|---|
| 本番アプリ | `hospital-app` | `A` | `LB_IP` |
| 開発アプリ | `dev-hospital-app` | `A` | `LB_IP` |

注意:

- `sawa-food.com` 本体のレコードは消さない
- 今回はサブドメインだけを GCP に向ける

### Step 2-3: サブドメイン設定は必要か

結論:

- **DNS だけなら必須ではありません**

Xサーバーの `サブドメイン設定` は、Xサーバー側に実際のサイトを置く時に主に使います。  
今回はアプリ本体を GCP に置くので、重要なのは DNS レコードです。

## Step 3: Google Search Console で所有確認する

Google の審査では、親ドメイン `sawa-food.com` の所有確認が必要です。

### Step 3-1: Search Console を開く

使う Google アカウント:

- Google Cloud の OAuth 設定を触る予定のアカウント

### Step 3-2: ドメインプロパティを追加

1. Search Console で `プロパティを追加`
2. `ドメイン` を選ぶ
3. `sawa-food.com` を入力

すると、TXT レコードが表示されます。

### Step 3-3: Xサーバーに TXT を追加

Xサーバーの場所:

- `ドメイン` -> `DNSレコード設定`

追加するもの:

| ホスト | 種別 | 値 |
|---|---|---|
| ルート | `TXT` | `google-site-verification=...` |

### Step 3-4: 検証する

Search Console に戻って `確認` を押します。

期待結果:

- `sawa-food.com` が verified になる

## Step 4: Google OAuth を設定する

## Step 4-1: OAuth 同意画面

Google Cloud Console の場所:

- `API とサービス` -> `OAuth 同意画面`

設定値:

| 項目 | 値 |
|---|---|
| アプリ名 | `hospital-app` |
| サポートメール | 合意したサポート用メール |
| ホームページ | `https://hospital-app.sawa-food.com/about` |
| プライバシーポリシー | `https://hospital-app.sawa-food.com/privacy` |
| 利用規約 | `https://hospital-app.sawa-food.com/terms` |
| 承認済みドメイン | `sawa-food.com` |

## Step 4-2: OAuth クライアント

Google Cloud Console の場所:

- `API とサービス` -> `認証情報`

対象:

- Web アプリケーションの OAuth client

### Authorized JavaScript origins

登録する値:

- `https://hospital-app.sawa-food.com`
- `https://dev-hospital-app.sawa-food.com`

### Authorized redirect URIs

現行のログイン実装は、Google Identity Services のボタンから ID トークンを受ける方式です。

そのため現状では:

- `redirect URI` は必須ではありません
- 重要なのは `Authorized JavaScript origins` です

将来、リダイレクト型 OAuth に変えた時だけ callback URI を追加します。

## Step 5: 本番切り替え前にやるシステム変更

本番切り替え前に、次のコード / 設定変更を先に入れるのが安全です。

### 本番

- canonical host を `hospital-app.sawa-food.com` に変更
- `CORS_ALLOW_ORIGINS` に `https://hospital-app.sawa-food.com` を追加
- カスタムドメイン時の API 呼び出しを `/api` に統一

### 開発

- canonical host / origin を `dev-hospital-app.sawa-food.com` に対応
- `CORS_ALLOW_ORIGINS` に `https://dev-hospital-app.sawa-food.com` を追加

## Step 6: 最終確認

### DNS / SSL

- `hospital-app.sawa-food.com` が Load Balancer の IP を向いている
- `dev-hospital-app.sawa-food.com` が Load Balancer の IP を向いている
- HTTPS 証明書が有効になっている

### 画面

- `https://hospital-app.sawa-food.com/` が開く
- `https://hospital-app.sawa-food.com/about` が公開で見える
- `https://hospital-app.sawa-food.com/privacy` が公開で見える
- `https://hospital-app.sawa-food.com/terms` が公開で見える

### ログイン

- 本番ドメインで Google ログインできる
- 開発ドメインで Google ログインできる

### アプリ動作

- `/pdf-upload` が使える
- `/orders` が使える
- API proxy が独自ドメインでも動く

## DNS 設定表

実作業前にこれを埋めて使ってください。

| 用途 | ホスト | 種別 | 値 | 設定場所 | 完了 |
|---|---|---|---|---|---|
| 本番アプリ | `hospital-app` | `A` | `LB_IP` | Xサーバー DNS | |
| 開発アプリ | `dev-hospital-app` | `A` | `LB_IP` | Xサーバー DNS | |
| Search Console 確認 | ルート | `TXT` | `google-site-verification=...` | Xサーバー DNS | |

## OAuth 設定表

Google Cloud の設定時にこの表を埋めて使ってください。

| 項目 | 値 |
|---|---|
| Authorized domain | `sawa-food.com` |
| Homepage URL | `https://hospital-app.sawa-food.com/about` |
| Privacy Policy URL | `https://hospital-app.sawa-food.com/privacy` |
| Terms URL | `https://hospital-app.sawa-food.com/terms` |
| JS origin (prod) | `https://hospital-app.sawa-food.com` |
| JS origin (dev) | `https://dev-hospital-app.sawa-food.com` |
| Redirect URI (prod) | 現行の GIS ボタン方式では不要 |
| Redirect URI (dev) | 現行の GIS ボタン方式では不要 |

## おすすめの実施順

1. Xサーバーで DNS 管理ができるか確認
2. GCP で固定IPと Load Balancer を作る
3. Xサーバーで A レコードを追加
4. Search Console で `sawa-food.com` を確認
5. OAuth 同意画面と OAuth client を更新
6. アプリ側の独自ドメイン対応をデプロイ
7. 本番URLで確認
8. 開発URLで確認

## ロールバック

切り替えで問題が出た場合:

1. 既存の `*.a.run.app` はそのまま残す
2. `hospital-app` と `dev-hospital-app` の DNS を戻す、または外す
3. 必要なら OAuth origin 設定を見直す

## 関連ファイル

- [middleware.ts](/Users/mmorinag/Sawa/2025.12/workspace/frontend/src/middleware.ts)
- [apiClient.ts](/Users/mmorinag/Sawa/2025.12/workspace/frontend/src/services/apiClient.ts)
- [prod main.tf](/Users/mmorinag/Sawa/2025.12/workspace/infra/terraform/envs/prod/main.tf)
- [dev main.tf](/Users/mmorinag/Sawa/2025.12/workspace/infra/terraform/envs/dev/main.tf)
- [about.tsx](/Users/mmorinag/Sawa/2025.12/workspace/frontend/src/pages/about.tsx)
- [privacy.tsx](/Users/mmorinag/Sawa/2025.12/workspace/frontend/src/pages/privacy.tsx)
- [terms.tsx](/Users/mmorinag/Sawa/2025.12/workspace/frontend/src/pages/terms.tsx)
- [login.tsx](/Users/mmorinag/Sawa/2025.12/workspace/frontend/src/pages/login.tsx)
