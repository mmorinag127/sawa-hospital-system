import { useRouter } from "next/router";
import type { ReactNode } from "react";
import { clearAuth } from "../services/apiClient";
import { enterSchoolLunch } from "../services/systemNavigation";

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
  const logout = () => {
    clearAuth();
    window.sessionStorage.removeItem("auth_next");
    window.google?.accounts?.id?.disableAutoSelect?.();
    window.location.replace("/login");
  };

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
              <a href="/" className={path === "/" ? "active" : ""}>統合トップに戻る</a>
              <a href="/hospital" className={path.startsWith("/hospital") ? "active" : ""}>病院注文</a>
              <a href="/shift" className={path.startsWith("/shift") ? "active" : ""}>シフト管理</a>
              <a href="/school-lunch" onClick={enterSchoolLunch} className={path.startsWith("/school-lunch") ? "active" : ""}>学校給食</a>
              <button type="button" className="unified-logout" onClick={logout}>ログアウト</button>
            </nav>
          ) : null}
        </div>
      </header>
      <div className="unified-shell__content">{children}</div>
      <footer className="unified-footer" aria-label="deploy version">
        <span>SAWA 統合管理</span><span>version {shortSha}</span><span>deployed {deployedAt}</span>
      </footer>
    </div>
  );
}
