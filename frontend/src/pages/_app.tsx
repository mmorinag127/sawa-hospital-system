import { useEffect } from "react";
import type { AppProps } from "next/app";
import { useRouter } from "next/router";
import { getStoredAuthHeader } from "../services/apiClient";
import PageTemplate from "../components/PageTemplate";
import UnifiedShell from "../components/UnifiedShell";
import "../styles/sawa-template.css";

const PUBLIC_ROUTES = new Set(["/login", "/auth/handoff", "/about", "/privacy", "/terms"]);
const portalUrl = (process.env.NEXT_PUBLIC_PORTAL_URL || "").replace(/\/$/, "");

export default function App({ Component, pageProps }: AppProps) {
  const router = useRouter();
  const gitSha = process.env.NEXT_PUBLIC_GIT_SHA || "unknown";
  const deployedAt = process.env.NEXT_PUBLIC_DEPLOYED_AT || "unknown";
  const publicPage = PUBLIC_ROUTES.has(router.pathname);

  useEffect(() => {
    if (!router.isReady) return;
    if (PUBLIC_ROUTES.has(router.pathname)) return;

    const header = getStoredAuthHeader();
    if (!header && typeof window !== "undefined") {
      const nextPath = router.asPath || "/";
      window.sessionStorage.setItem("auth_next", nextPath);
      window.location.href = portalUrl || "/login";
    }
  }, [router.isReady, router.pathname, router.asPath]);

  return (
    <UnifiedShell gitSha={gitSha} deployedAt={deployedAt} publicPage={publicPage}>
      <PageTemplate publicPage={publicPage}>
        <Component {...pageProps} />
      </PageTemplate>
    </UnifiedShell>
  );
}
