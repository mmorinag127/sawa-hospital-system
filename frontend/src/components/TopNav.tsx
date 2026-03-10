import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/router";
import { apiClient } from "../services/apiClient";

type NavItem = {
  href: string;
  label: string;
  isActive: (path: string) => boolean;
};

type NavGroup = {
  id: string;
  label: string;
  description: string;
  items: NavItem[];
};

const buildMonthId = () => {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
};

const normalizePath = (path: string) => path.split("?")[0]?.split("#")[0] ?? path;

export default function TopNav() {
  const router = useRouter();
  const currentPath = normalizePath(router.asPath || "/");
  const [gmailWatchStatus, setGmailWatchStatus] = useState<string | null>(null);
  const [gmailWatchUpdatedAt, setGmailWatchUpdatedAt] = useState<string | null>(null);
  const [gmailWatchExpiration, setGmailWatchExpiration] = useState<string | null>(null);
  const [gmailWatchError, setGmailWatchError] = useState<string | null>(null);
  const [gmailConfig, setGmailConfig] = useState<{
    client_id_set: boolean;
    client_secret_set: boolean;
    refresh_token_set: boolean;
    configured: boolean;
  } | null>(null);
  const [oauthConfig, setOauthConfig] = useState<{ configured: boolean } | null>(null);
  const [ocrPipeline, setOcrPipeline] = useState<{
    status?: string | null;
    configured?: boolean;
    last_error?: string | null;
    last_error_at?: string | null;
  } | null>(null);
  const [ingestSummary, setIngestSummary] = useState<{
    total: number;
    counts: Record<string, number>;
  } | null>(null);

  useEffect(() => {
    let isMounted = true;
    apiClient
      .get("/system/status")
      .then((res) => {
        if (!isMounted) return;
        const status = res.data?.gmail_watch?.status ?? null;
        setGmailWatchStatus(status);
        setGmailWatchUpdatedAt(res.data?.gmail_watch?.updated_at ?? null);
        setGmailWatchExpiration(res.data?.gmail_watch?.expiration_iso ?? null);
        setGmailWatchError(res.data?.gmail_watch?.error_code ?? null);
        setGmailConfig(res.data?.gmail_config ?? null);
        setOauthConfig(res.data?.oauth_config ?? null);
        setIngestSummary(res.data?.ingest_jobs ?? null);
        setOcrPipeline(res.data?.ocr_pipeline ?? null);
      })
      .catch(() => {
        if (!isMounted) return;
        setGmailWatchStatus(null);
      });
    return () => {
      isMounted = false;
    };
  }, []);
  const navGroups: NavGroup[] = [
    {
      id: "operator-daily",
      label: "ユーザー1",
      description: "日々の処理と注文確認",
      items: [
        {
          href: "/",
          label: "ダッシュボード",
          isActive: (path) => path === "/",
        },
        {
          href: "/orders",
          label: "注文一覧",
          isActive: (path) => path.startsWith("/orders"),
        },
        {
          href: "/facility-orders",
          label: "施設別注文",
          isActive: (path) => path.startsWith("/facility-orders"),
        },
        {
          href: "/daily-delivery-notes",
          label: "日別出力",
          isActive: (path) => path.startsWith("/daily-delivery-notes"),
        },
        {
          href: "/ocr-results",
          label: "OCR結果",
          isActive: (path) => path.startsWith("/ocr-results"),
        },
        {
          href: "/totals",
          label: "総量",
          isActive: (path) => path.startsWith("/totals"),
        },
        {
          href: "/shipping",
          label: "送り状",
          isActive: (path) => path === "/shipping",
        },
        {
          href: "/shipping-history",
          label: "送り状履歴",
          isActive: (path) => path.startsWith("/shipping-history"),
        },
      ],
    },
    {
      id: "operator-setup",
      label: "ユーザー2",
      description: "注文作成と施設登録",
      items: [
        {
          href: `/menus/${buildMonthId()}`,
          label: "月次メニュー",
          isActive: (path) => path.startsWith("/menus"),
        },
        {
          href: "/base-menus",
          label: "基準メニュー",
          isActive: (path) => path.startsWith("/base-menus"),
        },
        {
          href: "/menu-masters",
          label: "メニューマスター",
          isActive: (path) => path.startsWith("/menu-masters"),
        },
        {
          href: "/menu-rules",
          label: "メニュールール",
          isActive: (path) => path.startsWith("/menu-rules"),
        },
        {
          href: "/order-forms",
          label: "注文書生成",
          isActive: (path) => path.startsWith("/order-forms"),
        },
        {
          href: "/weekly-orders",
          label: "週次注文",
          isActive: (path) => path.startsWith("/weekly-orders"),
        },
        {
          href: "/facility-master",
          label: "施設一覧",
          isActive: (path) => path === "/facility-master",
        },
      ],
    },
    {
      id: "admin",
      label: "管理者",
      description: "システムと保守",
      items: [
        {
          href: "/system-status",
          label: "システム管理",
          isActive: (path) => path.startsWith("/system-status"),
        },
        {
          href: "/users",
          label: "ユーザー管理",
          isActive: (path) => path.startsWith("/users"),
        },
        {
          href: "/ocr-queue",
          label: "OCRキュー",
          isActive: (path) => path.startsWith("/ocr-queue"),
        },
        {
          href: "/ocr-training-data",
          label: "OCR学習データ",
          isActive: (path) => path.startsWith("/ocr-training-data"),
        },
      ],
    },
  ];

  const showGmailWarning =
    (gmailWatchStatus && gmailWatchStatus !== "ok") ||
    (gmailConfig && !gmailConfig.configured) ||
    (oauthConfig && !oauthConfig.configured);
  const showPipelineWarning =
    ocrPipeline &&
    (!ocrPipeline.configured ||
      (ocrPipeline.status && ocrPipeline.status !== "ok" && ocrPipeline.status !== "running"));

  const ingestCounts = ingestSummary?.counts ?? {};
  const ingestTotal = ingestSummary?.total ?? 0;

  return (
    <div className="top-nav-wrap">
      {showGmailWarning ? (
        <div className="system-warning">
          <div className="system-warning__title">
            システム警告（FAX PDFの自動取込が停止する可能性があります）
          </div>
          <div className="system-warning__meta">
            Gmail watch: {gmailWatchStatus ?? "不明"}
            {gmailWatchError ? ` / エラー: ${gmailWatchError}` : ""}
          </div>
          <div className="system-warning__meta">
            最終更新: {gmailWatchUpdatedAt ?? "不明"} / 有効期限:{" "}
            {gmailWatchExpiration ?? "不明"}
          </div>
          <div className="system-warning__meta">
            Gmail設定:{" "}
            {gmailConfig?.configured
              ? "OK"
              : `不足 (client_id=${gmailConfig?.client_id_set ? "OK" : "NG"}, secret=${
                  gmailConfig?.client_secret_set ? "OK" : "NG"
                }, refresh=${gmailConfig?.refresh_token_set ? "OK" : "NG"})`}
          </div>
          <div className="system-warning__meta">
            Web OAuth設定: {oauthConfig?.configured ? "OK" : "不足"}
          </div>
        </div>
      ) : null}
      {showPipelineWarning ? (
        <div className="system-warning pipeline-warning">
          <div className="system-warning__title">
            OCRパイプライン警告（OCR処理が停止する可能性があります）
          </div>
          <div className="system-warning__meta">
            状態: {ocrPipeline?.configured ? ocrPipeline?.status ?? "不明" : "不足"}
          </div>
          {ocrPipeline?.last_error ? (
            <div className="system-warning__meta">
              最終エラー: {ocrPipeline.last_error}
            </div>
          ) : null}
        </div>
      ) : null}
      {ingestSummary ? (
        <div className="system-ingest">
          <div className="system-ingest__title">取り込み状況</div>
          <div className="system-ingest__meta">総件数: {ingestTotal}</div>
          <div className="system-ingest__grid">
            <div className="system-ingest__pill">
              pending: {ingestCounts.pending ?? 0}
            </div>
            <div className="system-ingest__pill">
              processing: {ingestCounts.processing ?? 0}
            </div>
            <div className="system-ingest__pill">
              error: {ingestCounts.error ?? 0}
            </div>
            <div className="system-ingest__pill">
              done: {ingestCounts.done ?? 0}
            </div>
          </div>
        </div>
      ) : null}
      <div className="top-nav-groups">
        {navGroups.map((group) => (
          <div key={group.id} className="top-nav-group">
            <div className="top-nav-group__meta">
              <div className="top-nav-group__label">{group.label}</div>
              <div className="top-nav-group__description">{group.description}</div>
            </div>
            <nav className="top-nav">
              {group.items.map((item) => {
                const active = item.isActive(currentPath);
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    className={`top-link${active ? " active" : ""}`}
                    aria-current={active ? "page" : undefined}
                  >
                    {item.label}
                  </Link>
                );
              })}
            </nav>
          </div>
        ))}
      </div>
      <style jsx>{`
        .top-nav-wrap {
          display: flex;
          flex-direction: column;
          gap: 12px;
          align-items: stretch;
          width: 100%;
        }

        .system-warning {
          width: 100%;
          border-radius: 16px;
          background: #fff0e6;
          border: 1px solid rgba(191, 86, 0, 0.2);
          padding: 12px 16px;
          color: #6a2e00;
          box-shadow: 0 10px 24px rgba(120, 60, 0, 0.08);
        }

        .system-warning__title {
          font-weight: 700;
          font-size: 14px;
          margin-bottom: 6px;
        }

        .system-warning__meta {
          font-size: 12px;
          opacity: 0.9;
        }

        .system-warning__steps {
          margin-top: 8px;
          font-size: 12px;
          display: flex;
          flex-direction: column;
          gap: 2px;
        }

        .system-ingest {
          width: 100%;
          border-radius: 16px;
          background: #eef4ff;
          border: 1px solid rgba(69, 102, 167, 0.2);
          padding: 12px 16px;
          color: #1f355a;
          box-shadow: 0 10px 24px rgba(40, 70, 140, 0.08);
        }

        .system-ingest__title {
          font-weight: 700;
          font-size: 14px;
          margin-bottom: 6px;
        }

        .system-ingest__meta {
          font-size: 12px;
          margin-bottom: 6px;
          opacity: 0.9;
        }

        .system-ingest__grid {
          display: flex;
          gap: 8px;
          flex-wrap: wrap;
        }

        .system-ingest__pill {
          background: rgba(255, 255, 255, 0.7);
          border: 1px solid rgba(69, 102, 167, 0.2);
          color: #1f355a;
          padding: 4px 10px;
          border-radius: 999px;
          font-size: 12px;
          font-weight: 600;
        }

        .top-nav-groups {
          display: flex;
          flex-direction: column;
          gap: 10px;
          width: 100%;
        }

        .top-nav-group {
          display: grid;
          grid-template-columns: minmax(150px, 200px) minmax(0, 1fr);
          gap: 12px;
          align-items: start;
        }

        .top-nav-group__meta {
          display: flex;
          flex-direction: column;
          gap: 2px;
          padding-top: 6px;
          color: #51615c;
        }

        .top-nav-group__label {
          font-size: 12px;
          font-weight: 800;
          letter-spacing: 0.08em;
          text-transform: uppercase;
          color: #1f2a2a;
        }

        .top-nav-group__description {
          font-size: 12px;
        }

        .top-nav {
          display: flex;
          flex-wrap: wrap;
          gap: 10px;
          justify-content: flex-end;
          align-items: center;
        }

        @media (max-width: 900px) {
          .top-nav-group {
            grid-template-columns: 1fr;
            gap: 6px;
          }

          .top-nav-group__meta {
            padding-top: 0;
          }

          .top-nav {
            justify-content: flex-start;
          }
        }

        :global(.top-link) {
          display: inline-flex;
          align-items: center;
          padding: 8px 14px;
          border-radius: 999px;
          border: 1px solid rgba(31, 42, 42, 0.14);
          background: #f4f1ea;
          color: #1f2a2a;
          font-size: 13px;
          font-weight: 600;
          letter-spacing: 0.02em;
          transition: transform 0.2s ease, background 0.2s ease, color 0.2s ease,
            box-shadow 0.2s ease;
        }

        :global(.top-link:hover) {
          background: #1f2a2a;
          color: #f7f2e7;
          transform: translateY(-1px);
          box-shadow: 0 8px 14px rgba(20, 30, 28, 0.18);
        }

        :global(.top-link.active) {
          background: #1f2a2a;
          color: #f7f2e7;
          box-shadow: 0 6px 12px rgba(20, 30, 28, 0.16);
        }

        :global(.top-link:focus-visible) {
          outline: 2px solid #5f7b74;
          outline-offset: 2px;
        }
      `}</style>
    </div>
  );
}
