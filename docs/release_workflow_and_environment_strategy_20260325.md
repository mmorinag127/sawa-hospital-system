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

## Branch And Worktree Policy

現在の標準は Git Flow ベースにする。

- `master`: production の最終反映先。通常作業や stg deploy には使わない。
- `develop`: staging の統合元。feature worktree の merge 先で、stg deploy は原則この branch から行う。
- `feature`: `codex/<feature>` などの作業 branch。1機能または1修正につき1 worktree を作る。
- `release/prod-YYYYMMDD`: prod 昇格用 branch。stg 検証済みの `develop` から切り、prod deploy はこの branch の専用 worktree から行う。
- `stg` 固定 branch は現時点では持たない。stg は `develop` の役割で管理する。stg に固定ブランチが必要になった場合だけ `release/stg-YYYYMMDD` を追加する。

worktree の merge タイミング:

1. feature worktree 内で実装とローカル検証を完了する。
2. feature branch を `develop` に merge する。
3. `develop` の clean worktree から stg deploy する。
4. stg で operator 観点の確認を完了する。
5. `develop` から `release/prod-YYYYMMDD` を作る。
6. prod release worktree で preflight を通し、prod deploy する。
7. prod 検証後、release branch を `master` に merge する。

禁止:

- feature worktree や jj working copy から直接 stg/prod deploy しない。
- `develop` に入っていない sibling commit や jj working-copy commit を含むつもりで deploy しない。
- prod release branch に、stg 未検証の作業 branch を直接 merge しない。
- 診断コード、生成物、本番コードを1つの release 判断単位に混ぜない。

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

## 2026-05-17 Prod Release From Stg

The current prod release flow is documented in `docs/runbooks/prod-release-from-stg-and-exception-db-copy.md`.

Important distinction:

- Normal release: stg is authoritative for code, but prod remains authoritative for orders and menus.
- Exception path: full prod DB replacement from stg is allowed only with explicit approval and `exception_` Taskfile targets.
