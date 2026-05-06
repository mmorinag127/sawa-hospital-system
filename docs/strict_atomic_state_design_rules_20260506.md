# Strict Atomic State Design Rules

Date: 2026-05-06

Detailed remediation plan: `docs/strict_atomic_remediation_detail_plan_20260506.md`

## Purpose

Sawa の OCR / 施設テンプレート / シート / 袋分け / 出力は、すべて lineage を持つ業務成果物である。

このため、一般的な CRUD アプリよりも厳しく、以下を設計上の不変条件にする。

```text
見るだけでは絶対に変わらない。
変える処理は明示 command / worker / migration / repair のみ。
補修は許可するが、通常表示に混ぜない。
lineage artifact は immutable。
不整合は blocker。fallback で進めない。
```

今回の直接 failure class は、read path が canonical state を mutate したことである。
`GET /facilities/{facility_id}` が active facility template version を archive/create し、既存 OCR evidence / saved sheet と template version mismatch を発生させた。

## Design Position

一般的な業務システムでは、画面表示時に不足データを補完する lazy initialization や `get_or_create` が許容されることがある。

Sawa ではこれを許容しない。

理由:

- OCR結果、施設テンプレート、シート、袋分け、日別出力は、人間の確認と業務判断を含む成果物である。
- 表示時の補完が入ると、ユーザーが見ていない場所で正解が変わる。
- 1つの補完が downstream artifact 全体を無効化する可能性がある。
- stg/prod、local/stg、旧/新workflowの差分が、補完タイミングの差として再発する。

したがって、Sawa の state management は次の思想を採用する。

```text
CRUDではなく、artifact lineage system として扱う。
read model と command model を分離する。
正解を変える処理は、人間またはworkerが明示的に発火した command だけにする。
不足・矛盾・未解決は自動補完せず blocker とする。
```

## Atomicity Standard

Sawa の command は通常の「保存」より厳しく扱う。

Command は必ず次を満たす。

- input artifact ids が明示されている。
- expected version または expected digest を持つ。
- output artifact id を新規に作る。
- audit に actor / reason / source / before / after を残す。
- downstream を自動再解釈しない。
- 失敗時は rollback するか、failed job として明示的に止まる。

Command がこの条件を満たせない場合は、実行しない。

## State Classes

### Canonical

業務上の正解として扱う state。

- order
- facility template version
- OCR job
- OCR evidence run
- selected OCR result
- saved sheet draft
- bagging result
- output bundle
- confirmed snapshot
- order lines after final confirmation

Rules:

- Canonical state は明示 command だけが変更できる。
- Canonical artifact は lineage id を必須にする。
- 既存 artifact を上書きしない。新 version / new run / new draft として作る。
- 上流 artifact が変わったら下流 artifact は自動更新せず invalidated / blocked にする。

### Derived Projection

Canonical state から画面用に計算される表示。

- overlay preview
- sheet-source preview
- daily output view
- validation result
- inspection payload

Rules:

- Projection は保存しない。
- 保存が必要な場合は materialization command を別に用意する。
- Projection が失敗したら blocker を返す。

### Cache

再計算可能で、正解ではない高速化用データ。

Rules:

- Cache は canonical source にならない。
- Cache miss で canonical state を作らない。
- Cache から evidence / draft / template を復元して current にすることは禁止。
- 復元したい場合は repair candidate として出す。

### Diagnostic

調査・検証用 artifact。

Rules:

- Diagnostic artifact は workflow state を進めない。
- 通常画面から current として読まない。
- 参照するときは source / generated_at / input artifact ids を表示する。

### Repair Candidate

補修候補。まだ canonical ではない。

Rules:

- Repair dry-run は差分だけを返す。
- Repair apply は明示 command。
- ambiguity があれば repair_blocked。
- repair は actor / reason / idempotency key / before digest / after digest / affected records を必須にする。

## Read Path Rules

Read path は次の処理だけを許可する。

- DB上の現在状態を読む。
- Projection を計算する。
- 不足・不整合を blocker として返す。
- 既存 artifact の URI / metadata を返す。

Read path で禁止する処理:

- `session.add`
- `session.flush`
- `session.commit`
- `session.delete`
- `update_job`
- `create_job`
- `persist_*`
- `save_*`
- `ensure_*`
- `sync_*`
- `reconcile_*`
- `backfill_*`
- `refresh_*` with `persist=true`
- `get_or_create_*`
- GCS/DB への永続 artifact 生成

`GET`, `get_*`, `list_*`, `preview_*`, `project_*`, `describe_*` は read path と見なす。

## Command Path Rules

Command path は状態変更を許可する。ただし入力と出力を atomic に固定する。

Rules:

- 1 command は 1 aggregate root を主対象にする。
- 複数 aggregate を跨ぐ場合は DB transaction または worker orchestration にする。
- command input は対象 artifact id と expected version を持つ。
- command output は新 artifact id と lineage を返す。
- command は audit log を残す。
- command 途中失敗時は rollback、または failed job record にする。
- 同じ order に対する mutating job は同時1本まで。

Allowed command examples:

- `POST /workflow/context`
- `POST /ocr-runs`
- `POST /ocr-results/{id}/select`
- `POST /sheet/build`
- `PUT /sheet`
- `POST /bagging`
- `POST /output`
- `POST /confirm`
- `POST /repair/*`
- `POST /migration/*`

## Workflow Step Contract

### Step1: Context Confirm

Inputs:

- original PDF
- facility
- week
- active facility template version

