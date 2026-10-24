import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useRouter } from "next/router";

type Guard = { active: () => boolean; allow: (url: string) => boolean };
type Position = { token: string; index: number };
const key = "sawa_navigation";
const readPosition = (state: unknown): Position | null => {
  const entry = state as Record<string, unknown> | null;
  const value = entry?.[key] as Position | undefined;
  return Boolean(entry?.__N) && !entry?.__NA && typeof value?.token === "string" && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(value.token) &&
    Number.isSafeInteger(value.index) && value.index >= 0 ? value : null;
};
// Capture before Next initializes the router and replaces its own history state.
const documentEntry = typeof window === "undefined" ? null : readPosition(window.history.state);
let documentInitialized = false;
const documentOwners = new Set<string>();
const retireDocumentEntry = () => { documentInitialized = true; };
if (typeof window !== "undefined") window.addEventListener("popstate", retireDocumentEntry, { capture: true, once: true });
const Navigation = createContext<{ allow: (url: string) => boolean; register: (guard: Guard) => () => void } | null>(null);

export function NavigationBoundary({ children }: { children: ReactNode }) {
  const router = useRouter();
  const liveRouter = useRef(router);
  liveRouter.current = router;
  const ownerToken = useRef<string | null>(null);
  const guard = useRef<Guard | null>(null);
  const blocked = useRef(false);
  const restoring = useRef(false);
  const ownsCurrent = useRef<() => boolean>(() => false);
  const [error, setError] = useState("");
  const api = useMemo(() => {
    const clearWhenUnprotected = () => {
      if (!guard.current?.active()) { blocked.current = false; setError(""); }
    };
    return {
      allow: (url: string) => {
        clearWhenUnprotected();
        if (guard.current?.active() && !ownsCurrent.current()) {
          blocked.current = true;
          restoring.current = false;
          setError("履歴の位置を確認できません。編集内容を保持して移動を停止しました。");
          return false;
        }
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
    window.removeEventListener("popstate", retireDocumentEntry, true);
    const initialDocument = !documentInitialized;
    documentInitialized = true;
    const present = readPosition(history.state);
    // Reload authority belongs to initial document setup, never a later remount.
    let token = ownerToken.current ??= (present && (initialDocument || documentOwners.has(present.token)) ? present.token : null)
      ?? (initialDocument ? documentEntry?.token : null) ?? window.crypto.randomUUID();
    const position = (state: unknown): Position | null => {
      const value = readPosition(state);
      return value?.token === token ? value : null;
    };
    let current: Position | null = position(history.state) ??
      (initialDocument ? (documentEntry?.token === token ? documentEntry : { token, index: 0 }) : null);
    let popDecision: { state: unknown; allowed: boolean } | null = null;
    ownsCurrent.current = () => current !== null && position(history.state)?.index === current.index;
    if (current) {
      documentOwners.add(token);
      replace.call(history, { ...history.state, [key]: current }, "");
    }
    history.pushState = function (state, unused, url) {
      // Unknown entries have no inferable distance to the prior owned segment.
      const entry = position(history.state);
      if (!entry || entry.index !== current?.index) current = null;
      if (!current) token = ownerToken.current = window.crypto.randomUUID();
      documentOwners.add(token);
      const next = { token, index: current ? current.index + 1 : 0 };
      push.call(this, { ...state, [key]: next }, unused, url);
      current = next;
    };
    history.replaceState = function (state, unused, url) {
      const entry = position(history.state);
      if (!entry || entry.index !== current?.index) current = null;
      replace.call(this, current ? { ...state, [key]: current } : state, unused, url);
    };
    const unknown = () => {
      current = null;
      restoring.current = false;
      blocked.current = true;
      setError("履歴の位置を確認できません。編集内容を保持して移動を停止しました。");
    };
    const beforePop = ({ as }: { as: string }) => {
      const target = position(history.state);
      if (restoring.current && current && target?.index === current.index) {
        restoring.current = false;
        return false;
      }
      if (restoring.current && guard.current?.active()) { unknown(); return false; }
      if (!guard.current?.active()) {
        blocked.current = false;
        restoring.current = false;
        setError("");
        current = target;
        return true;
      }
      if (!target || !current) {
        // There is no reliable delta outside the entries this boundary owns.
        unknown();
        return false;
      }
      if (blocked.current || restoring.current) return false;
      if (!guard.current.allow(as)) {
        const delta = current.index - target.index;
        if (delta === 0) unknown();
        else {
          restoring.current = true;
          history.go(delta);
        }
        return false;
      }
      current = target;
      return true;
    };
    const decidePop = (state: { as: string }) => {
      if (popDecision?.state === state) return popDecision.allowed;
      const decision = { state, allowed: beforePop(state) };
      popDecision = decision;
      // Both window listeners may see the same event, in either registration order.
      setTimeout(() => { if (popDecision === decision) popDecision = null; }, 0);
      return decision.allowed;
    };
    // Next normalizes null/reloads __NA before beforePopState, and can skip it
    // for same-URL SSR entries. Observe the original entry before those branches.
    const capturePop = (event: PopStateEvent) => {
      const state = event.state;
      if (popDecision?.state === state) {
        if (!popDecision.allowed) event.stopImmediatePropagation();
        return;
      }
      const target = state?.__N && !state.__NA ? position(state) : null;
      if (!target) {
        current = null;
        restoring.current = false;
        if (guard.current?.active()) {
          event.stopImmediatePropagation();
          unknown();
        } else {
          blocked.current = false;
          setError("");
        }
        return;
      }
      // Cancel before Next updates its key/scroll bookkeeping for the rejected entry.
      if (restoring.current || guard.current?.active()) {
        if (!decidePop(state)) event.stopImmediatePropagation();
      } else if (!guard.current?.active()) {
        // Clean traversal must retain Next's events, scroll restoration and key updates.
        current = target;
        blocked.current = false;
        setError("");
      }
    };
    liveRouter.current.beforePopState(decidePop);
    window.addEventListener("popstate", capturePop, true);
    return () => {
      ownsCurrent.current = () => false;
      window.removeEventListener("popstate", capturePop, true);
      history.pushState = push;
      history.replaceState = replace;
      liveRouter.current.beforePopState(() => true);
    };
  }, [router.events]);

  return <Navigation.Provider value={api}>{error && <p role="alert">{error}</p>}{children}</Navigation.Provider>;
}

export function useNavigationBoundary() {
  const boundary = useContext(Navigation);
  if (!boundary) throw new Error("NavigationBoundary is required");
  return boundary;
}
