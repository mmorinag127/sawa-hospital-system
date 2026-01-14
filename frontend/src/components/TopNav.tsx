import Link from "next/link";
import { useRouter } from "next/router";

type NavItem = {
  href: string;
  label: string;
  isActive: (path: string) => boolean;
};

const buildMonthId = () => {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
};

const normalizePath = (path: string) => path.split("?")[0]?.split("#")[0] ?? path;

export default function TopNav() {
  const router = useRouter();
  const currentPath = normalizePath(router.asPath || "/");
  const navItems: NavItem[] = [
    {
      href: "/",
      label: "Dashboard",
      isActive: (path) => path === "/",
    },
    {
      href: `/menus/${buildMonthId()}`,
      label: "月次メニュー",
      isActive: (path) => path.startsWith("/menus"),
    },
    {
      href: "/menu-rules",
      label: "メニュールール",
      isActive: (path) => path.startsWith("/menu-rules"),
    },
    {
      href: "/orders",
      label: "Orders",
      isActive: (path) => path.startsWith("/orders"),
    },
    {
      href: "/facilities",
      label: "Facilities",
      isActive: (path) => path.startsWith("/facilities"),
    },
    {
      href: "/facility-master",
      label: "Facility Master",
      isActive: (path) => path === "/facility-master",
    },
    {
      href: "/ocr-queue",
      label: "OCR Queue",
      isActive: (path) => path.startsWith("/ocr-queue"),
    },
  ];

  return (
    <nav className="top-nav">
      {navItems.map((item) => {
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
      <style jsx>{`
        .top-nav {
          display: flex;
          flex-wrap: wrap;
          gap: 10px;
          justify-content: flex-end;
          align-items: center;
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
    </nav>
  );
}
