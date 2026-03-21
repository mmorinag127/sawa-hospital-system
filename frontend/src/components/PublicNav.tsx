import Link from "next/link";
import { useRouter } from "next/router";

const PUBLIC_LINKS = [
  { href: "/about", label: "ホームページ" },
  { href: "/privacy", label: "プライバシー" },
  { href: "/terms", label: "利用規約" },
  { href: "/login", label: "ログイン" },
];

export default function PublicNav() {
  const router = useRouter();
  const currentPath = (router.asPath || "/").split("?")[0]?.split("#")[0] ?? "/";

  return (
    <nav className="public-nav" aria-label="Public navigation">
      {PUBLIC_LINKS.map((link) => {
        const active = currentPath === link.href;
        return (
          <Link key={link.href} href={link.href} className={active ? "active" : ""}>
            {link.label}
          </Link>
        );
      })}
      <style jsx>{`
        .public-nav {
          display: flex;
          flex-wrap: wrap;
          gap: 10px;
          align-items: center;
        }

        .public-nav :global(a) {
          padding: 10px 16px;
          border-radius: 999px;
          border: 1px solid rgba(31, 42, 42, 0.14);
          background: rgba(255, 255, 255, 0.82);
          color: #1f2a2a;
          font-weight: 600;
          text-decoration: none;
        }

        .public-nav :global(a.active) {
          background: #1f2a2a;
          color: #f7f2e7;
          border-color: #1f2a2a;
        }
      `}</style>
    </nav>
  );
}
