import Link from "next/link";

export type OcrOperation = {
  order_id: string | null; document_id: string | null; job_id: string | null;
  kind: string | null; execution_id: string | null; generation: string | number | null;
  status: string; source_status?: string | null;
  created_at?: string | null; updated_at?: string | null; blockers: string[];
  job?: {error_message?: string | null; error_detail?: string | null};
};
export type OcrOperationsPayload = {
  source: string; observed_at: string;
  summary: {status: string; counts: Record<string, number>; total: number};
  intake?: Record<"uploaded_pdfs" | "ingest_jobs", {pending_count?: number;
    processing_count?: number; eligible_backlog_count?: number}>;
  items: OcrOperation[];
  history: Array<{job_id: string; order_id: string | null; source_status: string;
    created_at: string | null; updated_at: string | null; error: string | null}>;
  external_history?: {source: string; current: false; recorded_at: string | null;
    observation: Record<string, unknown>};
};
const labels: Record<string, string> = {
  idle: "現在のOCRジョブなし", pending: "待機中", queued: "待機中", running: "実行中",
  awaiting_output: "成果物待ち", recovering: "復旧中", failed: "失敗", stalled: "停止",
  blocked: "所有権・原本の確認が必要", review_required: "認識結果の確認が必要",
  succeeded: "認識完了", not_recognized: "認識未実行",
};
export const ocrOperationLabel = (value: string) => labels[value] || `状態未定義: ${value}`;
const date = (value?: string | null) => value ? value : "日時未記録";

export function OcrOperationsSummary({operations}: {operations?: OcrOperationsPayload | null}) {
  if (!operations || operations.source !== "recorded_sql_workflow/v1") {
    return <p role="alert">現行OCR状態を取得できません。</p>;
  }
  return <div aria-label="現行SQL OCR状態">
    <p><strong>{ocrOperationLabel(operations.summary.status)}</strong></p>
    <p>現在の参照: {operations.summary.total}件 / 観測: {date(operations.observed_at)}</p>
    <ul>{Object.entries(operations.summary.counts).map(([status, count]) =>
      <li key={status}>{ocrOperationLabel(status)}: {count}件</li>)}</ul>
    <div>{(["uploaded_pdfs", "ingest_jobs"] as const).map(key => <div key={key}>
      <h3>{key === "uploaded_pdfs" ? "アップロード受付（未帰属を含む）" : "取込受付（未帰属を含む）"}</h3>
      <p>待機: {operations.intake?.[key]?.pending_count ?? "未取得"} / 処理中: {operations.intake?.[key]?.processing_count ?? "未取得"}
        / 再試行対象: {operations.intake?.[key]?.eligible_backlog_count ?? "未取得"}</p>
    </div>)}</div>
  </div>;
}
export function OcrOperationsHistory({operations}: {operations?: OcrOperationsPayload | null}) {
  if (!operations) return null;
  return <details><summary>過去のOCR記録（現在の参照とは別）</summary>
    <ul>{operations.history.map(row => <li key={row.job_id}>
      {row.job_id} / 注文: {row.order_id || "未帰属"} / 記録状態: {row.source_status}
      <br />作成: {date(row.created_at)} / 更新: {date(row.updated_at)}
      {row.error ? <p>{row.error}</p> : null}
    </li>)}</ul>
    {operations.external_history ? <details><summary>旧外部パイプライン履歴 / {date(operations.external_history.recorded_at)}</summary>
      <pre style={{whiteSpace: "pre-wrap", overflowWrap: "anywhere"}}>{JSON.stringify(operations.external_history.observation, null, 2)}</pre>
    </details> : null}
  </details>;
}
export function OcrOperationsTable({items}: {items: OcrOperation[]}) {
  return <div style={{overflowX: "auto"}}><table>
    <thead><tr><th>注文 / 原本</th><th>ジョブ / 実行</th><th>状態</th><th>確認事項</th></tr></thead>
    <tbody>{items.map((item, index) => <tr key={`${item.order_id}:${item.job_id}:${index}`}>
      <td>{item.order_id ? <Link href={`/orders/${encodeURIComponent(item.order_id)}/workflow-v2`}>{item.order_id}</Link> : "未帰属"}
        <br />{item.document_id || "原本未確定"}</td>
      <td>{item.job_id || "ジョブ未記録"}<br />{item.kind || "所有権未確定"} / {item.execution_id || "実行未記録"}
        <br />世代: {item.generation ?? "未記録"}<br />{date(item.updated_at)}</td>
      <td>{ocrOperationLabel(item.status)}<br />記録状態: {item.source_status || "未記録"}</td>
      <td>{item.blockers.map(value => <p role="alert" key={value}>{value}</p>)}
        {item.job?.error_detail || item.job?.error_message || null}</td>
    </tr>)}</tbody>
  </table></div>;
}
