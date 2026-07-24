import { useRouter } from "next/router";
import type { ReactNode } from "react";

type Props = {
  children: ReactNode;
  gitSha: string;
  deployedAt: string;
  publicPage?: boolean;
};

const systemForPath = (path: string) => {
  if (path.startsWith("/hospital")) return "病院注文";
  if (path.startsWith("/shift")) return "シフト管理";
  if (path.startsWith("/school-lunch")) return "学校給食";
  return "システム選択";
};

export default function UnifiedShell({ children, gitSha, deployedAt, publicPage = false }: Props) {
  const router = useRouter();
  const path = router.asPath.split("?")[0] || "/";
  const currentSystem = publicPage ? "共通ログイン" : systemForPath(path);
  const shortSha = gitSha === "unknown" ? gitSha : gitSha.slice(0, 12);

  return (
    <div className="unified-shell">
      <header className="unified-header">
        <div className="unified-header__inner">
          <a href={publicPage ? "/about" : "/"} className="unified-brand" aria-label="SAWA 統合管理">
            <span className="unified-brand__mark" aria-hidden="true">S</span>
            <span><strong>SAWA</strong><small>統合管理</small></span>
          </a>
          <span className="unified-current">{currentSystem}</span>
          {!publicPage ? (
            <nav className="unified-system-nav" aria-label="システム切替">
              <a href="/" className={path === "/" ? "active" : ""}>システム選択</a>
              <a href="/hospital" className={path.startsWith("/hospital") ? "active" : ""}>病院注文</a>
              <a href="/shift" className={path.startsWith("/shift") ? "active" : ""}>シフト管理</a>
              <a href="/school-lunch" className={path.startsWith("/school-lunch") ? "active" : ""}>学校給食</a>
            </nav>
          ) : null}
        </div>
      </header>
      <div className="unified-shell__content">{children}</div>
      <footer className="unified-footer" aria-label="deploy version">
        <span>SAWA 統合管理</span><span>version {shortSha}</span><span>deployed {deployedAt}</span>
      </footer>
      <style jsx global>{`
        :root{--sawa-ink:#17201f;--sawa-muted:#60716d;--sawa-border:#d9e2df;--sawa-panel:#fff;--sawa-bg:#f3f6f5;--sawa-accent:#176b5e;--sawa-accent-soft:#e8f4f0}
        *{box-sizing:border-box}html,body,#__next{min-height:100%;margin:0}body{color:var(--sawa-ink);background:var(--sawa-bg);font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}button,input,select,textarea{font:inherit}a{color:inherit}
        .unified-shell{min-height:100vh;display:flex;flex-direction:column}.unified-shell__content{flex:1}.unified-header{position:relative;z-index:20;border-bottom:1px solid var(--sawa-border);background:rgba(255,255,255,.96);box-shadow:0 8px 22px rgba(18,33,31,.05)}
        .unified-header__inner{max-width:1440px;min-height:68px;margin:auto;padding:10px 24px;display:flex;align-items:center;gap:18px}.unified-brand{display:flex;align-items:center;gap:10px;text-decoration:none}.unified-brand__mark{width:40px;height:40px;display:grid;place-items:center;border-radius:12px;color:#fff;background:var(--sawa-accent);font-size:22px;font-weight:900}.unified-brand strong,.unified-brand small{display:block}.unified-brand strong{font-size:16px;letter-spacing:.08em}.unified-brand small{margin-top:1px;color:var(--sawa-muted);font-size:11px;font-weight:700}
        .unified-current{padding:6px 10px;border-left:1px solid var(--sawa-border);color:var(--sawa-muted);font-size:13px;font-weight:800}.unified-system-nav{margin-left:auto;display:flex;align-items:center;gap:6px;flex-wrap:wrap}.unified-system-nav a{min-height:36px;padding:0 12px;display:inline-flex;align-items:center;border:1px solid transparent;border-radius:8px;color:#40514d;font-size:13px;font-weight:800;text-decoration:none}.unified-system-nav a:hover,.unified-system-nav a.active{border-color:#a6d2c5;color:var(--sawa-accent);background:var(--sawa-accent-soft)}
        .unified-footer{margin-top:auto;padding:10px 24px;display:flex;justify-content:flex-end;gap:14px;flex-wrap:wrap;border-top:1px solid var(--sawa-border);color:var(--sawa-muted);background:var(--sawa-panel);font-size:11px}
        @media(max-width:820px){.unified-header__inner{align-items:flex-start;flex-wrap:wrap;padding:10px 14px}.unified-system-nav{width:100%;margin-left:0;overflow-x:auto;flex-wrap:nowrap}.unified-current{margin-left:auto}.unified-footer{justify-content:flex-start;padding-inline:14px}}
      `}</style>
    </div>
  );
}
