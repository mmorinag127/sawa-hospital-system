import { useEffect, useState } from "react";
import type { GetServerSideProps } from "next";
import { broadcastLogout } from "../services/browserSession";
import { portalCsrfHeaders } from "../services/apiClient";

// Clear sibling HttpOnly cookies at their exact paths on the shared origin.
// This does not depend on the school-lunch backend being available.
export const getServerSideProps: GetServerSideProps = async ({ res }) => {
  res.setHeader("Cache-Control", "no-store, max-age=0");
  res.setHeader("Set-Cookie", [
    "sawa_school_lunch_auth=; Path=/school-lunch; HttpOnly; Secure; SameSite=Strict; Max-Age=0",
    "sawa_school_lunch_session_generation=; Path=/school-lunch; HttpOnly; Secure; SameSite=Strict; Max-Age=0",
    "auth_header=; Path=/; Max-Age=0; SameSite=Lax",
  ]);
  return { props: {} };
};

export default function LogoutPage() {
  const [message, setMessage] = useState("ログアウト処理中です。");
  useEffect(() => {
    void fetch("/api/portal/auth/logout", { method: "POST", credentials: "same-origin", headers: portalCsrfHeaders(), redirect: "error" })
      .then((response) => {
        if (!response.ok) throw new Error("logout failed");
        broadcastLogout();
        window.google?.accounts?.id?.disableAutoSelect?.();
        window.location.replace("/login");
      }).catch(() => setMessage("ログアウトを確認できませんでした。再読み込みして再試行してください。"));
  }, []);
  return <p role="status">{message}</p>;
}
