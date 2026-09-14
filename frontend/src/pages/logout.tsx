import { useEffect } from "react";
import type { GetServerSideProps } from "next";
import { broadcastLogout } from "../services/browserSession";

// Clear sibling HttpOnly cookies at their exact paths on the shared origin.
// This does not depend on the school-lunch backend being available.
export const getServerSideProps: GetServerSideProps = async ({ res }) => {
  res.setHeader("Cache-Control", "no-store, max-age=0");
  res.setHeader("Set-Cookie", [
    "sawa_school_lunch_auth=; Path=/school-lunch; HttpOnly; Secure; SameSite=Strict; Max-Age=0",
    "auth_header=; Path=/; Max-Age=0; SameSite=Lax",
  ]);
  return { props: {} };
};

export default function LogoutPage() {
  useEffect(() => {
    broadcastLogout();
    window.google?.accounts?.id?.disableAutoSelect?.();
    window.location.replace("/login");
  }, []);
  return <p role="status">ログアウト処理中です。</p>;
}
