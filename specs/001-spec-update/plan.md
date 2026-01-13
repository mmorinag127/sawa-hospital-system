# Implementation Plan: GCPインフラ/IaCブートストラップ（hospital-order-system）

**Branch**: `001-spec-update` | **Date**: 2025-12-26 | **Spec**: specs/001-spec-update/spec.md
**Input**: Feature specification from `/specs/001-spec-update/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/commands/plan.md` for the execution workflow.

## Summary

GCPで hospital-order-system を運用するためのインフラをIaC化し、dev/stg/prodを再現可能にする。TerraformでAPI有効化、Storageバケット(raw/templates/exports)、Firestore、Pub/Sub topic/subscription（認証付きCloud Run push）、Cloud Runサービス(web/worker)、Secret Manager、Cloud Scheduler（Gmail watch更新）、Document AI Processor設定、監視導線を用意し、apply後に差分0・必要ID/URLをoutputsで取得できる状態にする。

## Technical Context

<!--
  ACTION REQUIRED: Replace the content in this section with the technical details
  for the project. The structure here is presented in advisory capacity to guide
  the iteration process.
-->

**Language/Version**: Terraform (GCP provider), Bash  
**Primary Dependencies**: GCP services (Cloud Run, Pub/Sub, Cloud Scheduler, Secret Manager, Firestore, Cloud Storage, Document AI, IAM)  
**Storage**: GCS buckets (raw/templates/exports), Firestore  
**Testing**: terraform plan/apply（idempotence）、疑似Pub/Sub publish→Cloud Run、Scheduler設定検証、outputs確認  
**Target Platform**: GCP (dev/stg/prod)  
**Project Type**: Infrastructure/IaC  
**Performance Goals**: apply後差分0、watch更新≤7日周期（推奨毎日）、Pub/Sub push成功/403なし  
**Constraints**: Least-privilege IAM、secret値をtfstate/ログに残さない、環境/state分離で誤爆防止  
**Scale/Scope**: 3環境(dev/stg/prod)、Cloud Run(web/worker)、raw/templates/exportsバケット、Document AI Processor

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- IaC idempotence & environment separation (dev/stg/prod) enforced via Terraform state/workspaces.
- Secrets never in state/logs; Secret Manager only.
- Scheduler for Gmail watch refresh ≤7日（推奨毎日） with logging/alerts.
- Pub/Sub push uses dedicated SA with `roles/run.invoker`; Cloud Run endpoints auth-required for workers.
- Outputs/templates buckets respect retention (raw 1〜2ヶ月) and access controls.

## Project Structure

### Documentation (this feature)

```text
specs/001-spec-update/
├── spec.md
├── plan.md
└── checklists/
```

### Source Code (repository root)

```text
infra/
└── terraform/          # IaC modules, env workspaces

docs/
└── runbooks/           # Manual steps (e.g., Document AI if not automated)
```

**Structure Decision**: Infra-focused repo layout with Terraform under infra/terraform and runbooks under docs/.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | n/a | n/a |
