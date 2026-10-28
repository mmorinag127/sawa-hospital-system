import { useEffect, useRef, useState } from "react";
import NextApp, { type AppContext, type AppProps } from "next/app";
import { datasetFromCookie, initializeDocumentDataset, type DocumentDataset } from "../services/stagingDataset";
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

type DeploymentInfo = { gitSha: string; deployedAt: string };

export default function App({ Component, pageProps, initialDataset, initialDeployment }: AppProps & {
  initialDataset?: DocumentDataset;
  initialDeployment?: DeploymentInfo;
}) {
  initializeDocumentDataset(initialDataset);
  const router = useRouter();
  const destination = useRef(router.asPath);
  destination.current = router.asPath;
  const deployment = useRef(initialDeployment || {gitSha: "unknown", deployedAt: "unknown"});
  if (initialDeployment) deployment.current = initialDeployment;
  const {gitSha, deployedAt} = deployment.current;
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
      setVerifiedPath(router.pathname);
    } else {
      void establishServerSession(controller.signal).then(() => {
        if (!controller.signal.aborted) setVerifiedPath(router.pathname);
      }).catch((error) => {
        if (controller.signal.aborted) return;
        if (error.status !== 401) {
          setAuthError(error.status === 403 ? "利用権限を確認できませんでした。" : "認証サービスを確認できませんでした。再読み込みして再試行してください。");
          return;
        }
        const nextPath = destination.current || "/";
        window.sessionStorage.setItem("auth_next", nextPath);
        window.location.href = portalUrl ? new URL(loginUrlFor(nextPath), portalUrl).toString() : loginUrlFor(nextPath);
      });
    }
    return () => controller.abort();
  }, [router.isReady, router.pathname]);

  return (
    <NavigationBoundary><UnifiedShell gitSha={gitSha} deployedAt={deployedAt} publicPage={publicPage}>
      <PageTemplate publicPage={publicPage}>
        {publicPage || (router.isReady && verifiedPath === router.pathname)
          ? <Component {...pageProps} />
          : <p role={authError ? "alert" : "status"}>{authError || "ログインを確認しています。"}</p>}
      </PageTemplate>
    </UnifiedShell></NavigationBoundary>
  );
}

App.getInitialProps = async (context: AppContext) => {
  const props = await NextApp.getInitialProps(context);
  if (!context.ctx.req) return props;
  context.ctx.res?.setHeader("Cache-Control", "private, no-store");
  return { ...props, initialDataset: datasetFromCookie(context.ctx.req.headers.cookie),
    initialDeployment: {
      gitSha: process.env.SAWA_GIT_SHA || process.env.NEXT_PUBLIC_GIT_SHA || "unknown",
      deployedAt: process.env.SAWA_DEPLOYED_AT || process.env.NEXT_PUBLIC_DEPLOYED_AT || "unknown",
    },
  };
};
