import Link from "next/link";
import { useCurrentUserRole } from "../../hooks/useCurrentUserRole";

const hospitalTools = [
  ["/hospital/system-status", "システム管理"],
  ["/hospital/system-process-logs", "処理ログ"],
  ["/hospital/ocr-queue", "OCRキュー"],
  ["/hospital/ocr-results", "OCR結果監視"],
  ["/hospital/ocr-training-data", "OCR学習データ"],
] as const;

export default function AdministrationPage() {
  const { isAdmin, loading } = useCurrentUserRole();
  return (
    <main className="sawa-page admin-home">
      <Link className="sawa-page__back" href="/">← システム選択へ戻る</Link>
      <h1 className="sawa-page__title">管理画面</h1>
      {loading ? <p role="status">管理者権限を確認しています。</p> : !isAdmin ? (
        <p role="alert">管理者アカウントでログインしてください。</p>
      ) : (
        <>
          <nav aria-label="共通管理">
            <h2>共通管理</h2>
            <ul>
              <li><Link href="/admin/users">共通ユーザー管理</Link></li>
              {process.env.NEXT_PUBLIC_SAWA_ASSISTANT_TRIAL_ENABLED === "true" && (
                <li><Link href="/admin/assistant">エージェント管理（利用料金・予算・依頼）</Link></li>
              )}
            </ul>
          </nav>
          <nav aria-label="病院注文の監視・保守">
            <h2>病院注文の監視・保守</h2>
            <ul>{hospitalTools.map(([href, label]) => <li key={href}><Link href={href}>{label}</Link></li>)}</ul>
          </nav>
        </>
      )}
      <style jsx>{`
        .admin-home nav { margin-block: 24px; }
        .admin-home nav h2 { font-size: 18px; margin: 0 0 8px; }
        .admin-home ul { list-style: none; padding: 0; margin: 0; border-top: 1px solid var(--sawa-border); }
        .admin-home li { border-bottom: 1px solid var(--sawa-border); }
        .admin-home li :global(a) { display: block; padding: 12px 8px; font-weight: 600; }
        .admin-home li :global(a:hover) { background: var(--sawa-accent-soft); }
      `}</style>
    </main>
  );
}
