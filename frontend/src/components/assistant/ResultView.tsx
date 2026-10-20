import { useState } from "react";

const names: Record<string,string> = {daily_work:"当日の調理方法",mode:"調理方法",placements:"食材ごとの使用設備",ingredient_ids:"対象食材ID",grouping_question_id:"調理方法の確認記録",question_id:"場所の確認記録",ingredient_name:"食材",location:"作業場所",equipment:"使用設備",document:"作業工程",inputs:"対象",scope:"対象範囲",recipe_revision:"共通工程の版",menu:"献立",revision:"版",headcount_confirmed:"食数確認済み",cost_key:"単価の対応先",key:"対応先",layout_id:"図面番号",layout_version:"図面の版",work_kind:"勤務区分",strength:"適用",weight:"優先度",is_locked:"固定済み",preview:"実施価格の計算結果",payload:"保存した計算入力",schools:"学校別の実施価格",implementation_date:"実施日",headcount:"食数",per_person_price:"1人当たり金額（円）",total_price:"合計金額（円）",cost_items:"単価と仕入先",quantity_items:"食材数量",unit_price_tax_included:"税込単価",price_unit:"単価の単位",origin_or_maker:"産地・製造元",corporation_name:"法人名",representative_name:"代表者名",tax_rate:"税率",recipient:"宛先",amount_per_person:"1人量",total_quantity:"使用総量",total_unit:"使用量の単位",
  start_date:"開始日",end_date_exclusive:"終了日（この日は含みません）",period_mode:"勤務期間の設定",confirmed_at:"確定日時",
  assignments:"勤務表", statuses:"勤務・休暇", violations:"不足・違反", candidate_validation:"検査結果", blocking:"確定を妨げる問題",
  staff_name:"職員", staff_id:"職員番号", work_date:"日付", shift_template:"勤務時間", position:"担当", reason:"理由", message:"内容",
  rows:"一覧", menus:"献立", ingredients:"食材", name:"名称", service_date:"提供日", servings:"食数", required_quantity:"必要量", unit:"単位", ingredient:"食材",
  process_steps:"調理工程", steps:"工程", instruction:"指示", cooking_instructions:"調理指示", assignees:"担当者", operation:"作業", start_time:"開始", end_time:"終了", duration_minutes:"所要時間（分）",
  routes:"動線", label:"表示名", locations:"作業場所", color:"色", points:"経路", source_versions:"元の献立版", source_menu_versions:"元の献立版", current_menu_versions:"現在の献立版",
  version:"版", status:"状態", state:"状態", source_receipt:"原本の出典", values:"訂正する認識値", cells:"原本セル", review_cells:"確認する認識値", cell:"セル", image_png_base64:"原本画像",
  facility:"学校・施設", facility_name:"学校・施設", facility_id:"学校・施設番号", target_month:"対象月", month:"対象月", supplier:"発注先", unit_price:"単価", opening_stock:"月初在庫", incoming:"入荷",
  issues:"要確認事項", inventory_issues:"在庫・発注の要確認事項", inventory_ready:"在庫・発注の計算完了", source_document_id:"取込原本", result:"抽出結果", summary:"集計", totals:"合計",
  date:"日付", quantity:"数量", needed:"必要量", order_quantity:"発注案数量", menu_name:"献立名", text:"内容", confirmed:"確定済み", source:"出典", stale:"元データが変更済み", blockers:"処理できない理由", warnings:"注意事項"
};
const label = (key: string) => names[key] || key;
const metadata = new Set(["grouping_question_id","question_id","daily_work_source","basis","fingerprint","draft_id","source_menu_execution_ids","id","staff_id","facility_id","selected_candidate_id","command_precondition","command_precondition_error","job_status","candidates","source_receipt","source_document_id","source_fingerprint","ocr_job_id","template_id","template_version_id","selected_ocr_result_id","confirmed_snapshot_id","input_revision","candidate_revision"]);
const stateLabels:Record<string,string>={individual:"単独の作業",together:"一緒に調理",separate:"別々に調理",draft:"下書き",succeeded:"作成済み",failed:"処理できませんでした",running:"処理中",queued:"処理待ち",confirmed:"確定済み",needs_review:"内容の確認待ち",working:"勤務",off:"休み",regular:"通常勤務",half_day:"半日勤務",unavailable:"勤務不可",request_off:"希望休",request_work:"勤務希望",hard:"必須",soft:"希望",calendar_month:"月初から月末",position:"担当業務"};
function valueText(value: unknown,key=""): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "はい" : "いいえ";
  return ["mode","status","state","work_kind","strength","period_mode"].includes(key) && typeof value==="string" ? stateLabels[value] || value : String(value);
}
function More({ title, value, expanded=false }: { title:string; value:unknown; expanded?:boolean }) {
  return <section className="result-group"><h4>{title}</h4><ResultView value={value} expanded={expanded}/></section>;
}
export default function ResultView({value,expanded=false}:{value:any;expanded?:boolean}) {
  const [page,setPage]=useState(0);
  if (value===null || typeof value!=="object") return <span>{valueText(value)}</span>;
  if (Array.isArray(value)) {
    if (!value.length) return <p>なし</p>;
    if (value.every(x=>x===null || typeof x!=="object")) return <span>{value.map(item=>valueText(item)).join("、")}</span>;
    const keys=Array.from(new Set(value.flatMap(x=>x && typeof x==="object" ? Object.keys(x):["内容"])));
    return <div><div style={{overflowX:"auto"}}><table style={{borderCollapse:"collapse",fontSize:13}}><thead><tr>{keys.map(k=><th key={k} style={{border:"1px solid #cbd8d1",padding:6}}>{label(k)}</th>)}</tr></thead><tbody>{value.slice(page*50,(page+1)*50).map((row,i)=><tr key={page*50+i}>{keys.map(k=><td key={k} style={{border:"1px solid #cbd8d1",padding:6,verticalAlign:"top"}}>{k==="image_png_base64" && typeof row[k]==="string" ? <img alt="原本の確認セル" src={`data:image/png;base64,${row[k]}`} style={{maxWidth:220,maxHeight:140}}/> : typeof row?.[k]==="object" && row[k]!==null ? <More title="内容を表示" value={row[k]}/> : valueText(row?.[k],k)}</td>)}</tr>)}</tbody></table></div>{value.length>50 && <p><button type="button" disabled={page===0} onClick={()=>setPage(p=>p-1)}>前へ</button> {page*50+1}–{Math.min(value.length,(page+1)*50)} / {value.length} <button type="button" disabled={(page+1)*50>=value.length} onClick={()=>setPage(p=>p+1)}>次へ</button></p>}</div>;
  }
  const business = ["assignments","statuses","violations","candidate_validation","menus","rows","ingredients","process_steps","routes","result","cells","review_cells","issues","inventory_issues"];
  const businessEntries=Object.entries(value).filter(([key])=>!metadata.has(key));
  const sourceEntries=Object.fromEntries(Object.entries(value).filter(([key])=>metadata.has(key)));
  return <div>{businessEntries.sort(([a],[b])=>Number(business.includes(b))-Number(business.includes(a))).map(([key,item])=><div key={key} style={{margin:"8px 0"}}>{item!==null && typeof item==="object" ? <More title={label(key)} value={item} expanded={expanded && ["assignments","rows","menus","preview","schools","orders"].includes(key)}/> : <><strong>{label(key)}：</strong>{key==="image_png_base64" && typeof item==="string" ? <img src={`data:image/png;base64,${item}`} alt="原本セル"/> : valueText(item,key)}</>}</div>)}{!!Object.keys(sourceEntries).length && <details><summary>版と出典の詳細</summary><pre style={{overflowWrap:"anywhere",whiteSpace:"pre-wrap",fontSize:12}}>{JSON.stringify(sourceEntries,null,2)}</pre></details>}<style jsx>{`table{border-collapse:collapse;font-size:13px}th,td{border:1px solid #cbd8d1;padding:6px;text-align:left;vertical-align:top;white-space:pre-wrap;min-width:65px}th{background:#f1f6f3}`}</style></div>;
}
