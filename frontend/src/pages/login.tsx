import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import PublicNav from "../components/PublicNav";
import { setBearerToken } from "../services/apiClient";
import { resolveLoginDestination } from "../services/loginDestination";
import { prepareSystemDestination } from "../services/systemNavigation";
import { watchBrowserLogout } from "../services/browserSession";

declare global {
  interface Window {
    google?: any;
  }
}

export default function LoginPage() {
  const envClientId = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID || "";
  const [clientId, setClientId] = useState(envClientId);
  const googleButtonRef = useRef<HTMLDivElement | null>(null);
  const [googleReady, setGoogleReady] = useState(false);
  const [message, setMessage] = useState("");
  const [configMessage, setConfigMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const submitting = useRef(false);
  const mounted = useRef(true);
  const attempt = useRef<AbortController | null>(null);

  useEffect(() => { mounted.current = true; return () => { mounted.current = false; attempt.current?.abort(); }; }, []);

  useEffect(() => {
    const portalUrl = (process.env.NEXT_PUBLIC_PORTAL_URL || "").replace(/\/$/, "");
    if (portalUrl && new URL(portalUrl).origin !== window.location.origin) {
      try {
        const login = new URL("/login", portalUrl);
        login.searchParams.set("next", resolveLoginDestination(window.location.search, window.sessionStorage.getItem("auth_next")));
        window.location.replace(login.toString());
      } catch { setMessage("復帰先が不正です。統合トップから開き直してください。"); }
    }
  }, []);

  const redirectAfterLogin = async (credential: string) => {
    if (submitting.current) return;
    submitting.current = true;
    setBusy(true);
    setMessage("");
    const controller = new AbortController();
    attempt.current = controller;
    const stopWatching = watchBrowserLogout(() => controller.abort());
    try {
      const next = resolveLoginDestination(window.location.search, window.sessionStorage.getItem("auth_next"));
      const destination = await prepareSystemDestination(next, `Bearer ${credential}`, controller.signal);
      if (!mounted.current || controller.signal.aborted) return;
      setBearerToken(credential);
      window.sessionStorage.removeItem("auth_next");
      // A full navigation runs the subsystem's server-side session guard.
      window.location.replace(destination);
    } catch (error) {
      if (mounted.current) setMessage(error instanceof Error ? error.message : "ログインを確認できませんでした。");
    } finally {
      stopWatching();
      attempt.current = null;
      submitting.current = false;
      if (mounted.current) setBusy(false);
    }
  };

  useEffect(() => {
    let active = true;
    const loadConfig = async () => {
      try {
        const res = await fetch("/api/auth/config", { cache: "no-store" });
        if (!res.ok) throw new Error("config fetch failed");
        const data = await res.json();
        const apiClientId = typeof data?.google_client_id === "string" ? data.google_client_id : "";
        if (active && apiClientId) {
          setClientId(apiClientId);
          setConfigMessage("");
        }
      } catch (err) {
        if (active && !envClientId) {
          setConfigMessage("ログイン設定の取得に失敗しました。");
        }
      }
    };
    loadConfig();
    return () => {
      active = false;
    };
  }, [envClientId]);

  useEffect(() => {
    if (!clientId) return;
    const initialize = () => {
      if (!window.google?.accounts?.id || !googleButtonRef.current) return;
      window.google.accounts.id.initialize({
        client_id: clientId,
        callback: (response: { credential?: string }) => {
          if (!response?.credential) {
            setMessage("Googleログインに失敗しました。");
            return;
          }
          void redirectAfterLogin(response.credential);
        },
      });
      window.google.accounts.id.renderButton(googleButtonRef.current, {
        theme: "outline",
        size: "large",
        shape: "pill",
        width: 320,
      });
      setGoogleReady(true);
    };
    if (window.google?.accounts?.id) {
      initialize();
      return;
    }
    const existing = document.querySelector('script[data-gis="true"]') as HTMLScriptElement | null;
    if (existing) {
      existing.addEventListener("load", initialize);
      return () => existing.removeEventListener("load", initialize);
    }
    const script = document.createElement("script");
    script.src = "https://accounts.google.com/gsi/client";
    script.async = true;
    script.defer = true;
    script.dataset.gis = "true";
    script.addEventListener("load", initialize);
    document.head.appendChild(script);
    return () => script.removeEventListener("load", initialize);
  }, [clientId]);

  return (
    <main className="page">
      <header className="hero">
        <div>
          <p className="eyebrow">Sign In</p>
          <h1>オペレーター認証</h1>
          <p className="subtle">
            Googleアカウントでログインしてください。管理者が登録した利用者のみ利用できます。
          </p>
          <p className="subtle links">
            <Link href="/about">ホームページ</Link>
            <Link href="/privacy">プライバシーポリシー</Link>
            <Link href="/terms">利用規約</Link>
          </p>
        </div>
        <PublicNav />
      </header>

      <section className="panel">
        <header className="panel-header">
          <h2>Googleログイン</h2>
        </header>
        {clientId ? (
          <>
            <div className="google-wrap">
              <div ref={googleButtonRef} />
            </div>
            {!googleReady && <p className="subtle">ボタンを読み込み中...</p>}
            {configMessage ? <p className="message">{configMessage}</p> : null}
          </>
        ) : (
          <p className="message">
            Google Client ID が未設定です。
            <br />
            <span className="subtle">NEXT_PUBLIC_GOOGLE_CLIENT_ID を設定してください。</span>
          </p>
        )}
      </section>

      {busy && <p role="status">利用権限を確認して、元の画面を開いています…</p>}
      {message && <p className="message" role="alert">{message}</p>}

      <style jsx>{`
        :global(body) {
          background: radial-gradient(circle at top left, #f8f4ea, #f4f7f6 40%, #eef1f0 100%);
          color: #1f2a2a;
          font-family: "Manrope", "Noto Sans JP", sans-serif;
        }

        :global(*) {
          box-sizing: border-box;
        }

        :global(a) {
          color: inherit;
          text-decoration: none;
        }

        .page {
          min-height: 100vh;
          padding: 48px 6vw 80px;
        }

        .hero {
          display: flex;
          flex-wrap: wrap;
          justify-content: space-between;
          gap: 24px;
          align-items: center;
          margin-bottom: 32px;
        }

        .eyebrow {
          letter-spacing: 0.12em;
          text-transform: uppercase;
          font-size: 12px;
          color: #5f7b74;
          margin-bottom: 8px;
        }

        h1 {
          font-size: clamp(26px, 4vw, 36px);
          margin: 0 0 12px;
        }

        .subtle {
          color: #51615c;
          margin: 0;
        }

        .links {
          display: flex;
          flex-wrap: wrap;
          gap: 12px;
          margin-top: 12px;
        }

        .links :global(a) {
          text-decoration: underline;
          text-underline-offset: 3px;
        }

        .nav {
          display: flex;
          gap: 12px;
        }

        .nav-link {
          padding: 10px 18px;
          border-radius: 999px;
          background: #1f2a2a;
          color: #f7f2e7;
          font-weight: 600;
          transition: transform 0.2s ease;
        }

        .nav-link:hover {
          transform: translateY(-2px);
        }

        .panel {
          background: #ffffff;
          border-radius: 18px;
          padding: 20px;
          border: 1px solid rgba(25, 32, 30, 0.08);
          box-shadow: 0 12px 26px rgba(27, 35, 33, 0.06);
          margin-bottom: 20px;
          max-width: 520px;
        }

        .panel-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 16px;
        }

        h2 {
          font-size: 18px;
          margin: 0;
        }

        .form-grid {
          display: grid;
          gap: 16px;
        }

        .google-wrap {
          display: flex;
          justify-content: center;
          padding: 8px 0 4px;
        }

        .field {
          display: flex;
          flex-direction: column;
          gap: 6px;
        }

        .field-label {
          color: #5f7b74;
          font-size: 12px;
          letter-spacing: 0.06em;
          text-transform: uppercase;
        }

        .input {
          border: 1px solid rgba(25, 32, 30, 0.14);
          border-radius: 10px;
          padding: 8px 10px;
          background: #fbfbf9;
        }

        .btn {
          border: none;
          border-radius: 999px;
          padding: 10px 18px;
          background: #e6ebe9;
          color: #1f2a2a;
          font-weight: 600;
          cursor: pointer;
          width: fit-content;
        }

        .btn.primary {
          background: #1f2a2a;
          color: #f7f2e7;
        }

        .message {
          margin-top: 12px;
          padding: 8px 12px;
          border-radius: 10px;
          background: #f0f4f2;
          font-size: 13px;
        }
      `}</style>
      <style jsx global>{`
        @import url("https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;700&family=Noto+Sans+JP:wght@400;600&display=swap");
      `}</style>
    </main>
  );
}
