import { useEffect, useRef, useState } from "react";
import Link from "next/link";

import TopNav from "../components/TopNav";
import { getStoredAuthHeader } from "../services/apiClient";

const SHIFT_WEB_URL = (process.env.NEXT_PUBLIC_SHIFT_WEB_URL || "").replace(/\/$/, "");

type ShiftReadyMessage = {
  type?: string;
};

export default function ShiftLaunchPage() {
  const popupRef = useRef<Window | null>(null);
  const [message, setMessage] = useState("");

  useEffect(() => {
    const receive = (event: MessageEvent<ShiftReadyMessage>) => {
      if (!SHIFT_WEB_URL || event.origin !== new URL(SHIFT_WEB_URL).origin) return;
      if (event.source !== popupRef.current || event.data?.type !== "sawa-shift-ready") return;

      const authorization = getStoredAuthHeader();
      if (!authorization.startsWith("Bearer ")) {
        popupRef.current?.close();
        popupRef.current = null;
        setMessage("シフト管理はGoogleログインで利用してください。いったんログアウトし、Googleでログインし直してください。");
        return;
      }

      popupRef.current?.postMessage(
        { type: "sawa-shift-auth", authorization },
        new URL(SHIFT_WEB_URL).origin,
      );
      setMessage("シフト管理へ認証情報を引き継ぎました。");
    };
    window.addEventListener("message", receive);
    return () => window.removeEventListener("message", receive);
  }, []);

  const openShift = () => {
    if (!SHIFT_WEB_URL) {
      setMessage("シフト管理の接続先が設定されていません。管理者へ連絡してください。");
      return;
    }
    if (!getStoredAuthHeader().startsWith("Bearer ")) {
      setMessage("シフト管理はGoogleログインで利用してください。いったんログアウトし、Googleでログインし直してください。");
      return;
    }
    popupRef.current = window.open(`${SHIFT_WEB_URL}/auth/handoff`, "sawa-shift");
    if (!popupRef.current) {
      setMessage("新しい画面を開けませんでした。ブラウザのポップアップを許可して、もう一度お試しください。");
      return;
    }
    setMessage("シフト管理を開いています…");
  };

  return (
    <main className="page">
      <header className="hero">
        <div>
          <p className="eyebrow">Common applications</p>
          <h1>シフト管理</h1>
          <p className="subtle">病院システムでログイン中のGoogleアカウントを使って、シフト管理を開きます。</p>
        </div>
      </header>
      <TopNav />
      <section className="panel">
        <h2>シフト管理を開く</h2>
        <p>ユーザー登録・停止・権限は、この病院システムの「ユーザー管理」で一元管理されます。</p>
        <button type="button" className="btn primary" onClick={openShift}>シフト管理を開く</button>
        {message ? <p className="message" role="status">{message}</p> : null}
        <p><Link href="/">ダッシュボードへ戻る</Link></p>
      </section>
    </main>
  );
}
