import { useEffect, useRef } from "react";
import { browserSessionKey } from "../services/browserSession";
import { hasActiveSessionAuthHeader } from "../services/apiClient";

export function useMenuInputOwner(target: string) {
  const live = useRef(true);
  const input = useRef(0);
  const operations = useRef<Record<string, number>>({});
  const currentTarget = useRef(target);
  currentTarget.current = target;
  useEffect(() => {
    live.current = true;
    return () => { live.current = false; };
  }, []);
  const changed = () => { input.current += 1; };
  const checkpoint = () => {
    const generation = input.current;
    const route = currentTarget.current;
    const auth = browserSessionKey();
    return () => live.current && hasActiveSessionAuthHeader() && browserSessionKey() === auth
      && currentTarget.current === route && input.current === generation;
  };
  const capture = (lane: string, bindInput = true) => {
    const generation = (operations.current[lane] || 0) + 1;
    operations.current[lane] = generation;
    const startInput = input.current;
    const startTarget = currentTarget.current;
    const auth = browserSessionKey();
    const active = () => live.current && hasActiveSessionAuthHeader() && auth === browserSessionKey()
      && startTarget === currentTarget.current && generation === operations.current[lane];
    return Object.assign(() => active() && (!bindInput || startInput === input.current), { active });
  };
  return { changed, capture, checkpoint };
}
