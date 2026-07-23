import Link from "next/link";
import { useRouter } from "next/router";

import { useCurrentUserRole } from "../hooks/useCurrentUserRole";

type NavItem = {
  href: string;
  label: string;
  isActive: (path: string) => boolean;
  minRole?: "operator" | "admin";
};

type NavGroup = {
  id: string;
  label: string;
  description: string;
  items: NavItem[];
};

const normalizePath = (path: string) => {
  const pathname = path.split("?")[0]?.split("#")[0] ?? path;
  if (pathname === "/hospital") return "/";
  return pathname.startsWith("/hospital/") ? pathname.slice("/hospital".length) : pathname;
};

const hospitalHref = (href: string) => (href === "/" ? "/hospital" : `/hospital${href}`);

export default function TopNav() {
  const router = useRouter();
  const currentPath = normalizePath(router.asPath || "/");
  const { role } = useCurrentUserRole();
  const roleRank = role === "admin" ? 2 : 1;

  const navGroups: NavGroup[] = [
    {
      id: "orders",
      label: "注文系",
      description: "注文の確認とアップロード",
      items: [
        {
          href: "/orders",
          label: "注文一覧",
          isActive: (path) => path.startsWith("/orders") || path.startsWith("/weekly-orders"),
        },
        {
          href: "/pdf-upload",
          label: "注文書アップロード",
          isActive: (path) => path.startsWith("/pdf-upload"),
        },
        {
          href: "/order-forms",
          label: "注文書生成",
          isActive: (path) => path.startsWith("/order-forms"),
        },
      ],
    },
    {
      id: "work",
      label: "作業系",
      description: "日別出力と発送作業",
      items: [
        {
          href: "/daily-delivery-notes",
          label: "日別出力",
          isActive: (path) => path.startsWith("/daily-delivery-notes") || path.startsWith("/totals"),
        },
        {
          href: "/weekly-weight-output",
          label: "週別重量表",
          isActive: (path) => path.startsWith("/weekly-weight-output"),
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
      id: "facilities",
      label: "施設系",
      description: "施設ごとの注文と設定",
      items: [
        {
          href: "/facility-orders",
          label: "施設別注文",
          isActive: (path) => path.startsWith("/facility-orders"),
        },
        {
          href: "/facility-master",
          label: "施設一覧",
          isActive: (path) => path === "/facility-master" || path.startsWith("/facilities"),
        },
      ],
    },
    {
      id: "menus",
      label: "メニュー系",
      description: "月次・基準・マスター・ルール",
      items: [
        {
          href: "/menus",
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
      ],
    },
    {
      id: "admin",
      label: "管理者系",
      description: "監視と保守",
      items: [
        {
          href: "/system-status",
          label: "システム管理",
          isActive: (path) => path.startsWith("/system-status"),
          minRole: "admin",
        },
        {
          href: "/system-process-logs",
          label: "処理ログ",
          isActive: (path) => path.startsWith("/system-process-logs"),
          minRole: "admin",
        },
        {
          href: "/ocr-queue",
          label: "OCRキュー",
          isActive: (path) => path.startsWith("/ocr-queue"),
          minRole: "admin",
        },
        {
          href: "/ocr-results",
          label: "OCR結果監視",
          isActive: (path) => path.startsWith("/ocr-results"),
          minRole: "admin",
        },
        {
          href: "/ocr-training-data",
          label: "OCR学習データ",
          isActive: (path) => path.startsWith("/ocr-training-data"),
          minRole: "admin",
        },
      ],
    },
  ];

  const visibleNavGroups = navGroups
    .map((group) => ({
      ...group,
      items: group.items.filter((item) => {
        const requiredRank = item.minRole === "admin" ? 2 : 1;
        return roleRank >= requiredRank;
      }),
    }))
    .filter((group) => group.items.length > 0);

  return (
    <div className="top-nav-wrap">
      <div className="top-nav-groups">
        <div className="top-nav-group top-nav-group--dashboard">
          <div className="top-nav-group__meta">
            <div className="top-nav-group__label">ダッシュボード</div>
            <div className="top-nav-group__description">全体状況の確認と入口</div>
          </div>
          <Link
            href="/hospital"
            className={`dashboard-link${currentPath === "/" ? " active" : ""}`}
            aria-current={currentPath === "/" ? "page" : undefined}
          >
            <span className="top-link__label">ダッシュボード</span>
          </Link>
        </div>
        {visibleNavGroups.map((group) => (
          <div key={group.id} className={`top-nav-group top-nav-group--${group.id}`}>
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
                    href={hospitalHref(item.href)}
                    className={`top-link${active ? " active" : ""}`}
                    aria-current={active ? "page" : undefined}
                  >
                    <span className="top-link__label">{item.label}</span>
                  </Link>
                );
              })}
            </nav>
          </div>
        ))}
      </div>
      <style jsx>{`
        .top-nav-wrap {
          display: block;
          flex: 0 0 100%;
          min-width: 0;
          width: 100%;
        }

        :global(.hero > div:first-child) {
          min-height: 126px;
        }

        .top-nav-groups {
          display: grid;
          gap: 10px;
          grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
        }

        .top-nav-group {
          display: grid;
          gap: 6px;
          align-content: start;
          padding: 11px 12px;
          border-radius: 18px;
          border: 1px solid rgba(18, 41, 38, 0.09);
          background: #ffffff;
          backdrop-filter: blur(10px);
          box-shadow: 0 10px 22px rgba(18, 33, 31, 0.05);
        }

        .top-nav-group__meta {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 10px;
        }

        .top-nav-group__label {
          font-size: 11px;
          letter-spacing: 0.1em;
          color: #5f7b74;
          font-weight: 700;
        }

        .top-nav-group__description {
          display: none;
        }

        .top-nav {
          display: grid;
          gap: 8px;
          grid-template-columns: 1fr;
        }

        :global(.dashboard-link) {
          display: flex;
          align-items: center;
          justify-content: flex-start;
          min-height: 48px;
          padding: 12px 14px;
          border-radius: 12px;
          background: #fbfbf9;
          color: #17302c;
          font-weight: 700;
          text-decoration: none;
          border: 1px solid rgba(25, 32, 30, 0.06);
          box-shadow: none;
          transition: background 0.14s ease, border-color 0.14s ease;
        }

        :global(.dashboard-link:hover) {
          background: #f2f4f1;
        }

        :global(.dashboard-link.active) {
          background: #eef2ef;
          border-color: rgba(25, 32, 30, 0.14);
        }

        :global(.top-link) {
          display: flex;
          align-items: center;
          justify-content: flex-start;
          min-height: 48px;
          min-width: 0;
          padding: 12px 14px;
          border-radius: 12px;
          background: #fbfbf9;
          color: #17302c;
          font-size: 13px;
          font-weight: 700;
          text-decoration: none;
          border: 1px solid rgba(25, 32, 30, 0.06);
          box-shadow: none;
          transition: background 0.14s ease, border-color 0.14s ease;
        }

        .top-link__label {
          line-height: 1.35;
          font-size: 12px;
          text-align: left;
        }

        :global(.top-link:hover) {
          background: #f2f4f1;
        }

        :global(.top-link.active) {
          background: #eef2ef;
          border-color: rgba(25, 32, 30, 0.14);
        }

        @media (max-width: 720px) {
          .top-nav-group {
            padding: 12px 14px;
          }

          .top-nav-groups {
            grid-template-columns: 1fr;
          }

          .top-link {
            min-height: 46px;
          }
        }
      `}</style>
    </div>
  );
}
