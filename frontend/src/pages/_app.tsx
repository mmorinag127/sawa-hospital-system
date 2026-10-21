import { useEffect, useState } from "react";
import type { AppProps } from "next/app";
import { useRouter } from "next/router";
import { getStoredAuthHeader, establishServerSession } from "../services/apiClient";
import PageTemplate from "../components/PageTemplate";
import UnifiedShell from "../components/UnifiedShell";
import { NavigationBoundary } from "../components/NavigationBoundary";
import { watchBrowserLogout } from "../services/browserSession";
import { loginUrlFor } from "../services/loginDestination";
import "../styles/sawa-template.css";
import "../../public/assistant-workflow.css";

const PUBLIC_ROUTES = new Set(["/login", "/logout", "/auth/session", "/auth/automation", "/auth/handoff", "/about", "/privacy", "/terms"]);
const portalUrl = (process.env.NEXT_PUBLIC_PORTAL_URL || "").replace(/\/$/, "");

export default function App({ Component, pageProps }: AppProps) {
  const router = useRouter();
  const gitSha = process.env.NEXT_PUBLIC_GIT_SHA || "unknown";
  const deployedAt = process.env.NEXT_PUBLIC_DEPLOYED_AT || "unknown";
  const publicPage = PUBLIC_ROUTES.has(router.pathname);
  const [verifiedPath, setVerifiedPath] = useState<string | null>(null);
  const [authError, setAuthError] = useState("");

  useEffect(() => watchBrowserLogout(() => window.location.replace("/login")), []);

  useEffect(() => {
    if (!router.isReady) return;
    if (PUBLIC_ROUTES.has(router.pathname)) return;

    setAuthError("");
    const controller = new AbortController();
    if (getStoredAuthHeader()) {
      setVerifiedPath(router.asPath);
    } else {
      void establishServerSession(controller.signal).then(() => {
        if (!controller.signal.aborted) setVerifiedPath(router.asPath);
      }).catch((error) => {
        if (controller.signal.aborted) return;
        if (error.status !== 401) {
          setAuthError(error.status === 403 ? "利用権限を確認できませんでした。" : "認証サービスを確認できませんでした。再読み込みして再試行してください。");
          return;
        }
        const nextPath = router.asPath || "/";
        window.sessionStorage.setItem("auth_next", nextPath);
        window.location.href = portalUrl ? new URL(loginUrlFor(nextPath), portalUrl).toString() : loginUrlFor(nextPath);
      });
    }
    return () => controller.abort();
  }, [router.isReady, router.pathname, router.asPath]);

  return (
    <NavigationBoundary><UnifiedShell gitSha={gitSha} deployedAt={deployedAt} publicPage={publicPage}>
      <PageTemplate publicPage={publicPage}>
        {publicPage || (router.isReady && verifiedPath === router.asPath)
          ? <Component {...pageProps} />
          : <p role={authError ? "alert" : "status"}>{authError || "ログインを確認しています。"}</p>}
      </PageTemplate>
    </UnifiedShell></NavigationBoundary>
  );
}
