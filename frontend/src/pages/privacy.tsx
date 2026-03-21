import PublicNav from "../components/PublicNav";

export default function PrivacyPage() {
  return (
    <main className="page">
      <header className="hero">
        <div>
          <p className="eyebrow">Privacy Policy</p>
          <h1>プライバシーポリシー</h1>
          <p className="subtle">
            注文書PDF、OCR結果、確認シート、出力帳票に含まれる業務データの取り扱い方針です。
          </p>
        </div>
        <PublicNav />
      </header>

      <section className="panel">
        <h2>取得する情報</h2>
        <p>
          本システムでは、アップロードされたPDF、OCR処理結果、修正シート、出力帳票、操作ログ、
          認証に必要なGoogleアカウント識別情報を扱います。
        </p>
        <h2>利用目的</h2>
        <p>
          注文書のOCR、確認、袋分け、納品書・ラベル出力、監査ログ、障害対応、運用保守のために利用します。
        </p>
        <h2>Googleログインについて</h2>
        <p>
          Googleログインは利用者の認証と権限管理のために利用します。認証後は、注文書PDFのアップロード、
          OCR確認、出力帳票の確認などの業務機能を提供します。
        </p>
        <h2>保管</h2>
        <p>
          業務運用と監査に必要な範囲で保存し、不要になったデータは定められた保持期間に従って削除します。
          PDF、OCR結果、修正履歴は、確認・追跡・再処理のために一定期間保持される場合があります。
        </p>
        <h2>第三者提供</h2>
        <p>
          法令に基づく場合を除き、業務運用に必要な範囲を超えて第三者へ提供しません。クラウドインフラや
          OCR/LLM処理に必要な委託先環境上でデータを処理する場合があります。
        </p>
        <h2>安全管理</h2>
        <p>
          アクセス制御、監査ログ、権限管理、保存先の分離など、業務データ保護に必要な安全管理措置を講じます。
        </p>
        <h2>問い合わせ</h2>
        <p>運用管理者へ問い合わせてください。</p>
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
        h2 {
          margin-bottom: 8px;
        }
      `}</style>
    </main>
  );
}
