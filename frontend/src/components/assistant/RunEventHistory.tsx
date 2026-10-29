import { useEffect, useRef, useState } from "react";
import { apiClient } from "../../services/apiClient";

type Message = { id: string; role: string; text: string };
type Page = { conversation: Message[]; business_messages: Message[];
  previous_event: number | null; has_previous: boolean };

export default function RunEventHistory({ runId, admin }: { runId: string; admin: boolean }) {
  const [page, setPage] = useState<Page | null>(null), [back, setBack] = useState<(number | null)[]>([]);
  const [loading, setLoading] = useState(false), [error, setError] = useState("");
  const generation = useRef(0), cursor = useRef<number | null>(null);
  const identity = useRef(runId); identity.current = runId;
  useEffect(() => { ++generation.current; cursor.current = null; setPage(null); setBack([]); setError(""); setLoading(false);
    return () => { ++generation.current; }; }, [runId]);

  async function read(before: number | null, stack: (number | null)[]) {
    const owner = ++generation.current; setLoading(true); setError("");
    try {
      const { data } = await apiClient.get<Page>(`/assistant-trial/runs/${runId}/events`, {
        params: before === null ? {} : { before },
      });
      if (owner !== generation.current || identity.current !== runId) return;
      cursor.current = before; setBack(stack); setPage(data);
    } catch {
      if (owner === generation.current && identity.current === runId) setError("保存済み履歴を取得できませんでした。");
    } finally { if (owner === generation.current && identity.current === runId) setLoading(false); }
  }
  return <details className="conversation-history" onToggle={event => {
    if (event.currentTarget.open && !page && !loading) void read(null, []);
  }}>
    <summary>保存済みの全履歴</summary>
    {error && <p role="alert">{error}<button disabled={loading} onClick={() => void read(cursor.current, back)}>再読み込み</button></p>}
    {loading && <p role="status">履歴を取得しています。</p>}
    <ol>{(admin ? page?.conversation : page?.business_messages)?.map(message => <li key={message.id}>
      <strong>{message.role === "user" ? "あなた" : "エージェント"}</strong><p style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{message.text}</p>
    </li>)}</ol>
    <button disabled={loading || !back.length} onClick={() => void read(back[back.length - 1], back.slice(0, -1))}>新しい履歴</button>
    <button disabled={loading || !page?.has_previous || page.previous_event === null}
      onClick={() => { if (page?.previous_event) void read(page.previous_event, [...back, cursor.current]); }}>以前の履歴</button>
  </details>;
}
