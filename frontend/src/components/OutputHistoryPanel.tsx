import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { useQuery } from "@tanstack/react-query";
import { DataTable, RequestState, SawaProvider } from "@sawa/ui";
import type { components } from "../generated/output-history-api";
import { apiClient, hasActiveSessionAuthHeader } from "../services/apiClient";
import { browserSessionKey, subscribeBrowserSession } from "../services/browserSession";

type HistoryWire = components["schemas"]["OutputHistoryResponse"];
type HistoryEntry = Omit<HistoryWire["entries"][number], "validation"> & {validation:"validated"|"blocked"|"not_loaded"};
type History = Omit<HistoryWire,"entries"> & {entries:HistoryEntry[];next_before_id?:string|null};
type Props = {
  orderId: string;
  documentId: string;
  workflowRevision: number | null;
  currentOutputBundleId: string | null;
  route: string;
  inputKey: unknown;
  captureOwner: () => () => boolean;
};

class HistoryReadError extends Error {
  constructor(message: string, readonly current: () => boolean) { super(message); }
}

const isIdentity = (value: unknown): value is string => typeof value === "string" && !!value.trim() && value === value.trim();
const timestampOffset = (value: string) => value.match(/^\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d+)?(Z|[+-](?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:\.\d+)?)?)$/)?.[1];

function assertHistory(data: History, props: Props, page: number, limit: number) {
  if (!data || !isIdentity(data.order_id) || data.order_id !== props.orderId || data.workflow_revision !== props.workflowRevision
    || data.page !== page + 1 || data.limit !== limit || !Number.isInteger(data.total) || data.total < 0
    || !isIdentity(data.current_output_bundle_id)
    || data.current_output_bundle_id !== props.currentOutputBundleId
    || !Array.isArray(data.entries) || data.entries.length > limit || data.entries.length > data.total) throw new Error("出力生成記録の対象が一致しません。");
  const seen = new Set<string>();
  for (const entry of data.entries) {
    if (!entry || !isIdentity(entry.output_bundle_id) || seen.has(entry.output_bundle_id)
      || !isIdentity(entry.order_id) || entry.order_id !== props.orderId || !isIdentity(entry.source_saved_sheet_id)
      || !isIdentity(entry.source_bagging_result_id)
      || (entry.template_version_id !== null && !isIdentity(entry.template_version_id))
      || (entry.created_at !== null && typeof entry.created_at !== "string")
      || typeof entry.current !== "boolean" || entry.current !== (entry.output_bundle_id === data.current_output_bundle_id)
      || !Array.isArray(entry.blocked_reasons) || entry.blocked_reasons.some(reason => typeof reason !== "string" || !reason)
      || !["validated", "blocked", "not_loaded"].includes(entry.validation)
      || (entry.validation === "blocked" ? entry.blocked_reasons.length === 0 : entry.blocked_reasons.length !== 0)
      || ((entry.created_at === null || !timestampOffset(entry.created_at))
        && (entry.validation !== "blocked" || !entry.blocked_reasons.includes("output_bundle_created_at_invalid")))) {
      throw new Error("出力生成記録の検証状態が不正です。");
    }
    seen.add(entry.output_bundle_id);
  }
}

