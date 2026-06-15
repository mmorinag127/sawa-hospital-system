import Link from "next/link";
import TopNav from "../components/TopNav";

const version = "v2026.05.27-current-stg";
const updatedAt = "2026-05-27 16:10 JST";
const groups = [
  {
    "key": "order_processing",
    "title": "注文処理 workflow-v2",
    "description": "週次または注文一覧からworkflow-v2を開き、施設・週・OCR・シート編集・出力確認・確定まで進めます。",
    "manuals": [
      {
        "kind": "detail",
        "label": "詳細版",
        "href": "/manuals/current-stg-20260519/order_processing_detail_workflow_v2_current_stg_20260527_1610.pdf"
      },
      {
        "kind": "quick",
        "label": "簡易版",
        "href": "/manuals/current-stg-20260519/order_processing_quick_workflow_v2_current_stg_20260519_0920.pdf"
      }
    ]
  },
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
        "href": "/manuals/current-stg-20260519/daily_output_detail_current_stg_20260519_1045.pdf"
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
    "description": "施設一覧から施設を開き、基本情報、発注書ヘッダー、納品書ヘッダー、ラベル表示を更新します。",
    "manuals": [
      {
        "kind": "detail",
        "label": "更新版",
        "href": "/manuals/current-stg-20260519/facility_management_detail_current_stg_20260615.md"
      },
      {
        "kind": "detail",
        "label": "旧詳細版",
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
    <main className="page">
      <section className="hero">
        <div>
          <p className="eyebrow">Manual Library</p>
          <h1>マニュアル集</h1>
          <p className="subtle">現行stg liveで撮影した操作マニュアルを、詳細版と簡易版に分けて管理します。</p>
        </div>
        <TopNav />
      </section>

      <section className="panel">
        <header className="panel-header">
          <div>
            <h2>最新版</h2>
            <p className="subtle">Version: {version} / Updated: {updatedAt}</p>
          </div>
        </header>
        <div className="manual-list">
        {groups.map((group) => (
          <article className="manual-row" key={group.key}>
            <div>
              <p className="row-label">{group.key}</p>
              <h2>{group.title}</h2>
              <p>{group.description}</p>
            </div>
            <div className="actions">
              {group.manuals.map((manual) => (
                <a key={manual.href} href={manual.href} target="_blank" rel="noreferrer" className={manual.kind === "detail" ? "btn primary" : "btn"}>
                  {manual.label}
                </a>
              ))}
            </div>
          </article>
        ))}
        </div>
      </section>

      <section className="panel note">
        <header className="panel-header">
          <h2>運用メモ</h2>
          <Link href="/" className="ghost-link">ダッシュボードへ戻る</Link>
        </header>
        <p className="subtle">PDFはこのページに集約し、ダッシュボードにはPDF直リンクを置きません。</p>
      </section>

      <style jsx>{`
        :global(body) {
          background: radial-gradient(circle at top left, #f8f4ea, #f4f7f6 40%, #eef1f0 100%);
          color: #1f2a2a;
          font-family: "Manrope", "Noto Sans JP", sans-serif;
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

        .eyebrow,
        .row-label {
          letter-spacing: 0.12em;
          text-transform: uppercase;
          font-size: 12px;
          color: #5f7b74;
          margin: 0 0 8px;
          font-weight: 800;
        }

        h1 {
          font-size: clamp(26px, 4vw, 36px);
          margin: 0 0 12px;
        }

        h2 {
          font-size: 18px;
          margin: 0;
        }

        .subtle {
          color: #51615c;
          margin: 0;
        }

        .panel {
          background: #ffffff;
          border-radius: 18px;
          padding: 20px;
          border: 1px solid rgba(25, 32, 30, 0.08);
          box-shadow: 0 12px 26px rgba(27, 35, 33, 0.06);
          margin-bottom: 20px;
        }

        .panel-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          gap: 16px;
          margin-bottom: 16px;
        }

        .manual-list {
          display: grid;
          gap: 12px;
        }

        .manual-row {
          display: grid;
          grid-template-columns: minmax(0, 1fr) auto;
          gap: 16px;
          align-items: center;
          padding: 14px;
          border-radius: 14px;
          border: 1px solid rgba(25, 32, 30, 0.08);
          background: #fbfbf9;
        }

        .manual-row p {
          line-height: 1.7;
          margin: 6px 0 0;
        }

        .actions {
          display: flex;
          flex-wrap: wrap;
          gap: 10px;
          justify-content: flex-end;
        }

        .btn,
        .ghost-link {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          min-height: 38px;
          padding: 0 14px;
          border-radius: 999px;
          background: #e6ebe9;
          color: #1f2a2a;
          font-weight: 700;
          border: none;
        }

        .btn.primary {
          background: #1f2a2a;
          color: #f7f2e7;
        }

        .note {
          margin-bottom: 0;
        }

        @media (max-width: 720px) {
          .page {
            padding: 28px 16px 48px;
          }
          .manual-row {
            grid-template-columns: 1fr;
          }
          .actions {
            justify-content: flex-start;
          }
        }
      `}</style>
    </main>
  );
}
