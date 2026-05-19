import Link from "next/link";

const version = "v2026.05.19-current-stg";
const updatedAt = "2026-05-19 08:05 JST";
const groups = [
  {
    "key": "order_form_creation",
    "title": "注文書作成",
    "description": "施設・週・パターンを選択して注文書を作成します。",
    "manuals": [
      {
        "kind": "detail",
        "label": "詳細版",
        "href": "/manuals/current-stg-20260519/order_form_creation_detail_current_stg_20260519_0805.pdf"
      },
      {
        "kind": "quick",
        "label": "簡易版",
        "href": "/manuals/current-stg-20260519/order_form_creation_quick_current_stg_20260519_0805.pdf"
      }
    ]
  },
  {
    "key": "daily_output",
    "title": "日別出力処理",
    "description": "発送日単位でラベル、納品書、一括Excelを出力します。",
    "manuals": [
      {
        "kind": "detail",
        "label": "詳細版",
        "href": "/manuals/current-stg-20260519/daily_output_detail_current_stg_20260519_0805.pdf"
      },
      {
        "kind": "quick",
        "label": "簡易版",
        "href": "/manuals/current-stg-20260519/daily_output_quick_current_stg_20260519_0805.pdf"
      }
    ]
  },
  {
    "key": "weekly_output",
    "title": "週別出力処理",
    "description": "指定日の週に含まれる注文から、週別重量表Excelを出力します。",
    "manuals": [
      {
        "kind": "detail",
        "label": "詳細版",
        "href": "/manuals/current-stg-20260519/weekly_output_detail_current_stg_20260519_0805.pdf"
      },
      {
        "kind": "quick",
        "label": "簡易版",
        "href": "/manuals/current-stg-20260519/weekly_output_quick_current_stg_20260519_0805.pdf"
      }
    ]
  },
  {
    "key": "monthly_menu",
    "title": "月次メニュー登録処理",
    "description": "月次メニューを開き、ファイル・登録範囲・項目を確認して登録します。",
    "manuals": [
      {
        "kind": "detail",
        "label": "詳細版",
        "href": "/manuals/current-stg-20260519/monthly_menu_detail_current_stg_20260519_0805.pdf"
      },
      {
        "kind": "quick",
        "label": "簡易版",
        "href": "/manuals/current-stg-20260519/monthly_menu_quick_current_stg_20260519_0805.pdf"
      }
    ]
  },
  {
    "key": "facility_management",
    "title": "施設一覧 修正・新規追加",
    "description": "施設一覧から施設を開き、基本情報やテンプレートを更新します。",
    "manuals": [
      {
        "kind": "detail",
        "label": "詳細版",
        "href": "/manuals/current-stg-20260519/facility_management_detail_current_stg_20260519_0805.pdf"
      },
      {
        "kind": "quick",
        "label": "簡易版",
        "href": "/manuals/current-stg-20260519/facility_management_quick_current_stg_20260519_0805.pdf"
      }
    ]
  },
  {
    "key": "shipping",
    "title": "送り状処理",
    "description": "送り状PDF、Excel補完、追跡照会、履歴確認を行います。",
    "manuals": [
      {
        "kind": "detail",
        "label": "詳細版",
        "href": "/manuals/current-stg-20260519/shipping_detail_current_stg_20260519_0805.pdf"
      },
      {
        "kind": "quick",
        "label": "簡易版",
        "href": "/manuals/current-stg-20260519/shipping_quick_current_stg_20260519_0805.pdf"
      }
    ]
  }
] as const;

export default function ManualsPage() {
  return (
    <main className="manuals-page">
      <section className="hero">
        <p className="eyebrow">MANUALS</p>
        <h1>マニュアルページ</h1>
        <p className="lead">現行stg liveのスクショだけで作成した最新版です。注文処理はworkflow-v2で再作成中のため一時的に外しています。</p>
        <div className="version">Version: {version} / Updated: {updatedAt}</div>
      </section>

      <section className="manual-grid">
        {groups.map((group) => (
          <article className="manual-card" key={group.key}>
            <div>
              <p className="card-label">{group.key}</p>
              <h2>{group.title}</h2>
              <p>{group.description}</p>
            </div>
            <div className="actions">
              {group.manuals.map((manual) => (
                <a key={manual.href} href={manual.href} target="_blank" rel="noreferrer" className={manual.kind === "detail" ? "btn primary" : "btn ghost"}>
                  {manual.label}
                </a>
              ))}
            </div>
          </article>
        ))}
      </section>

      <section className="note">
        <h2>運用メモ</h2>
        <p>すべてのPDFは現行stg liveで撮影したスクショだけを使用し、バージョンと更新日時で管理しています。</p>
        <Link href="/" className="back">ダッシュボードへ戻る</Link>
      </section>

      <style jsx>{`
        .manuals-page { min-height: 100vh; padding: 40px 5vw 64px; background: #f6f8f7; color: #17201d; }
        .hero { max-width: 1120px; margin: 0 auto 28px; }
        .eyebrow, .card-label { color: #66736f; font-size: 12px; letter-spacing: .08em; text-transform: uppercase; font-weight: 700; }
        h1 { font-size: 42px; margin: 8px 0 12px; }
        .lead { font-size: 18px; line-height: 1.8; max-width: 820px; }
        .version { display: inline-flex; padding: 8px 12px; border: 1px solid #d7dfdc; border-radius: 6px; background: #fff; font-size: 14px; }
        .manual-grid { max-width: 1120px; margin: 0 auto; display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 16px; }
        .manual-card { background: #fff; border: 1px solid #dce3e0; border-radius: 8px; padding: 20px; display: flex; flex-direction: column; gap: 18px; justify-content: space-between; min-height: 220px; }
        .manual-card h2 { font-size: 22px; margin: 6px 0 10px; }
        .manual-card p { line-height: 1.7; margin: 0; }
        .actions { display: flex; flex-wrap: wrap; gap: 10px; }
        .btn, .back { text-decoration: none; border-radius: 6px; padding: 10px 14px; font-weight: 700; border: 1px solid #18231f; }
        .btn.primary { background: #18231f; color: white; }
        .btn.ghost, .back { background: white; color: #18231f; }
        .note { max-width: 1120px; margin: 24px auto 0; background: #fff; border: 1px solid #dce3e0; border-radius: 8px; padding: 20px; }
        .note h2 { margin-top: 0; font-size: 20px; }
        @media (max-width: 720px) { .manuals-page { padding: 28px 16px 48px; } h1 { font-size: 34px; } }
      `}</style>
    </main>
  );
}