function OutputHistoryContent(props: Props & { session: string }) {
  const [page, setPage] = useState(0);
  const [limit, setLimit] = useState(25);
  const pageCursors = useRef(new Map<number,string>());
  const [verifiedEntries,setVerifiedEntries] = useState<Record<string,HistoryEntry>>({});
  const [verifying,setVerifying] = useState<string|null>(null);
  const [verificationError,setVerificationError] = useState("");
  const mounted = useRef(true), generation = useRef(0);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; ++generation.current; };
  }, []);
  const enabled = isIdentity(props.orderId) && isIdentity(props.documentId) && isIdentity(props.currentOutputBundleId) && Number.isInteger(props.workflowRevision)
    && props.workflowRevision !== null && props.workflowRevision > 0
    && hasActiveSessionAuthHeader() && props.captureOwner()();
  const query = useQuery({
    queryKey: ["output-history", props.orderId, props.documentId, props.workflowRevision, props.currentOutputBundleId, props.route,
      props.inputKey, props.session, page, limit],
    enabled, retry: false, staleTime: 0, refetchOnWindowFocus: false,
    queryFn: async ({signal}) => {
      const own = props.captureOwner(), operation = ++generation.current;
      setVerifiedEntries({});
      setVerificationError("");
      const current = () => mounted.current && operation === generation.current && !signal.aborted
        && props.session === browserSessionKey() && hasActiveSessionAuthHeader() && own();
      try {
        if (!current()) throw new Error("出力生成記録の読み込みは取り消されました。");
        const response = await apiClient.get<History>("/outputs/history", {
          params: {order_id: props.orderId, expected_workflow_revision: props.workflowRevision, page: page + 1, limit,
            validate_records:false, before_id:pageCursors.current.get(page)},
          signal,
        });
        if (!current()) throw new Error("出力生成記録の読み込みは取り消されました。");
        assertHistory(response.data, props, page, limit);
        if(response.data.next_before_id)pageCursors.current.set(page+1,response.data.next_before_id);
        return {payload: response.data, current};
      } catch (reason: unknown) {
        const detail = (reason as {response?: {data?: {detail?: unknown}}})?.response?.data?.detail;
        const message = typeof detail === "string" ? `出力生成記録を確認できません: ${detail}`
          : reason instanceof Error ? reason.message : "出力生成記録を確認できません。";
        throw new HistoryReadError(message, current);
      }
    },
  });
  const data = enabled && query.data?.current() ? query.data.payload : null;
  const error = query.error instanceof HistoryReadError && query.error.current() ? query.error.message : undefined;
  if (!enabled) return <RequestState error="注文・PDF・保存済みworkflow revision・認証を確認してください。" />;
  if (!data) return <RequestState pending={query.isFetching} error={error} onRetry={() => void query.refetch()} />;
  if (data.page > Math.max(1, Math.ceil(data.total / data.limit))) {
    return <RequestState error="指定ページは現在の生成記録の範囲外です。先頭ページを読み直してください。" onRetry={() => setPage(0)} />;
  }
  const verifyEntry = async (entry:HistoryEntry) => {
    if(verifying)return;
    const own=props.captureOwner(),operation=generation.current;
    const current=()=>mounted.current&&operation===generation.current&&props.session===browserSessionKey()&&own();
    if(!current())return;
    setVerifying(entry.output_bundle_id);setVerificationError("");
    try {
      const response=await apiClient.get<History>("/outputs/history",{params:{order_id:props.orderId,
        expected_workflow_revision:props.workflowRevision,page:1,limit:1,record_id:entry.output_bundle_id,validate_records:true}});
      if(!current())return;
      assertHistory(response.data,props,0,1);
      const checked=response.data.entries[0];
      if(!checked||checked.output_bundle_id!==entry.output_bundle_id||checked.validation==="not_loaded"
        || checked.source_saved_sheet_id!==entry.source_saved_sheet_id||checked.source_bagging_result_id!==entry.source_bagging_result_id
        || checked.template_version_id!==entry.template_version_id||checked.created_at!==entry.created_at)throw new Error("出力生成記録の版が変更されました。");
      setVerifiedEntries(values=>({...values,[checked.output_bundle_id]:checked}));
    } catch(error) {if(current())setVerificationError(error instanceof Error?error.message:"出力記録の検証に失敗しました。");}
    finally {if(mounted.current)setVerifying(null);}
  };
  return <section aria-label="出力生成記録">
    <RequestState error={verificationError||undefined}/>
    <DataTable label="保存済み出力生成記録（ダウンロード完了記録ではありません）" rows={data.entries.map(entry=>verifiedEntries[entry.output_bundle_id]||entry)}
      columns={[
        {id: "created", label: "生成日時", render: entry => {
          const offset = entry.created_at ? timestampOffset(entry.created_at) : undefined;
          return <><span>{entry.created_at === null ? "日時不正（要確認）" : `${entry.created_at}（${offset === "Z" || offset === "+00:00" ? "UTC" : offset ? `UTC${offset}` : "時差未確定・要確認"}）`}</span>
            <details><summary>記録の詳細</summary><dl>
              <dt>出力ID</dt><dd>{entry.output_bundle_id}</dd>
              <dt>注文ID</dt><dd>{entry.order_id}</dd>
              <dt>保存済みシートID</dt><dd>{entry.source_saved_sheet_id}</dd>
              <dt>袋分け結果ID</dt><dd>{entry.source_bagging_result_id}</dd>
              <dt>施設版ID</dt><dd>{entry.template_version_id === null ? "未記録" : entry.template_version_id}</dd>
              {entry.blocked_reasons.length ? <><dt>確認事項</dt><dd>{entry.blocked_reasons.join(" / ")}</dd></> : null}
            </dl></details></>;
        }},
        {id: "current", label: "参照", render: entry => entry.current ? "現行" : "過去（参照のみ）"},
        {id: "validation", label: "検証", render: entry => entry.validation === "validated" ? "記録検証済み" : entry.validation === "blocked" ? "要確認" : <button type="button" className="btn" disabled={verifying!==null} onClick={()=>void verifyEntry(entry)}>{verifying===entry.output_bundle_id?"検証中...":"本文を検証"}</button>},
      ]}
      rowKey={entry => entry.output_bundle_id} total={data.total} page={data.page - 1} rowsPerPage={data.limit}
      onPageChange={value => {
        if (!Number.isInteger(value) || value < 0 || value >= Math.ceil(data.total / data.limit)) return;
        if (value === page + 1 && data.next_before_id) pageCursors.current.set(value, data.next_before_id);
        if (value === 0 || pageCursors.current.has(value)) setPage(value);
      }}
      onRowsPerPageChange={value => {if (Number.isInteger(value) && value >= 1 && value <= 100) {pageCursors.current.clear();setLimit(value); setPage(0);}}}
      pending={query.isFetching} error={error} onRetry={() => void query.refetch()} />
  </section>;
}

export default function OutputHistoryPanel(props: Props) {
  const session = useSyncExternalStore(subscribeBrowserSession, browserSessionKey, () => "server");
  const identity = JSON.stringify([props.orderId, props.documentId, props.workflowRevision, props.currentOutputBundleId, props.route, props.inputKey, session]);
  return <SawaProvider sessionKey={`hospital-output-history:${session}`}>
    <OutputHistoryContent key={identity} {...props} session={session} />
  </SawaProvider>;
}
