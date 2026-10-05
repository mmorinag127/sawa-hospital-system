import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useRouter } from "next/router";

type Guard = { active: () => boolean; allow: (url: string) => boolean };
type Position = { token: string; index: number };
const key = "sawa_navigation";
const readPosition = (state: unknown): Position | null => {
  const value = (state as Record<string, Position> | null)?.[key];
  return typeof value?.token === "string" && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(value.token) &&
    Number.isSafeInteger(value.index) && value.index >= 0 ? value : null;
};
// Capture before Next initializes the router and replaces its own history state.
const documentEntry = typeof window === "undefined" ? null : readPosition(window.history.state);
const Navigation = createContext<{ allow: (url: string) => boolean; register: (guard: Guard) => () => void } | null>(null);

export function NavigationBoundary({ children }: { children: ReactNode }) {
  const router = useRouter();
  const ownerRouter = useRef(router).current;
  const ownerToken = useRef<string | null>(null);
  const guard = useRef<Guard | null>(null);
  const blocked = useRef(false);
  const restoring = useRef(false);
  const [error, setError] = useState("");
  const api = useMemo(() => {
    const clearWhenUnprotected = () => {
      if (!guard.current?.active()) { blocked.current = false; setError(""); }
    };
    return {
      allow: (url: string) => {
        clearWhenUnprotected();
        return !blocked.current && !restoring.current && (guard.current?.allow(url) ?? true);
      },
      register: (next: Guard) => {
        guard.current = next;
        clearWhenUnprotected();
        return () => {
          if (guard.current === next) { guard.current = null; clearWhenUnprotected(); }
        };
      },
    };
  }, []);

  useEffect(() => {
    const history = window.history;
    const push = history.pushState;
    const replace = history.replaceState;
    // A reload keeps the document's existing owned history; effect replay keeps the ref.
    let token = ownerToken.current ??= readPosition(history.state)?.token ?? documentEntry?.token ?? window.crypto.randomUUID();
    const position = (state: unknown): Position | null => {
      const value = readPosition(state);
      return value?.token === token ? value : null;
    };
    let current: Position | null = position(history.state) ??
      (documentEntry?.token === token ? documentEntry : { token, index: 0 });
    replace.call(history, { ...history.state, [key]: current }, "");
    history.pushState = function (state, unused, url) {
      // Unknown entries have no inferable distance to the prior owned segment.
      if (!current) token = ownerToken.current = window.crypto.randomUUID();
      const next = { token, index: current ? current.index + 1 : 0 };
      push.call(this, { ...state, [key]: next }, unused, url);
      current = next;
    };
    history.replaceState = function (state, unused, url) {
      replace.call(this, current ? { ...state, [key]: current } : state, unused, url);
    };
    ownerRouter.beforePopState(({ as }) => {
      const target = position(history.state);
      if (restoring.current && current && target?.index === current.index) {
        restoring.current = false;
        return false;
      }
      if (!guard.current?.active()) {
        blocked.current = false;
        restoring.current = false;
        setError("");
        current = target;
        return true;
      }
      if (!target || !current) {
        // There is no reliable delta outside the entries this boundary owns.
        current = null;
        blocked.current = true;
        setError("履歴の位置を確認できません。編集内容を保持して移動を停止しました。");
        return false;
      }
      if (blocked.current || restoring.current) return false;
      if (!guard.current.allow(as)) {
        const delta = current.index - target.index;
        if (delta === 0) {
          blocked.current = true;
          setError("履歴の位置を確認できません。編集内容を保持して移動を停止しました。");
        } else {
          restoring.current = true;
          history.go(delta);
        }
        return false;
      }
      current = target;
      return true;
    });
    return () => {
      history.pushState = push;
      history.replaceState = replace;
      ownerRouter.beforePopState(() => true);
    };
  }, [ownerRouter]);

  return <Navigation.Provider value={api}>{error && <p role="alert">{error}</p>}{children}</Navigation.Provider>;
}

export function useNavigationBoundary() {
  const boundary = useContext(Navigation);
  if (!boundary) throw new Error("NavigationBoundary is required");
  return boundary;
}
