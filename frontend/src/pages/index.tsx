import { useEffect, useState } from "react";
import Link from "next/link";
import { useCurrentUserRole } from "../hooks/useCurrentUserRole";
import { apiClient } from "../services/apiClient";

export default function SystemSelectorPage() {
  const { role } = useCurrentUserRole();
  const [systems, setSystems] = useState<string[]>([]);

  useEffect(() => {
    apiClient
      .get<{ systems?: string[] }>("/portal/auth/me")
      .then((response) => setSystems(response.data.systems || []))
      .catch(() => setSystems([]));
  }, []);

  return (
    <main className="portal-page">
      <header><p className="eyebrow">統合管理ポータル</p><h1>システム選択</h1><p>利用するシステムを選択してください。</p></header>
      <section className="systems" aria-label="利用可能なシステム">
        {systems.includes("hospital") ? <Link href="/hospital"><strong>病院注文</strong><span>注文・施設・帳票・発送を管理</span></Link> : null}
        {systems.includes("shift") ? <Link href="/shift"><strong>シフト管理</strong><span>人員・制約・シフト生成を管理</span></Link> : null}
        {systems.includes("school-lunch") ? <Link href="/school-lunch"><strong>学校給食</strong><span>準備中</span></Link> : null}
        {role === "admin" ? <Link href="/admin/users"><strong>管理画面</strong><span>共通ユーザーと利用できるシステムを管理</span></Link> : null}
      </section>
      <style jsx>{`main{max-width:1080px;margin:auto;padding:48px 24px}header{max-width:680px}.eyebrow{margin:0 0 6px;color:var(--sawa-accent);font-size:12px;font-weight:900;letter-spacing:.08em}h1{margin:0;font-size:36px}header p:last-child{color:var(--sawa-muted)}.systems{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:20px;margin:32px 0}.systems :global(a){display:grid;gap:10px;min-height:150px;padding:26px;border:1px solid var(--sawa-border);border-radius:14px;background:var(--sawa-panel);text-decoration:none;box-shadow:0 8px 24px rgba(23,32,31,.06)}.systems :global(a:hover){border-color:#79b7a8;background:var(--sawa-accent-soft)}.systems strong{font-size:24px}.systems span{color:var(--sawa-muted)}`}</style>
    </main>
  );
}
