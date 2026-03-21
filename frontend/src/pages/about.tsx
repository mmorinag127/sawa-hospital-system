import Link from "next/link";
import PublicNav from "../components/PublicNav";

export default function AboutPage() {
  return (
    <main className="page">
      <header className="hero">
        <div>
          <p className="eyebrow">Sawa Order OCR</p>
          <h1>病院・施設向け注文書OCRシステム</h1>
          <p className="subtle">
            Googleログインで認証し、PDFを直接アップロードしてOCR・確認・確定まで進める業務システムです。
          </p>
        </div>
        <PublicNav />
      </header>

      <section className="panel">
        <h2>主な機能</h2>
        <ul>
          <li>注文書PDFの直接アップロード</li>
          <li>OCRとLLM再解析による数量シート生成</li>
          <li>注文確認、袋分け、納品書、ラベル出力</li>
          <li>日別出力と総量確認</li>
        </ul>
      </section>

      <section className="panel">
        <h2>認証と受付方法</h2>
        <p>
          本システムでは、利用者の識別と権限管理のためにGoogleログインを利用します。注文書の受付は
          認証後に注文書PDFを直接アップロードする方式です。
        </p>
        <p>
          取り込まれたPDFは、OCR、確認シート、袋分け、納品書・ラベル出力の業務処理にのみ利用されます。
        </p>
      </section>

      <section className="panel">
        <h2>利用対象</h2>
        <p>
          本システムは、病院・高齢者施設向けの食事注文書処理を行う業務担当者向けに提供されます。認証済み
          の担当者のみが利用できます。
        </p>
      </section>

      <section className="panel">
        <h2>公開情報</h2>
        <p>
          利用には認証が必要です。<Link href="/login">ログインページ</Link>からGoogleアカウントで
          ログインしてください。データの取り扱いは<Link href="/privacy">プライバシーポリシー</Link>、
          利用条件は<Link href="/terms">利用規約</Link>を参照してください。
        </p>
      </section>

      <style jsx>{`
        :global(body) {
          background: radial-gradient(circle at top left, #f8f4ea, #f4f7f6 40%, #eef1f0 100%);
          color: #1f2a2a;
          font-family: "Manrope", "Noto Sans JP", sans-serif;
        }
        .page {
          min-height: 100vh;
          padding: 48px 6vw 80px;
        }
        .hero {
          display: flex;
          justify-content: space-between;
          align-items: flex-start;
          gap: 24px;
          flex-wrap: wrap;
          margin-bottom: 28px;
        }
        .eyebrow {
          letter-spacing: 0.12em;
          text-transform: uppercase;
          font-size: 12px;
          color: #5f7b74;
          margin-bottom: 8px;
        }
        h1 {
          font-size: clamp(28px, 4vw, 42px);
          margin: 0 0 12px;
        }
        h2 {
          margin-top: 0;
        }
        .subtle {
          color: #51615c;
          max-width: 760px;
        }
        .panel {
          background: #fff;
          border-radius: 18px;
          padding: 24px;
          border: 1px solid rgba(25, 32, 30, 0.08);
          box-shadow: 0 12px 26px rgba(27, 35, 33, 0.06);
          margin-bottom: 20px;
          max-width: 920px;
        }
        ul {
          margin: 0;
          padding-left: 20px;
        }
      `}</style>
    </main>
  );
}
