import type { SheetPayload } from './workflowTypes';

export type QuantityReview = {row_index: number; col_index: number; field: string; raw_text: string; confirmed_blank: boolean};
export const isQuantityField = (field: string) => /^(qty\.|qty_|quantity)/.test(field);
export const isNumericQuantity = (value: string) => {
  const text = value.trim().replace(/[０-９]/g, digit => String(digit.charCodeAt(0)-0xff10)).replace(/．/g,'.').replace(/－/g,'-').replace(/，/g,',');
  return /^-?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?$/.test(text) && Number.isFinite(Number(text.replace(/,/g,'')));
};

export function quantityReview(sheet: SheetPayload | null): QuantityReview[] {
  return Array.isArray(sheet?.quantity_ocr_review) ? sheet.quantity_ocr_review as QuantityReview[] : [];
}

export function editableQuantities(sheet: SheetPayload): SheetPayload {
  const rows = sheet.rows.map(row => [...row]);
  const review = new Map(quantityReview(sheet).map(item => [`${item.row_index}:${item.col_index}`, {...item}]));
  rows.forEach((row, r) => row.forEach((value, c) => {
    const field = sheet.fields[c];
    if (field && isQuantityField(field) && value.trim() && !isNumericQuantity(value)) {
      review.set(`${r}:${c}`, {row_index:r, col_index:c, field, raw_text:value, confirmed_blank:false});
      row[c] = '';
    }
  }));
  let rejected = 0;
  const items = sheet.ocr_numeric_cell_items?.map(item => {
    const field = sheet.fields[item.target_col_index ?? -1];
    if (!field || !isQuantityField(field) || !item.value?.trim() || isNumericQuantity(item.value)) return item;
    rejected += Number(item.classification === 'accepted');
    return {...item, raw_text:item.value, value:'', classification:'unresolved'};
  });
  const summary = sheet.ocr_numeric_cell_summary;
  return {...sheet, rows, ...(review.size ? {quantity_ocr_review:[...review.values()]} : {}), ocr_numeric_cell_items:items,
    ...(rejected && summary ? {ocr_numeric_cell_summary:{...summary,
      accepted_count:Math.max(0,Number(summary.accepted_count || 0)-rejected),
      unresolved_count:Number(summary.unresolved_count || 0)+rejected}} : {})};
}

export const pendingQuantities = (sheet: SheetPayload | null) => quantityReview(sheet).filter(item =>
  !sheet?.rows[item.row_index]?.[item.col_index]?.trim() && !item.confirmed_blank);
