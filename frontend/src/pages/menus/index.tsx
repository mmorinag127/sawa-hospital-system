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
    </main>
  );
}
