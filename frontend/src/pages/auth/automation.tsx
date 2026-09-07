import { useState, type FormEvent } from "react";
import { clearAuth, setBearerToken } from "../../services/apiClient";

export default function AutomationLoginPage() {
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy || !token.trim()) return;
    setBusy(true);
    setError("");
    clearAuth();
    try {
      const response = await fetch("/api/portal/automation/auth", {
        method: "POST",
        headers: { Authorization: `Bearer ${token.trim()}` },
        cache: "no-store",
        redirect: "error",
      });
      if (!response.ok) throw new Error("Authentication rejected");
      const identity = await response.json();
      if (identity.role !== "operator" || !identity.systems?.includes("shift")) {
        throw new Error("Shift access required");
      }
      setBearerToken(token.trim());
      setToken("");
      window.location.replace("/shift");
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
          <span>認証トークン</span>
          <input type="password" autoComplete="off" value={token}
            onChange={(event) => setToken(event.target.value)} disabled={busy} required />
        </label>
        <button className="btn primary" type="submit" disabled={busy || !token.trim()}>
          {busy ? "認証中" : "ログイン"}
        </button>
        {error && <p role="alert">{error}</p>}
      </form>
    </main>
  );
}
