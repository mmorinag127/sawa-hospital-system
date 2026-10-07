import { Button, Input, NativeSelect } from "@mui/material";
import { useState, type FormEvent } from "react";
import { clearAuth, setBearerToken } from "../../services/apiClient";
import { publishSchoolLunchSessionGeneration } from "../../services/browserSession";

export default function AutomationLoginPage() {
  const [token, setToken] = useState("");
  const [system, setSystem] = useState("shift");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy || !token.trim()) return;
    setBusy(true);
    setError("");
    clearAuth();
    try {
      const response = await fetch(`/api/portal/automation/auth?system=${encodeURIComponent(system)}`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token.trim()}` },
        cache: "no-store",
        redirect: "error",
      });
      if (!response.ok) throw new Error("Authentication rejected");
      const identity = await response.json();
      if (identity.role !== "operator" || identity.systems?.length !== 1 || identity.systems[0] !== system) {
        throw new Error("System access required");
      }
      if (system === "school-lunch") {
        const session = await fetch("/school-lunch/api/backend/shared-auth/me", {
          method: "GET",
          headers: { Authorization: `Bearer ${token.trim()}` },
          credentials: "same-origin",
          cache: "no-store",
          redirect: "error",
        });
        if (!session.ok) throw new Error("School lunch session rejected");
        const issued = await session.json() as { session_generation?: unknown };
        publishSchoolLunchSessionGeneration(issued.session_generation);
      }
      setBearerToken(token.trim());
      setToken("");
      window.location.replace(system === "school-lunch" ? "/school-lunch/implementation-price-tables" : "/shift");
    } catch {
      setToken("");
      setError("認証できませんでした。");
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="page">
      <h1>自動検証用ログイン</h1>
      <form className="form-grid" onSubmit={submit} autoComplete="off">
        <label className="field">
          <span>対象システム</span>
          <NativeSelect disableUnderline className="hospital-automation-control" value={system} onChange={(event) => setSystem(event.target.value)} disabled={busy}>
            <option value="shift">シフト</option>
            <option value="school-lunch">学校給食</option>
          </NativeSelect>
        </label>
        <label className="field">
          <span>認証トークン</span>
          <Input disableUnderline className="hospital-automation-control" type="password" inputProps={{ autoComplete: "off" }} value={token}
            onChange={(event) => setToken(event.target.value)} disabled={busy} required />
        </label>
        <Button className="btn primary hospital-automation-control" type="submit" disabled={busy || !token.trim()}>
          {busy ? "認証中" : "ログイン"}
        </Button>
        {error && <p role="alert">{error}</p>}
      </form><style jsx>{`:global(.hospital-automation-control.MuiButton-root){all:revert;font:inherit;outline-color:var(--sawa-accent)}:global(.hospital-automation-control.MuiInput-root){all:revert;display:inline-block;font:inherit}:global(.hospital-automation-control.MuiInput-root input),:global(.hospital-automation-control.MuiInput-root select){all:revert;font:inherit;outline-color:var(--sawa-accent)}:global(.hospital-automation-control.MuiInput-root .MuiNativeSelect-icon){display:none}`}</style>
    </main>
  );
}
