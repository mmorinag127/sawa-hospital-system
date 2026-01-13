# Feature Specification: GCPインフラ/IaCブートストラップ（hospital-order-system）

**Feature Branch**: `001-spec-update`  
**Created**: 2025-12-26  
**Status**: Draft  
**Input**: User description: "追加の仕様を additonal-spec.mdに書いたのでこれをよく読んで既存のものと合わせて新しい仕様を考えてください。変更点などがある場合は、それに従って該当箇所を修正するように"

## User Scenarios & Testing *(mandatory)*

<!--
  IMPORTANT: User stories should be PRIORITIZED as user journeys ordered by importance.
  Each user story/journey must be INDEPENDENTLY TESTABLE - meaning if you implement just ONE of them,
  you should still have a viable MVP (Minimum Viable Product) that delivers value.
  
  Assign priorities (P1, P2, P3, etc.) to each story, where P1 is the most critical.
  Think of each story as a standalone slice of functionality that can be:
  - Developed independently
  - Tested independently
  - Deployed independently
  - Demonstrated to users independently
-->

### User Story 1 - 1コマンドでdev環境をIaCで構築 (Priority: P1)

管理者がTerraform applyで必要なGCPリソースをまとめて作成し、再適用で差分0にできる。

**Why this priority**: 環境再現性と手作業削減が最重要。

**Independent Test**: `terraform plan/apply` でリソース作成→再度 `plan` が差分0になる。

**Acceptance Scenarios**:

1. **Given** terraform環境と認証がある, **When** plan/applyする, **Then** API有効化・バケット・Firestore・Pub/Sub・Cloud Run・Secret Manager・Cloud Schedulerが作成される  
2. **Given** apply後, **When** 再度planする, **Then** 差分0となる  
3. **Given** apply後, **When** terraform outputを見る, **Then** Cloud Run URL/Topic/SA/Bucket/Processor ID等が取得できる

---

### User Story 2 - Pub/Sub pushでCloud Run workerを認証付き呼び出し (Priority: P1)

管理者がPub/Sub pushを設定し、認証付きでCloud Run workerを呼び出せる。

**Why this priority**: 非同期処理と通知を安全に動かすため必須。

**Independent Test**: 疑似メッセージpublishでworkerがログ出力し、403が発生しない。

**Acceptance Scenarios**:

1. **Given** push subscriptionを作成, **When** Cloud Run endpointへPOSTする, **Then** 認証付きで到達しログに残る  
2. **Given** push用SA, **When** IAMを確認する, **Then** `roles/run.invoker` が付与されている  
3. **Given** 疑似メッセージpublish, **When** workerが処理, **Then** ログで確認できる

---

### User Story 3 - Gmail Push通知のwatchを自動更新 (Priority: P1)

管理者がwatch更新を自動化し、失効せず継続運用できる。

**Why this priority**: watchは7日で失効し、更新漏れでシステムが止まるため。

**Independent Test**: Schedulerが設定周期でwatch更新を実行し、成功/失敗ログと期限管理ができる。

**Acceptance Scenarios**:

1. **Given** Scheduler設定, **When** 少なくとも7日以内に定期実行する, **Then** watchが継続する（推奨: 毎日）  
2. **Given** watch更新が走る, **When** 成功/失敗を記録する, **Then** ログ/通知で確認できる  
3. **Given** watch有効期限を保持, **When** 期限前, **Then** 再設定が行われる

---

### User Story 4 - Document AI Processorを環境ごとに準備 (Priority: P1)

管理者がOCR用Processorを準備し、ID/Regionを設定として参照できる。

**Why this priority**: アプリのOCR処理に必須。

**Independent Test**: Processorを作成または既存を参照し、ID/Regionを設定で取得できる。

**Acceptance Scenarios**:

1. **Given** Processor作成権限がある, **When** Processorを用意する, **Then** ID/Regionが設定として保存される  
2. **Given** 自動作成が困難, **When** 手動手順を参照する, **Then** runbookに手順が明記される  
3. **Given** worker設定, **When** Processor IDを参照する, **Then** OCRが実行できる

---

### User Story 5 - テンプレ/出力格納先を自動準備 (Priority: P2)

管理者がラベル/納品書/総量CSVのテンプレ格納と出力保存先を用意できる。

**Why this priority**: 運用者がWebから取得できるようにするため。

