import { Button, Input, NativeSelect, Checkbox } from "@mui/material";
import { ThemeProvider } from "@mui/material/styles";
import { sawaTheme } from "@sawa/ui";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";

import { useCurrentUserRole } from "../../hooks/useCurrentUserRole";
import { apiClient } from "../../services/apiClient";

type UserRole = "admin" | "operator";
type UserStatus = "active" | "inactive";
type SystemKey = "hospital" | "shift" | "school-lunch";

type User = {
  id: string;
  account: string;
  role: UserRole;
  status: UserStatus;
  systems: SystemKey[];
};

type Notice = {
  kind: "success" | "error" | "warning";
  text: string;
};

const emptyUser = (): User => ({
  id: "",
  account: "",
  role: "operator",
  status: "active",
  systems: [],
});

const systemLabels: Record<SystemKey, string> = {
  hospital: "病院注文",
  shift: "シフト管理",
  "school-lunch": "学校給食",
};

const errorMessage = (error: unknown, fallback: string) => {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  return typeof detail === "string" && detail.trim() ? `${fallback}: ${detail}` : fallback;
};

export default function CommonUsersPage() {
  const { isAdmin, loading: roleLoading } = useCurrentUserRole();
  const [items, setItems] = useState<User[]>([]);
  const [editing, setEditing] = useState<User | null>(null);
  const [listLoading, setListLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);

  const loadUsers = useCallback(async () => {
    setListLoading(true);
    try {
      const response = await apiClient.get<{ items?: User[] }>("/portal/users");
      setItems(Array.isArray(response.data.items) ? response.data.items : []);
      return true;
    } catch (error) {
      setNotice({ kind: "error", text: errorMessage(error, "ユーザー一覧を取得できませんでした") });
      return false;
    } finally {
      setListLoading(false);
    }
  }, []);

  useEffect(() => {
    if (roleLoading || !isAdmin) return;
    void loadUsers();
  }, [isAdmin, loadUsers, roleLoading]);

  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!editing || saving) return;

    setSaving(true);
    setNotice(null);
    const body = {
      account: editing.account,
      role: editing.role,
      status: editing.status,
      systems: editing.systems,
    };

    try {
      const response = editing.id
        ? await apiClient.put<{ user: User }>(`/portal/users/${editing.id}`, body)
        : await apiClient.post<{ user: User }>("/portal/users", body);
      const saved = response.data.user;
      setItems((current) => {
        const exists = current.some((item) => item.id === saved.id);
        const next = exists
          ? current.map((item) => (item.id === saved.id ? saved : item))
          : [...current, saved];
        return next.sort((left, right) => left.account.localeCompare(right.account, "ja"));
      });
      setEditing(null);
      setNotice({ kind: "success", text: `${saved.account} を保存しました。` });
    } catch (error) {
      setNotice({ kind: "error", text: errorMessage(error, "ユーザーを保存できませんでした") });
    } finally {
      setSaving(false);
    }
  };

  const toggle = (key: SystemKey) => {
    setEditing((current) =>
      current
        ? {
            ...current,
            systems: current.systems.includes(key)
              ? current.systems.filter((system) => system !== key)
              : [...current.systems, key],
          }
        : current,
    );
  };

  if (roleLoading) {
    return (
      <main className="sawa-page" aria-busy="true">
        <div className="sawa-notice">管理者権限を確認しています。</div>
      </main>
    );
  }

  if (!isAdmin) {
    return (
      <main className="sawa-page">
        <Link className="sawa-page__back" href="/">← システム選択へ戻る</Link>
        <section className="sawa-card" aria-labelledby="access-denied-title">
          <div className="sawa-card__body">
            <p className="sawa-page__eyebrow">Administrator only</p>
            <h1 className="sawa-page__title" id="access-denied-title">管理者権限が必要です</h1>
            <p className="sawa-page__description">
              ユーザー一覧と権限設定は管理者だけが確認できます。管理者アカウントでログインしてください。
            </p>
          </div>
        </section>
      </main>
    );
  }

  return (
    <ThemeProvider theme={sawaTheme}>
    <main className="sawa-page admin-users-page">
      <Link className="sawa-page__back" href="/">← システム選択へ戻る</Link>
      <header className="sawa-page__header">
        <div>
          <p className="sawa-page__eyebrow">Administration</p>
          <h1 className="sawa-page__title">共通ユーザー管理</h1>
          <p className="sawa-page__description">
            利用者の権限・状態と、アクセスできるシステムを一か所で管理します。
          </p>
        </div>
        <Button
          className="sawa-button sawa-button--primary admin-ordinary-control"
          type="button"
          onClick={() => {
            setEditing(emptyUser());
            setNotice(null);
          }}
        >
          ユーザーを追加
        </Button>
      </header>

      <div aria-live="polite" aria-atomic="true">
        {notice ? (
          <p className={`sawa-notice sawa-notice--${notice.kind}`} role={notice.kind === "error" ? "alert" : "status"}>
            {notice.text}
          </p>
        ) : null}
      </div>

      {editing ? (
        <section className="sawa-card" aria-labelledby="user-editor-title">
          <div className="sawa-card__header">
            <div>
              <h2 id="user-editor-title">{editing.id ? "ユーザーを編集" : "新しいユーザー"}</h2>
              <p>メールアドレス、権限、利用システムを確認して保存してください。</p>
            </div>
          </div>
          <form className="sawa-card__body sawa-form-grid" onSubmit={save}>
            <label className="sawa-field sawa-field--wide">
              メールアドレス
              <Input className="admin-ordinary-control" disableUnderline
                type="email"
                required
                autoComplete="email"
                placeholder="user@example.com"
                value={editing.account}
                onChange={(event) => setEditing({ ...editing, account: event.target.value })}
              />
            </label>
            <label className="sawa-field">
              権限
              <NativeSelect className="admin-ordinary-control" disableUnderline
                value={editing.role}
                onChange={(event) => setEditing({ ...editing, role: event.target.value as UserRole })}
              >
                <option value="operator">一般利用者</option>
                <option value="admin">管理者</option>
              </NativeSelect>
            </label>
            <label className="sawa-field">
              アカウント状態
              <NativeSelect className="admin-ordinary-control" disableUnderline
                value={editing.status}
                onChange={(event) => setEditing({ ...editing, status: event.target.value as UserStatus })}
              >
                <option value="active">有効</option>
                <option value="inactive">停止</option>
              </NativeSelect>
            </label>
            <fieldset className="admin-system-access sawa-field--wide">
              <legend>利用できるシステム</legend>
              <div className="admin-system-access__options">
                {(Object.keys(systemLabels) as SystemKey[]).map((key) => (
                  <label key={key}>
                    <Checkbox className="admin-ordinary-control"
                      checked={editing.systems.includes(key)}
                      onChange={() => toggle(key)}
                    />
                    <span>{systemLabels[key]}</span>
                  </label>
                ))}
              </div>
            </fieldset>
            <div className="sawa-actions sawa-field--wide">
              <Button
                className="sawa-button admin-ordinary-control"
                type="button"
                disabled={saving}
                onClick={() => setEditing(null)}
              >
                キャンセル
              </Button>
              <Button className="sawa-button sawa-button--primary admin-ordinary-control" type="submit" disabled={saving}>
                {saving ? "保存中…" : "保存"}
              </Button>
            </div>
          </form>
        </section>
      ) : null}

      <section className="sawa-card admin-user-list" aria-labelledby="user-list-title">
        <div className="sawa-card__header">
          <div>
            <h2 id="user-list-title">ユーザー一覧</h2>
            <p>{listLoading ? "読み込み中です。" : `${items.length}件のユーザー`}</p>
          </div>
          <Button className="sawa-button admin-ordinary-control" type="button" disabled={listLoading} onClick={() => void loadUsers()}>
            {listLoading ? "更新中…" : "一覧を更新"}
          </Button>
        </div>
        {listLoading && items.length === 0 ? <div className="sawa-empty">ユーザー一覧を読み込んでいます。</div> : null}
        {!listLoading && items.length === 0 ? <div className="sawa-empty">登録されているユーザーはいません。</div> : null}
        {items.length > 0 ? (
          <div className="sawa-table-wrap">
            <table className="sawa-table">
              <thead>
                <tr><th>メールアドレス</th><th>権限</th><th>状態</th><th>利用システム</th><th>操作</th></tr>
              </thead>
              <tbody>
                {items.map((user) => (
                  <tr key={user.id}>
                    <td><strong>{user.account}</strong></td>
                    <td><span className={`sawa-badge${user.role === "admin" ? " sawa-badge--admin" : ""}`}>{user.role === "admin" ? "管理者" : "一般利用者"}</span></td>
                    <td><span className={`sawa-badge${user.status === "active" ? " sawa-badge--active" : ""}`}>{user.status === "active" ? "有効" : "停止"}</span></td>
                    <td>{user.systems.map((system) => systemLabels[system]).join(" / ") || "なし"}</td>
                    <td><Button className="sawa-button admin-ordinary-control" type="button" onClick={() => { setEditing({ ...user }); setNotice(null); }}>編集</Button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>

      <style jsx>{`
        :global(.admin-ordinary-control.MuiButton-root) {
          text-transform: none;
          letter-spacing: inherit;
          min-width: 0;
        }
        :global(.admin-ordinary-control.sawa-button) {
          min-height: 42px;
          padding: 0 16px;
          border: 1px solid var(--sawa-border-strong);
          border-radius: var(--sawa-radius-sm);
          color: #344642;
          background: #fff;
          font-weight: 850;
          cursor: pointer;
        }
        :global(.admin-ordinary-control.sawa-button:hover:not(:disabled)) {
          border-color: var(--sawa-accent);
          color: var(--sawa-accent);
        }
        :global(.admin-ordinary-control.sawa-button--primary) {
          border-color: var(--sawa-accent);
          color: #fff;
          background: var(--sawa-accent);
        }
        :global(.admin-ordinary-control.sawa-button--primary:hover:not(:disabled)) {
          border-color: var(--sawa-accent-hover);
          color: #fff;
          background: var(--sawa-accent-hover);
        }
        :global(.admin-ordinary-control.sawa-button:disabled) {
          opacity: 0.55;
          cursor: not-allowed;
        }
        :global(.admin-users-page .sawa-field .admin-ordinary-control.MuiInputBase-root) {
          width: 100%;
          min-height: 44px;
          padding: 10px 12px;
          border: 1px solid var(--sawa-border-strong);
          border-radius: var(--sawa-radius-sm);
          color: var(--sawa-ink);
          background: #fff;
          font: inherit;
        }
        :global(.admin-users-page .sawa-field .admin-ordinary-control.MuiInputBase-root:focus-within) {
          border-color: var(--sawa-accent);
          box-shadow: 0 0 0 3px rgba(23, 107, 94, 0.12);
        }
        :global(.admin-users-page .sawa-field .admin-ordinary-control .MuiInputBase-input) {
          width: 100%;
          min-height: 0;
          padding: 0;
          border: 0;
          border-radius: 0;
          color: inherit;
          background: transparent;
          box-shadow: none;
        }
        :global(.admin-ordinary-control.MuiCheckbox-root) {
          padding: 0;
        }
        :global(.admin-ordinary-control.MuiCheckbox-root .MuiSvgIcon-root) {
          font-size: 18px;
        }
        :global(.admin-system-access__options .admin-ordinary-control.MuiCheckbox-root input) {
          width: 100%;
          height: 100%;
        }
      `}</style>
    </main>
    </ThemeProvider>
  );
}
