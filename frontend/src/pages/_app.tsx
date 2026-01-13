import { useEffect } from "react";
import type { AppProps } from "next/app";
import { useRouter } from "next/router";
import { getStoredAuthHeader } from "../services/apiClient";

export default function App({ Component, pageProps }: AppProps) {
  const router = useRouter();

  useEffect(() => {
    if (!router.isReady) return;
    if (router.pathname === "/login") return;

    const header = getStoredAuthHeader();
    if (!header && typeof window !== "undefined") {
      const nextPath = router.asPath || "/";
      window.sessionStorage.setItem("auth_next", nextPath);
      window.location.href = "/login";
    }
  }, [router.isReady, router.pathname, router.asPath]);

  return <Component {...pageProps} />;
}
