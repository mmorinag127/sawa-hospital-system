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
      <header><p className="eyebrow">Sawa Integrated Management</p><h1>システム選択</h1><p>利用するシステムを選択してください。</p></header>
      <section className="systems" aria-label="利用可能なシステム">
        {systems.includes("hospital") ? <Link href="/hospital"><strong>病院注文</strong><span>注文・施設・帳票・発送を管理</span></Link> : null}
        {systems.includes("shift") ? <Link href="/shift"><strong>シフト管理</strong><span>人員・制約・シフト生成を管理</span></Link> : null}
        {systems.includes("school-lunch") ? <Link href="/school-lunch"><strong>学校給食</strong><span>準備中</span></Link> : null}
      </section>
      {role === "admin" ? <Link className="admin-link" href="/admin/users">共通ユーザー管理</Link> : null}
      <style jsx>{`main{max-width:1080px;margin:auto;padding:56px 24px}h1{font-size:36px}.systems{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:20px;margin:32px 0}.systems :global(a){display:grid;gap:10px;min-height:150px;padding:26px;border:1px solid #d9e2df;border-radius:14px;background:#fff;text-decoration:none;box-shadow:0 8px 24px rgba(23,32,31,.06)}.systems strong{font-size:24px}.systems span{color:#60716d}.admin-link{font-weight:700}`}</style>
    </main>
  );
}
