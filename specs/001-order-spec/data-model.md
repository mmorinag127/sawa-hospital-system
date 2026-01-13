# Data Model: 病院・施設 発注FAX自動取込〜出力 (MVP)

**Branch**: 001-order-spec  
**Spec**: specs/001-order-spec/spec.md  
**Date**: 2025-12-23

## Entities

### Facility
- id (FACxxxxx), name, code (optional), created_at/updated_at.
- Relationships: has many FacilityArea, FacilityMasterConfig, Orders.

### FacilityArea
- id (ARExxxxx), facility_id (FK), name, sort_order.
- Relationships: belongs to Facility.

### FacilityMasterConfig
- id (FMCxxxxx), facility_id (FK), fax_template (layout/regions), packaging_policy, label_profile, invoice_template_ref, column_mapping, retention_policy, ocr_retry_limit, timezone (JST default).
- Relationships: belongs to Facility.

### WeeklyMenu
- id (WEKyyyyWww), week_start, source_file_uri, status, created_by.
- Relationships: has many MenuItem; applies to many Facilities (global with per-facility overrides).

### MenuItem
- id (MEIxxxxx), weekly_menu_id (FK), name, unit_type, qty_per_serving, temp_type (hot/cold), daypart (朝/昼/夕), category, facility_override (nullable).

### OrderDocument
- id (DOCxxxxx), facility_id (nullable until resolved), week_id (nullable until resolved), storage_uri, source_email_id, ocr_attempts, status (pending/processing/failed/success).
- Relationships: belongs to Order once resolved.

### Order
- id (ORDxxxxx), facility_id, week_id, status (未着/要確認/確定/エラー), current_document_id, superseded_document_ids, created_at/updated_at.
- Relationships: has many OrderLine, Bags, Outputs; belongs to Facility, WeeklyMenu.

### OrderLine
- id (OLNxxxxx), order_id (FK), date, daypart, menu_item_ref, diet_type, area_id, quantity_original, quantity_corrected, change_note.

### Bag
- id (BAGxxxxx), order_id (FK), date, daypart, menu_item_ref, diet_type, area_id, quantity, label_bucket (for split bags).

### LabelRow
- id (LABxxxxx), order_id (FK), bag_id (FK), fields per label spec (facility_name, exp_date, storage, meal_slot, area, menu_category, product_name, qty, details, maker_info, notice).

### DeliveryNote
- id (INVxxxxx), order_id (FK), facility_id, date, columns per facility mapping, generated_at.

### ManufacturingAggregateRow
- id (MAGxxxxx), week_id, facility_id, menu_item_ref, diet_type, area_id, quantity, generated_at.

### User
- id (USRxxxxx), role (admin/operator), account (Google for admin, ID/PW for operator), status, created_at.

### AuditLog
- id (AUDxxxxx), actor (user/worker), action (upload/update/confirm/config_change), target (entity/id), metadata (FAC/WEK), created_at.

### Notification
- id (NTFxxxxx), target_role (admin/operator), type (business_error/system_error), message, related_entity, sent_at.

## Validation & Rules

- IDs: prefixed codes per FR-001.
- Facility resolution: if facility not confidently extracted, order stays facility_unconfirmed until operator selection.
- Duplicate facility-week: newest OrderDocument supersedes older; older outputs excluded.
- OrderLine: quantity_corrected takes precedence; change columns treated as final values (not deltas).
- Zero quantities: omitted from LabelRow output.
- Retention: emails/PDFs kept per retention_policy (default 1–2 months).
- Access: admin only for master config/menu upload; operator for confirm/edit; both authenticated per role.
- Status flow: 未着 → 要確認 → 確定; エラー reachable on processing failure; confirm only from 要確認.

## State Transitions

- Order.status
  - 未着 (created on ingest) -> 要確認 (extracted/awaiting operator)
  - 要確認 -> 確定 (operator confirm)
  - any -> エラー (processing failure; recover by retry)
  - 要確認/エラー -> 要確認 (reprocessing or new document supersedes)

## Derived/Outputs

- Bag, LabelRow, DeliveryNote, ManufacturingAggregateRow regenerated on confirm and when facility/week/menu config changes relevant to the order.
