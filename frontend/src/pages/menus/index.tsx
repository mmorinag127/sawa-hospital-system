import { useRouter } from "next/router";
import { useEffect, useState } from "react";

import TopNav from "../../components/TopNav";
import { apiClient } from "../../services/apiClient";

export default function MonthlyMenuIndexPage() {
  const router = useRouter();
  const [message, setMessage] = useState("最新の月次メニューを確認中です。");

  useEffect(() => {
    let cancelled = false;

    const loadLatest = async () => {
      try {
        const res = await apiClient.get("/monthly-menus/latest");
        const monthId = String(res.data?.menu?.id || "").trim();
        if (!monthId) {
          throw new Error("missing_month_id");
        }
        if (!cancelled) {
          router.replace(`/menus/${monthId}`);
        }
      } catch (err: any) {
        if (cancelled) return;
        const status = err?.response?.status;
        if (status === 403) {
          setMessage("権限がありません。月次メニューの操作にはユーザー2以上の権限が必要です。");
        } else if (status === 404) {
          setMessage("月次メニューがまだ登録されていません。");
        } else {
          setMessage("月次メニューの読込先を解決できませんでした。");
        }
      }
    };

    loadLatest();
    return () => {
      cancelled = true;
    };
  }, [router]);

  return (
    <main className="page">
      <header className="hero">
        <div>
          <p className="eyebrow">Monthly Menu</p>
          <h1>月次メニュー</h1>
          <p className="subtle">{message}</p>
        </div>
        <TopNav />
      </header>

      <section className="panel">
        <p className="subtle">{message}</p>
      </section>

      <style jsx>{`
        :global(body) {
          background: radial-gradient(circle at top left, #f8f4ea, #f4f7f6 40%, #eef1f0 100%);
          color: #1f2a2a;
          font-family: -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Yu Gothic", "Meiryo", sans-serif;
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

        .panel {
          background: #ffffff;
          border-radius: 18px;
          padding: 24px;
          border: 1px solid rgba(25, 32, 30, 0.08);
          box-shadow: 0 12px 26px rgba(27, 35, 33, 0.06);
        }
      `}</style>
    </main>
  );
}
