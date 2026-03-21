import PublicNav from "../components/PublicNav";

export default function TermsPage() {
  return (
    <main className="page">
      <header className="hero">
        <div>
          <p className="eyebrow">Terms of Service</p>
          <h1>利用規約</h1>
          <p className="subtle">
            本システムは業務利用を目的として提供されます。利用者は認証済みの担当者に限られます。
          </p>
        </div>
        <PublicNav />
      </header>

      <section className="panel">
        <h2>利用者</h2>
        <p>管理者により許可された利用者のみが本システムを利用できます。</p>
        <h2>利用範囲</h2>
        <p>
          本システムは、病院・施設向け注文書の確認、OCR補助、袋分け、納品書・ラベル出力など、業務上必要な
          範囲でのみ利用できます。
        </p>
        <h2>禁止事項</h2>
        <p>不正アクセス、無関係な個人情報の投入、権限外データへのアクセス、業務外利用を禁止します。</p>
        <h2>データの正確性</h2>
        <p>OCRとLLM再解析は補助機能です。最終確定前に利用者が内容を確認してください。</p>
        <h2>アップロードデータ</h2>
        <p>
          利用者は、業務上必要なPDFのみをアップロードし、重複登録や誤登録が判明した場合は速やかに運用管理者
          に連絡してください。
        </p>
        <h2>アカウント管理</h2>
        <p>
          利用者は自身のGoogleアカウント認証情報を適切に管理し、第三者に利用させてはいけません。
        </p>
        <h2>変更</h2>
        <p>運用者は必要に応じて機能や規約を変更できます。</p>
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