Commands:

- confirm facility/week/template
- run OCR
- optionally ask LLM for facility/week suggestions

Forbidden:

- OCR without resolved template
- sheet generation without confirmed context
- cache/evidence fallback becoming current

### Step2: OCR Selection

Inputs:

- OCR evidence candidates for the order

Commands:

- select exactly one OCR result
- delete OCR result
- rerun OCR

Forbidden:

- multiple current OCR results
- candidate evidence becoming current implicitly
- legacy cache becoming current evidence

### Step3: Sheet Edit

Inputs:

- selected OCR result
- fixed facility template version
- weekly menu base sheet

Commands:

- build sheet draft from selected OCR
- save sheet draft

Forbidden:

- hidden OCR rerun
- hidden template re-resolution
- position remap overwriting explicit saved sheet date/daypart/menu

### Step4: Bagging

Inputs:

- saved sheet id

Commands:

- materialize bagging result
- confirm bagging result

Forbidden:

- bagging from OCR evidence directly
- bagging from unsaved sheet
- fallback from old OrderLine when saved sheet exists

### Step5: Output Confirm

Inputs:

- confirmed bagging result

Commands:

- build output bundle
- final confirm

Forbidden:

- page open causing confirmation
- partial confirmation without atomic snapshot

## Lineage Invariants

The following must hold for current workflow:

```text
workflow.template_version_id == selected_ocr_result.template_version_id
workflow.template_version_id == saved_sheet.template_version_id
saved_sheet.base_evidence_run_id == selected_ocr_result_id
bagging_result.saved_sheet_id == saved_sheet_id
output_bundle.bagging_result_id == bagging_result_id
confirmed_snapshot.output_bundle_id == output_bundle_id
```

If any invariant is not satisfied, the system must block.

It must not repair automatically from a GET response.

## Template Version Rules

- Facility template version is immutable.
- Active version changes only by explicit operator command or migration.
- Read endpoints cannot create, archive, or activate template versions.
- `source=facility-api-get` or equivalent read-source version creation is forbidden.
- Active version must be unique per facility.
- Downstream artifacts must carry `template_version_id`.
- If an artifact lacks `template_version_id`, it is legacy and cannot become current without explicit migration/repair.

## Repair Rules

Repair is allowed and necessary. It is only forbidden to mix repair with display.

Repair must be split into:

1. detect
2. dry-run
3. operator/system approval
4. apply
5. audit
6. verification

Repair apply must persist:

- actor
- reason
- repair type
- idempotency key
- input artifact ids
- before digest
- after digest
- affected record ids
- rollback or invalidation plan

## Migration Rules

- Migration is explicit and isolated.
- Migration may create new canonical versions.
- Migration must never run from normal page GET.
- Migration must write audit records and summary.
- Migration must be idempotent.
- Migration must not silently choose among multiple canonical candidates.

## Naming Rules

Function names must communicate side effects.

Allowed for read-only:

- `get_*`
- `list_*`
- `load_*`
- `project_*`
- `preview_*`
- `describe_*`
- `validate_*`

Mutating names:

- `create_*`
- `save_*`
- `persist_*`
- `materialize_*`
- `select_*`
- `confirm_*`
- `archive_*`
- `delete_*`
- `repair_*`
- `migrate_*`

Danger names:

- `ensure_*`
- `sync_*`
- `reconcile_*`
- `backfill_*`
- `refresh_*`
- `get_or_create_*`

Danger names are not allowed in read paths.

## DB Rules

- Lineage columns should become non-null after legacy migration.
- Current state tables must reference artifact ids, not duplicate full mutable payloads unless clearly snapshotting.
- Output artifacts must reference their input artifact ids.
- Active template version must be constrained to one per facility.
- Audit log must exist for all canonical mutation commands.
- Order lines should be a final-confirm materialization result, not an input fallback during current workflow.

## CI Rules

CI must fail if:

- a `GET` route contains `session.add`, `session.flush`, `session.delete`, `session.commit`
- a `GET` route calls `ensure_*`, `sync_*`, `reconcile_*`, `backfill_*`, `refresh_*`, `get_or_create_*`
- a `get_*` service function writes to DB/GCS
- a canonical artifact can be created without explicit actor/source
- template/evidence/sheet lineage ids are nullable in new current paths
- current workflow can proceed with mismatched template/evidence/sheet ids

## Runtime Guard

Add request-scoped guard:

- mark request method and endpoint
- for GET, reject DB write attempts except explicitly allowlisted diagnostic cache writes
- log stack trace for any attempted write in read path
- fail closed in stg/prod for canonical tables

Canonical tables for strict guard:

- `facility_template_versions`
- `orders`
- `ocr_jobs`
- `order_ocr_evidence_runs`
- `order_sheet_drafts`
- `order_workflow_states`
- `order_current_states`
- `order_confirmed_snapshots`
- `bags`
- `label_rows`
- `delivery_notes`
- `manufacturing_aggregate_rows`

## Rewrite Target

The target architecture should split services by responsibility:

```text
workflow_read_service
workflow_command_service
workflow_projection_service
workflow_repair_service
artifact_lineage_service
facility_template_command_service
facility_template_read_service
ocr_evidence_command_service
ocr_evidence_read_service
sheet_command_service
sheet_projection_service
bagging_command_service
output_command_service
```

The main rule is structural, not stylistic:

```text
Read services cannot import command services.
Command services can call projection/validation helpers.
Projection helpers cannot persist.
Repair services cannot be called from page GET.
```
