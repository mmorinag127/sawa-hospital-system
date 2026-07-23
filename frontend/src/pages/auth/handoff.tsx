import { useEffect, useState } from "react";
import { setBearerToken } from "../../services/apiClient";

type AuthMessage = { type?: string; authorization?: string };

export default function AuthHandoffPage() {
  const [message, setMessage] = useState("統合ポータルのログイン情報を確認しています…");

  useEffect(() => {
    const portalUrl = (process.env.NEXT_PUBLIC_PORTAL_URL || "").replace(/\/$/, "");
    if (!portalUrl || !window.opener) {
      setMessage("統合ポータルから病院注文を開き直してください。");
      return;
    }
    const portalOrigin = new URL(portalUrl).origin;
    const receive = async (event: MessageEvent<AuthMessage>) => {
      if (event.origin !== portalOrigin || event.source !== window.opener) return;
      if (event.data?.type !== "sawa-portal-auth" || !event.data.authorization?.startsWith("Bearer ")) return;
      setBearerToken(event.data.authorization.slice("Bearer ".length));
      try {
        const response = await fetch("/api/auth/me", { headers: { Authorization: event.data.authorization }, cache: "no-store" });
        if (!response.ok) throw new Error(String(response.status));
        window.location.replace("/");
      } catch {
        setMessage("病院注文の利用権限を確認できませんでした。統合管理者に確認してください。");
      }
    };
    window.addEventListener("message", receive);
    window.opener.postMessage({ type: "sawa-app-ready" }, portalOrigin);
    return () => window.removeEventListener("message", receive);
  }, []);

  return <main className="page"><h1>病院注文を開いています</h1><p role="status">{message}</p></main>;
}
