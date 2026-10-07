import { useEffect, useRef } from "react";
import { useRouter } from "next/router";
import { browserSessionKey } from "../services/browserSession";
import { hasActiveSessionAuthHeader } from "../services/apiClient";

export function useH1EditorOwner(selection: number) {
  const router = useRouter();
  const state = useRef({ alive: false, selection, input: 0, operation: 0, route: router.asPath });
  const valuesRef = useRef<string | undefined>(undefined);
  state.current.selection = selection;
  state.current.route = router.asPath;
  useEffect(() => { state.current.alive = true; return () => { state.current.alive = false; }; }, []);
  useEffect(() => {
    const retire = () => { state.current.operation += 1; };
    router.events.on("routeChangeStart", retire);
    return () => router.events.off("routeChangeStart", retire);
  }, [router.events]);
  const edit = (values?: unknown) => {
    if (values !== undefined) {
      const recorded = JSON.stringify(values);
      if (recorded === valuesRef.current) return;
      valuesRef.current = recorded;
    } else valuesRef.current = undefined;
    state.current.input += 1;
  };
  const start = () => {
    const own = { ...state.current, operation: ++state.current.operation, auth: browserSessionKey() };
    const current = () => state.current.alive && hasActiveSessionAuthHeader() && own.auth === browserSessionKey()
      && own.selection === state.current.selection && own.input === state.current.input
      && own.operation === state.current.operation && own.route === state.current.route;
    const requireCurrent = () => { if (!current()) throw new Error("入力・選択・ログイン状態が変わったため操作結果を適用しませんでした。"); };
    return { current, requireCurrent };
  };
  const formLease = useRef<ReturnType<typeof start> | null>(null);
  const beginOperation = () => {
    const lease = start();
    formLease.current = lease;
    return Object.assign(lease.current, { canReport: lease.current });
  };
  const formOperation = () => {
    const lease = formLease.current;
    formLease.current = null;
    if (!lease) throw new Error("共有formの操作開始を確認できませんでした。");
    return lease;
  };
  return { edit, start, beginOperation, formOperation };
}
