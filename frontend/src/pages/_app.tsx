import { useEffect } from "react";
import type { AppProps } from "next/app";
import { useRouter } from "next/router";
import { getStoredAuthHeader } from "../services/apiClient";

const PUBLIC_ROUTES = new Set(["/login", "/about", "/privacy", "/terms"]);

export default function App({ Component, pageProps }: AppProps) {
  const router = useRouter();
  const gitSha = process.env.NEXT_PUBLIC_GIT_SHA || "unknown";
  const deployedAt = process.env.NEXT_PUBLIC_DEPLOYED_AT || "unknown";
  const shortSha = gitSha === "unknown" ? gitSha : gitSha.slice(0, 12);

  useEffect(() => {
    if (!router.isReady) return;
    if (PUBLIC_ROUTES.has(router.pathname)) return;

    const header = getStoredAuthHeader();
    if (!header && typeof window !== "undefined") {
      const nextPath = router.asPath || "/";
      window.sessionStorage.setItem("auth_next", nextPath);
      window.location.href = "/login";
    }
  }, [router.isReady, router.pathname, router.asPath]);

  return (
    <div className="app-shell">
      <Component {...pageProps} />
      <footer className="deploy-footer" aria-label="deploy version">
        <span>version {shortSha}</span>
        <span>deployed {deployedAt}</span>
      </footer>
      <style jsx>{`
        .app-shell {
          min-height: 100vh;
          display: flex;
          flex-direction: column;
        }
        .deploy-footer {
          margin-top: auto;
          padding: 10px 16px;
          border-top: 1px solid #e5e7eb;
          color: #64748b;
          background: #f8fafc;
          font-size: 12px;
          line-height: 1.5;
          display: flex;
          gap: 12px;
          justify-content: flex-end;
          flex-wrap: wrap;
        }
      `}</style>
    </div>
  );
}
