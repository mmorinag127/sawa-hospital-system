import { useCallback, useEffect, useRef, useState } from "react";
import { apiClient } from "../../services/apiClient";
import type { BusinessProgress } from "../../components/assistant/BusinessProgress";

export type AssistantRunSummary = {
  id: string; instruction: string; domains: string[]; status: string; spent: number;
  provider?: string; business?: BusinessProgress;
};
type Cursor = { before_created: string; before_id: string };
type Page = { items: AssistantRunSummary[]; next: Cursor | null };

export default function useAssistantHistory() {
  const [page, setPage] = useState<Page>({ items: [], next: null });
  const [loading, setLoading] = useState(false);
  const [unfinished, setUnfinished] = useState(false);
  const [back, setBack] = useState<(Cursor | null)[]>([]);
  const cursor = useRef<Cursor | null>(null), scope = useRef(false), generation = useRef(0);
  const active = useRef(true);
  useEffect(() => { active.current = true; return () => { active.current = false; ++generation.current; }; }, []);

  const read = useCallback(async (nextCursor: Cursor | null, stack: (Cursor | null)[], filter: boolean) => {
    const owner = ++generation.current, previousScope = scope.current; setLoading(true); scope.current = filter;
    try {
      const { data } = await apiClient.get<Page>("/assistant-trial/run-history", {
        params: { ...nextCursor, unfinished: filter },
      });
      if (!active.current || owner !== generation.current) return;
      cursor.current = nextCursor; setBack(stack); setUnfinished(filter); setPage(data);
    } catch (error) {
      if (active.current && owner === generation.current) scope.current = previousScope;
      throw error;
    } finally { if (active.current && owner === generation.current) setLoading(false); }
  }, []);
  const refresh = useCallback((reset = false) => read(reset ? null : cursor.current,
    reset ? [] : back, scope.current), [read, back]);
  const filter = useCallback((value: boolean) => read(null, [], value), [read]);
  const next = useCallback(() => page.next ? read(page.next, [...back, cursor.current], scope.current)
    : Promise.resolve(), [page.next, back, read]);
  const previous = useCallback(() => back.length ? read(back[back.length - 1], back.slice(0, -1), scope.current)
    : Promise.resolve(), [back, read]);
  const patch = useCallback((run: AssistantRunSummary) => setPage(current => ({ ...current,
    items: current.items.map(item => item.id === run.id ? { ...item, ...run } : item),
  })), []);
  return { items: page.items, hasNext: !!page.next, hasPrevious: !!back.length,
    loading, unfinished, refresh, filter, next, previous, patch };
}
