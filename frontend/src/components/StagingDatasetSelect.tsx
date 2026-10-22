import { useEffect, useState } from "react";
import { DATASET_KEY, DATASET_GENERATION, documentDatasetMatches } from "../services/stagingDataset";

type Choice = { id: string; label: string; available: boolean; snapshot_at?: string };
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
          if (!documentDatasetMatches(value.selected)) { window.location.reload(); return; }
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
    {error || state.error ? <span role="alert">{error || state.error}</span> : null}
  </div>;
}
