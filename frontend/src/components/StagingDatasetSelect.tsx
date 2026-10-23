import { useEffect, useRef, useState } from "react";
import { DATASET_KEY, DATASET_GENERATION, documentDatasetMatches } from "../services/stagingDataset";

type Choice = { id: string; label: string; available: boolean; snapshot_at?: string };
type Refresh = { configured: boolean; blocked_reason?: string; job?: { id: string; phase: string; component?: string; error?: string; missing_at_source?: Record<string, number> } | null };
type State = { enabled: boolean; selected?: string; options?: Choice[]; error?: string | null };

function headers(): Record<string, string> {
  const authorization = window.sessionStorage.getItem("auth_header");
  const csrf = document.cookie.split(";").map(value => value.trim()).find(value => value.startsWith("__Host-sawa_csrf="));
  return { "Content-Type": "application/json", ...(authorization ? { Authorization: authorization } : {}),
    ...(csrf ? { "X-Sawa-CSRF": decodeURIComponent(csrf.slice("__Host-sawa_csrf=".length)) } : {}) };
}

export default function StagingDatasetSelect() {
  const [state, setState] = useState<State | null>(null);
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState<Refresh | null>(null);
  const [refreshBusy, setRefreshBusy] = useState(false);
  const [refreshError, setRefreshError] = useState("");
  const refreshId = useRef<string | null>(null);
  const refreshSubmitting = useRef(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let controller = new AbortController();
    const generation = window.localStorage.getItem(DATASET_GENERATION);
    const changed = () => {
      if (window.localStorage.getItem(DATASET_GENERATION) !== generation) window.location.reload();
    };
    window.addEventListener("storage", changed);
    window.addEventListener("focus", changed);
    const loadChoices = () => {
      controller.abort();
      controller = new AbortController();
      const requestController = controller;
      void fetch("/api/staging-datasets", { headers: headers(), credentials: "same-origin", cache: "no-store", signal: requestController.signal })
      .then(async response => {
        if (!response.ok) throw new Error("unavailable");
        const value: State = await response.json();
        if (requestController.signal.aborted) return;
        if (value.enabled && value.selected) {
          window.sessionStorage.setItem(DATASET_KEY, value.selected);
          if (value.options?.some(option => option.id === value.selected && option.available) && !documentDatasetMatches(value.selected)) {
            window.location.reload(); return;
          }
        }
        setError("");
        setState(value);
      }).catch(() => {
        if (!requestController.signal.aborted && window.location.hostname.includes("-stg")) setError("参照DBを確認できません。");
      });
    };
    window.addEventListener("sawa:auth-generation", loadChoices);
    loadChoices();
    return () => { controller.abort(); window.removeEventListener("storage", changed); window.removeEventListener("focus", changed); window.removeEventListener("sawa:auth-generation", loadChoices); };
  }, []);

  const loadRefresh = async (signal?: AbortSignal) => {
    const response = await fetch("/api/staging-datasets/refresh", { headers: headers(), credentials: "same-origin", cache: "no-store", signal });
    if (!response.ok) throw new Error("refresh_status_unavailable");
    const value: Refresh = await response.json();
    if (signal?.aborted) return;
    setRefresh(value);
    if (value.job && ["succeeded", "failed"].includes(value.job.phase) && value.job.id === refreshId.current) refreshId.current = null;
    setRefreshError("");
    if (value.job?.phase === "succeeded") {
      const choices = await fetch("/api/staging-datasets", { headers: headers(), credentials: "same-origin", cache: "no-store", signal });
      if (choices.ok && !signal?.aborted) setState(await choices.json());
    }
  };

  useEffect(() => {
    if (!state?.enabled) return;
    const controller = new AbortController();
    void loadRefresh(controller.signal).catch(() => {
      if (!controller.signal.aborted) setRefreshError("コピー更新の状態を取得できません。");
    });
    return () => controller.abort();
  }, [state?.enabled]);

  const refreshPhase = refresh?.job?.phase;
  useEffect(() => {
    if (!refreshPhase || ["succeeded", "failed"].includes(refreshPhase)) return;
    const controller = new AbortController();
    const timer = window.setInterval(() => {
      void loadRefresh(controller.signal).catch(() => {
        if (!controller.signal.aborted) setRefreshError("更新状況を取得できません。更新処理は継続している可能性があります。");
      });
    }, 5000);
    return () => { window.clearInterval(timer); controller.abort(); };
  }, [refreshPhase]);

  const startRefresh = async () => {
    if (!refresh?.configured || refreshSubmitting.current) return;
    refreshSubmitting.current = true;
    setRefreshBusy(true);
    setRefreshError("");
    if (!refreshId.current) refreshId.current = refresh?.job?.phase === "queued" ? refresh.job.id : window.crypto.randomUUID().replaceAll("-", "");
    try {
      const response = await fetch("/api/staging-datasets/refresh", { method: "POST", headers: headers(), credentials: "same-origin",
        body: JSON.stringify({ request_id: refreshId.current }) });
      if (!response.ok) throw new Error("refresh_not_started");
      const job = await response.json();
      setRefresh(previous => previous ? { ...previous, job } : previous);
      refreshId.current = null;
      await loadRefresh();
    } catch {
      setRefreshError("更新開始を確認できません。状態を再確認してください。現在のコピーとテストDBは維持しています。");
    } finally { refreshSubmitting.current = false; setRefreshBusy(false); }
  };

  const change = async (dataset: string) => {
    if (!state || dataset === state.selected || busy) return;
    setBusy(true);
    setError("");
    try {
      // All three boundaries must be ready before the shared browser cookie changes.
      const targets = ["/api/system/staging-dataset-ready", "/shift/api/backend/staging-dataset-ready", "/school-lunch/api/backend/staging-dataset-ready"];
      const schoolGeneration = window.sessionStorage.getItem("sawa_school_lunch_session_generation");
      // The school proxy requires its existing session bootstrap, even for readiness.
      if (!schoolGeneration) {
        const bootstrap = await fetch("/school-lunch/api/backend/shared-auth/me", { headers: headers(), credentials: "same-origin", cache: "no-store" });
        if (!bootstrap.ok) throw new Error("school_session");
        const session = await bootstrap.json();
        if (!session.session_generation) throw new Error("school_session");
        window.sessionStorage.setItem("sawa_school_lunch_session_generation", session.session_generation);
        window.localStorage.setItem("sawa_school_lunch_published_generation", session.session_generation);
      }
      await Promise.all(targets.map(async target => {
        const response = await fetch(target, { headers: { ...headers(), "X-Sawa-Dataset": dataset,
          "X-Sawa-Session-Generation": window.sessionStorage.getItem("sawa_school_lunch_session_generation") || "" }, credentials: "same-origin", cache: "no-store" });
        if (!response.ok) throw new Error("not_ready");
        const result = await response.json();
        if (result.dataset !== dataset || result.ready !== true) throw new Error("not_ready");
      }));
      const response = await fetch("/api/staging-datasets", { method: "PUT", headers: headers(), credentials: "same-origin", body: JSON.stringify({ dataset }) });
      if (!response.ok || (await response.json()).selected !== dataset) throw new Error("selection_failed");
      window.sessionStorage.setItem(DATASET_KEY, dataset);
      window.localStorage.setItem(DATASET_GENERATION, window.crypto.randomUUID());
      window.location.reload();
    } catch {
      setError("3システムの参照DBを切り替えられませんでした。現在の選択を維持しています。");
      setBusy(false);
    }
  };

  if (!state?.enabled) return error ? <span role="alert">{error}</span> : null;
  const selected = state.options?.find(option => option.id === state.selected);
  return <div aria-busy={busy} style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
    <label>stg参照DB <select aria-label="stg参照DB" value={state.selected} disabled={busy} onChange={event => void change(event.target.value)}>
      {!selected ? <option value={state.selected}>選択先を確認できません</option> : null}
      {state.options?.map(option => <option key={option.id} value={option.id} disabled={!option.available}>{option.label}</option>)}
    </select></label>
    {busy ? <span role="status">切替中…</span> : selected?.snapshot_at ? <small>コピー時点: {selected.snapshot_at}</small> : null}
    <details>
      <summary>本番コピーを更新</summary>
      <p>本番のDB・PDF・設定から新しいコピーを作成します。既存テストDBと以前のコピーは保持します。</p>
      {refresh && !refresh.configured ? <p role="status">本番の同期元が未設定です。DB・PDF・設定の接続設定後に更新できます。</p> : null}
      <button type="button" disabled={!refresh?.configured || refreshBusy || (!!refreshPhase && !["queued", "succeeded", "failed"].includes(refreshPhase))}
        onClick={() => void startRefresh()}>{refreshBusy ? "更新を開始中…" : refreshPhase === "queued" ? "更新を再送" : "本番データからコピーを更新"}</button>
      <button type="button" onClick={() => void loadRefresh().catch(() => setRefreshError("更新状況を取得できません。"))}>更新状況を再確認</button>
      {refresh?.job ? <p role="status">{({ queued: "開始待ち", starting: "更新を開始しています", copying_database: "DBをコピー中", copying_files_and_settings: "PDF・設定をコピー中", verifying_manifest: "コピーを検証中", succeeded: "更新完了。参照DBの最新コピーを選択できます。", failed: "更新失敗。現在のコピーを維持しています。" } as Record<string, string>)[refresh.job.phase] || "更新処理中"}
        {refresh.job.component ? `（${({ order: "病院注文", shift: "シフト", "school-lunch": "学校給食" } as Record<string, string>)[refresh.job.component] || refresh.job.component}）` : ""}</p> : null}
      {refresh?.job?.phase === "succeeded" && Object.values(refresh.job.missing_at_source || {}).some(count => count > 0) ? <p role="status">本番側ですでに見つからない原本: {Object.values(refresh.job.missing_at_source || {}).reduce((sum, count) => sum + count, 0)}件。原本がない状態もコピーに保持しています。</p> : null}
      {refreshError ? <p role="alert">{refreshError}</p> : null}
    </details>
    {error || state.error ? <span role="alert">{error || state.error}</span> : null}
  </div>;
}