**Independent Test**: templates/exportsバケットが用意され、テンプレ配置と出力保存ができる。

**Acceptance Scenarios**:

1. **Given** templatesバケット, **When** テンプレを配置する, **Then** ラベル/納品テンプレが保持される  
2. **Given** exportsバケット, **When** 週/日/施設単位で出力を保存する, **Then** 取得できる  
3. **Given** ラベル出力, **When** 項目を確認する, **Then** 現行シール例の項目セットを満たす

---

[Add more user stories as needed, each with an assigned priority]

### Edge Cases

- Gmail watch失効（7日）により通知が止まる → Schedulerで更新必須、失敗通知を行う。
- Pub/Sub push認証設定ミスで403 → push SAに `roles/run.invoker` を付与する。
- Document AI Processorが権限制限で作成できない → runbookで回避/代替手順を提供。
- stg/prod 誤爆防止 → Terraform workspace/環境分離を必須化。
- Secret漏洩防止 → secret値をtfstateやログに残さず、Secret Managerのみで保管。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001 (IaC)**: すべてのGCPリソースを`infra/terraform`で宣言し、環境別(dev/stg/prod)に適用でき、再適用で差分0となる。
- **FR-002 (API enablement)**: 必要APIをコード化して有効化する。
- **FR-003 (Storage)**: raw/templates/exportsバケットを作成し、rawは1〜2ヶ月保持のライフサイクル設定を持つ（設定可能）。
- **FR-004 (Pub/Sub→Cloud Run)**: Pub/Sub topic/subscriptionを作成し、pushを認証付きでCloud Runに送る。push SAに `roles/run.invoker` を付与する。
- **FR-005 (Cloud Run)**: web/worker等のCloud RunサービスをIaCで作成・更新し、実行SAを最小権限で指定する。
- **FR-006 (Gmail watch自動化)**: Gmail watch設定を保持し、Cloud Schedulerで少なくとも7日以内（推奨: 毎日）に更新し、結果と有効期限を記録・通知できる。
- **FR-007 (Document AI Processor)**: Processorを作成し、ID/Regionを設定として保持する。自動化不可の場合、runbookに手順を明記する。
- **FR-008 (Secrets)**: Secret Managerで秘密値の格納先を用意し、tfstateやログにsecret値を残さない。
- **FR-009 (Firestore)**: Firestoreを初期化し、アプリが参照できる権限を付与する。
- **FR-010 (Monitoring)**: Cloud Run/workerエラー率、Pub/Sub滞留、watch更新失敗を検知できる最小限の監視導線を用意する。
- **FR-011 (Templates/Exports)**: templatesバケットにラベル/納品テンプレを置けること、exportsに週・日・施設単位で出力を保存できること。ラベル項目は現行シール例の項目セットを満たす。

### Non-Functional Requirements

- 環境分離: dev/stg/prodでstate分離・命名規約を持ち、誤適用を防ぐ。
- セキュリティ: 公開不要なworkerは原則認証必須（Pub/Sub経由のみ）、最小権限IAMを徹底。

### Key Entities *(include if feature involves data)*

- Environment (dev/stg/prod)
- Terraform State
- Service Accounts (web/worker/push-auth/scheduler)
- Storage Buckets (raw/templates/exports)
- Pub/Sub Topics & Subscriptions
- Cloud Run Services
- Cloud Scheduler Jobs (watch renewal)
- Document AI Processor
- Secret Manager Secrets
- Firestore Database

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: dev環境が手動最小＋Terraform applyで再現でき、再planで差分0となる。
- **SC-002**: Pub/Sub→Cloud Run pushが認証付きで成功し、403が発生しない検証が通る。
- **SC-003**: Gmail watchが7日以内に自動更新され、期限切れで停止することがない（監視で確認）。
- **SC-004**: Document AI Processor ID/Regionが環境ごとに設定から参照できる。
- **SC-005**: templates/exportsバケットでテンプレ配置と出力保存が行え、ラベル項目が現行シール例の項目セットを満たす。

## Assumptions

- GCPプロジェクトとBillingは既に作成済みで、Terraform実行権限がある。
- Gmail OAuth同意や初回のwatch設定は手動承認が必要な場合がある。
- Document AI Processorの作成権限が付与されているか、付与が困難な場合は手動手順で補う。
