# Release Workflow And Environment Strategy (2026-03-25)

## Goal

今後、別機能を並行で進めても、

- ローカル差分が混ざらない
- deploy が最後勝ちで潰れにくい
- 本番前に段階確認できる
- 実運用開始後も `dev / stg / prod` を明確に使い分けられる

状態を標準運用にする。

## Target Model

### local dev

- 個人開発
- 単体動作確認
- 1機能 = 1 branch + 1 worktree

### cloud dev

- Cloud Run / Cloud SQL / OCR pipeline を含む統合寄り確認
- 開発中の統合確認で使う

### staging

- 本番前の最終確認
- `prod-like`
- 公開しない
- scheduler は原則 off
- 現状の対象は `sawahospitalsystem / asia-northeast2`
- 同一 project を使うので、project-wide リソースは再管理せず、Firestore collection 名を `-stg` で分離する
- `tofu init/plan` は backend bucket にアクセスできる ADC か `GOOGLE_APPLICATION_CREDENTIALS` が必要
- Cloud Scheduler service agent の IAM は `project_number` を明示的に持たせる

### production

- 唯一の公開環境
- scheduler / 外部連携 / 定期処理を有効
- 最も厳しい gate を適用

### feature preview

- branch 単位の一時確認環境
- `preview-web` / `preview-worker` を軽量に出す
- scheduler / PubSub push / 実データ書き込みは持ち込まない

## Workflow Standard

1. `task worktree_add_feature FEATURE=<name>`
2. worktree で実装
3. local test / local run
4. 必要なら cloud dev または feature preview で確認
5. `stg` へ deploy
6. `stg` で operator 観点の確認
7. 同じ image / commit を `prod` に promote

重要:

- `build once, promote many`
- `prod` で rebuild しない
- dirty workspace から直接 deploy しない

## What We Standardize Now

今回の repo 更新で標準化するもの:

- env 共通の predeploy check スクリプト
- env 共通の web deploy スクリプト
- worktree 作成スクリプト
- Taskfile の `stg` / worktree / script check タスク

## Test And Gate Policy

### local

- 機能単位の unit / integration / frontend build

### release tooling

- `task ops_script_check`
- shell script は最低限 `bash -n` を通す

### staging

- `task predeploy_stg_checks`
- 本番相当の導線確認
- OCR 品質 gate は warming-up を許容

### production

- `task predeploy_prod_checks`
- `task deploy_prod_web`
- `task deploy_prod_worker`
- OCR quality / web proxy / protected API を strict で確認

## Current Status

ここまでで反映済み:

- `Taskfile.yml` に `predeploy_stg_checks`, `deploy_stg_web`, `deploy_stg_worker`, `worktree_add_feature` を追加
- env 共通の predeploy / web deploy スクリプトを追加
- `infra/terraform/envs/stg` を `prod-like` に更新
- `stg` に Cloud SQL / auth / OCR pipeline / service env / paused scheduler を追加

残り:

- `stg` を `task infra_stg_plan` / `task infra_stg_apply` で実際に apply する
- `stg` の実デプロイと operator 観点の通し確認
- feature preview 用の軽量 Cloud Run deploy を追加する

## Rule Of Thumb

- まず分けるのは `service` ではなく `workflow`
- `worker` の細分化は後でよい
- 先に `worktree`, `stg`, `promote`, `feature flag` を整える

この順番なら、今の monolith 構成を壊さずに、並行開発と実運用の両方を滑らかにできる。
