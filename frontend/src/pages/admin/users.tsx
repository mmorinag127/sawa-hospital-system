import { Button, MenuItem, Checkbox } from "@mui/material";
import { DataTable, EditForm, FormTextField, SawaProvider } from "@sawa/ui";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { Controller, useFormContext } from "react-hook-form";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";

import { refreshCurrentUserRole, useCurrentUserRole } from "../../hooks/useCurrentUserRole";
import { useH1LeaveGuard } from "../../hooks/useH1LeaveGuard";
import { apiClient, hasActiveSessionAuthHeader } from "../../services/apiClient";
import { browserSessionKey, subscribeBrowserSession } from "../../services/browserSession";

type UserRole = "admin" | "operator";
type UserStatus = "active" | "inactive";
type SystemKey = "hospital" | "shift" | "school-lunch";

type User = {
  id: string;
  account: string;
  role: UserRole;
  status: UserStatus;
  systems: string[];
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
const isKnownSystem = (key: string): key is SystemKey => Object.prototype.hasOwnProperty.call(systemLabels, key);

const localCommandReasons = {
  target: "編集対象を確認できません。",
  systems: "利用システムの現在値を確認してください。未対応の値は破棄せず保存を停止しました。",
  cancelled: "ユーザー保存は取り消されました。",
  retired: "ユーザー保存の編集対象が変更されました。",
  savedTarget: "保存されたユーザーの対象・値を確認できません。",
} as const;
class LocalUserCommandError extends Error {
  constructor(readonly reason: keyof typeof localCommandReasons) { super(localCommandReasons[reason]); }
}

const errorMessage = (error: unknown, fallback: string) => {
  if (error instanceof LocalUserCommandError) return localCommandReasons[error.reason];
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  return typeof detail === "string" && detail.trim() ? `${fallback}: ${detail}` : fallback;
};

const identity = (value: unknown): value is string => typeof value === "string" && !!value.trim() && value === value.trim();
const validUser = (value: unknown): value is User => {
  const user = value as User | null;
  return !!user && identity(user.id) && typeof user.account === "string" && !!user.account.trim()
    && ["admin", "operator"].includes(user.role) && ["active", "inactive"].includes(user.status)
    && Array.isArray(user.systems) && user.systems.every(identity) && new Set(user.systems).size === user.systems.length;
};
const editorPaths = ["/admin/users", "/hospital/admin/users"];
class UsersReadError extends Error {
  constructor(message: string, readonly current: () => boolean) { super(message); }
}

function UserFields() {
  const emailField = useRef<HTMLDivElement | null>(null);
  const { control } = useFormContext<User>();
  return <>
    <div ref={emailField}><FormTextField<User> name="account" label="メールアドレス" type="email" required autoComplete="email"
      placeholder="user@example.com"
      rules={{ validate: () => {
        const input = emailField.current?.querySelector<HTMLInputElement>('input[name="account"]');
        if (!input) return "メールアドレスの入力を確認できません。";
        const valid = input.checkValidity();
        if (!valid) input.reportValidity();
        return valid || input.validationMessage;
      } }} /></div>
    <FormTextField<User> name="role" label="権限" select>
      <MenuItem value="operator">一般利用者</MenuItem><MenuItem value="admin">管理者</MenuItem>
    </FormTextField>
    <FormTextField<User> name="status" label="アカウント状態" select>
      <MenuItem value="active">有効</MenuItem><MenuItem value="inactive">停止</MenuItem>
    </FormTextField>
    <Controller name="systems" control={control} render={({ field }) => <fieldset className="admin-system-access sawa-field--wide">
      <legend>利用できるシステム</legend><div className="admin-system-access__options">
        {(Object.keys(systemLabels) as SystemKey[]).map(key => <label key={key}>
          <Checkbox className="admin-ordinary-control" checked={field.value.includes(key)} onChange={() => field.onChange(
            field.value.includes(key) ? field.value.filter(system => system !== key) : [...field.value, key])} />
          <span>{systemLabels[key]}</span>
        </label>)}
        {field.value.filter(key => !isKnownSystem(key)).map(key => <span key={key}>現在の利用システム: {key}</span>)}
      </div>
    </fieldset>} />
  </>;
}

function UsersContent({ session }: { session: string }) {
  const { isAdmin, loading: roleLoading } = useCurrentUserRole();
  const [editing, setEditing] = useState<User | null>(null);
  const [editorEpoch, setEditorEpoch] = useState(0);
  const [dirty, setDirty] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [page, setPage] = useState(0), [pageSize, setPageSize] = useState(25);
  const mounted = useRef(true), editorGeneration = useRef(0), inputGeneration = useRef(0), operation = useRef(0), readGeneration = useRef(0);
  const admin = useRef(isAdmin), dirtyRef = useRef(dirty), snapshot = useRef("");
  const sortAfterSave = useRef(false);
  const formLease = useRef<(() => boolean) | null>(null);
  if (admin.current !== isAdmin) { ++operation.current; ++readGeneration.current; }
  admin.current = isAdmin; dirtyRef.current = dirty;
  const client = useQueryClient();
  const queryKey = ["portal-users", session];
  const currentSession = () => mounted.current && admin.current && session === browserSessionKey() && hasActiveSessionAuthHeader();
  useH1LeaveGuard(dirty, editorPaths);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; ++editorGeneration.current; ++operation.current; ++readGeneration.current; };
  }, []);
  const users = useQuery({
    queryKey, enabled: !roleLoading && isAdmin, retry: false, staleTime: 0, refetchOnWindowFocus: false,
    queryFn: async ({ signal }) => {
      const generation = ++readGeneration.current;
      const editor = editorGeneration.current;
      const current = () => currentSession() && !signal.aborted && generation === readGeneration.current;
      try {
        const response = await apiClient.get<{ items: unknown }>("/portal/users", { signal });
        if (!current() || editor !== editorGeneration.current) throw new Error("ユーザー一覧の読み込みは取り消されました。");
        if (!Array.isArray(response.data?.items) || !response.data.items.every(validUser)
          || new Set(response.data.items.map(user => user.id)).size !== response.data.items.length) throw new Error("ユーザー一覧の取得値を確認できません。");
        const items = sortAfterSave.current ? [...response.data.items].sort((left, right) => left.account.localeCompare(right.account, "ja")) : response.data.items;
        // Previously adopted rows remain visible while a fresh read is pending.
        return { items, current: currentSession };
      } catch (error) {
        throw new UsersReadError(errorMessage(error, "ユーザー一覧を取得できませんでした"), () => current() && editor === editorGeneration.current);
      }
    },
  });
  const items = users.data?.current() && isAdmin ? users.data.items : [];
  const listLoading = users.isFetching;
  const chooseEditor = (user: User | null) => {
    if (dirtyRef.current && !window.confirm("未保存の変更を破棄しますか？")) return;
    ++editorGeneration.current; ++operation.current; ++inputGeneration.current;
    snapshot.current = ""; dirtyRef.current = false; setDirty(false);
    setEditorEpoch(epoch => epoch + 1);
    setEditing(user ? { ...user, systems: [...user.systems] } : null); setNotice(null);
    void client.cancelQueries({ queryKey }).then(() => {
      if (currentSession()) void client.invalidateQueries({ queryKey });
    });
  };
  const beginOperation = () => {
    const editor = editorGeneration.current, input = inputGeneration.current, id = ++operation.current;
    return () => currentSession() && editor === editorGeneration.current && input === inputGeneration.current && id === operation.current;
  };
  const save = async (values: User) => {
    const own = formLease.current;
    if (!own?.() || !editing || values.id !== editing.id) throw new LocalUserCommandError("target");
    if (values.systems.some(key => !isKnownSystem(key))) throw new LocalUserCommandError("systems");
    ++readGeneration.current;
    await client.cancelQueries({ queryKey });
    if (!own()) throw new LocalUserCommandError("cancelled");
    setNotice(null);
    const body = {
      account: values.account, role: values.role, status: values.status, systems: values.systems,
    };
    try {
      const response = values.id
        ? await apiClient.put<{ user: User }>(`/portal/users/${values.id}`, body)
        : await apiClient.post<{ user: User }>("/portal/users", body);
      // Every completed permission write refreshes actual authority, even if its editor was retired.
      const refreshRole = () => session === browserSessionKey() ? refreshCurrentUserRole() : Promise.resolve();
      if (!own()) { void refreshRole(); throw new LocalUserCommandError("retired"); }
      const saved = response.data.user;
      if (!validUser(saved) || (values.id && saved.id !== values.id)) { void refreshRole(); throw new LocalUserCommandError("savedTarget"); }
      sortAfterSave.current = true;
      ++readGeneration.current;
      const next = items.some(item => item.id === saved.id) ? items.map(item => item.id === saved.id ? saved : item) : [...items, saved];
      client.setQueryData(queryKey, { items: next.sort((left, right) => left.account.localeCompare(right.account, "ja")),
        current: currentSession });
      dirtyRef.current = false; setDirty(false); setEditing(null); ++editorGeneration.current;
      setNotice({ kind: "success", text: `${saved.account} を保存しました。` });
      void client.invalidateQueries({ queryKey });
      void refreshRole();
      return saved;
    } catch (error) {
      if (own()) {
        void client.invalidateQueries({ queryKey });
      }
      throw error;
    }
  };

  return (
    <main className="sawa-page admin-users-page">
      {roleLoading ? <p className="sawa-notice" role="status">管理者権限を確認しています。</p> : null}
      {!roleLoading && !isAdmin ? <section className="sawa-card" aria-labelledby="access-denied-title">
        <Link className="sawa-page__back" href="/">← システム選択へ戻る</Link>
        <div className="sawa-card__body">
          <p className="sawa-page__eyebrow">Administrator only</p>
          <h1 className="sawa-page__title" id="access-denied-title">管理者権限が必要です</h1>
          <p className="sawa-page__description">ユーザー一覧と権限設定は管理者だけが確認できます。管理者アカウントでログインしてください。</p>
          {editing ? <p>未保存の編集内容は保持しています。</p> : null}
        </div>
      </section> : null}
      {/* Keep the same RHF instance and its entered values during authority rechecks. */}
      <div hidden={!isAdmin}>
      <fieldset disabled={roleLoading || !isAdmin} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
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
          onClick={() => chooseEditor(emptyUser())}
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
          <EditForm<User> key={editorEpoch} label={editing.id ? "ユーザーを編集" : "新しいユーザー"}
            defaultValues={editing} onSave={save} beginOperation={() => {
              const own = beginOperation();
              formLease.current = own;
              return own;
            }} onDirtyChange={setDirty} onValuesChange={values => {
              const value = JSON.stringify(values);
              if (snapshot.current && snapshot.current !== value) ++inputGeneration.current;
              snapshot.current = value;
              dirtyRef.current = value !== JSON.stringify(editing);
            }} classifyError={error => ({ kind: "error", message: errorMessage(error, "ユーザーを保存できませんでした") })}>
            <UserFields />
          </EditForm>
          <Button className="sawa-button admin-ordinary-control" type="button" onClick={() => chooseEditor(null)}>キャンセル</Button>
        </section>
      ) : null}

      <section className="sawa-card admin-user-list" aria-labelledby="user-list-title">
        <div className="sawa-card__header">
          <div>
            <h2 id="user-list-title">ユーザー一覧</h2>
            <p>{listLoading ? "読み込み中です。" : `${items.length}件のユーザー`}</p>
          </div>
          <Button className="sawa-button admin-ordinary-control" type="button" disabled={listLoading} onClick={() => { sortAfterSave.current = false; void users.refetch(); }}>
            {listLoading ? "更新中…" : "一覧を更新"}
          </Button>
        </div>
        {listLoading && items.length === 0 ? <div className="sawa-empty">ユーザー一覧を読み込んでいます。</div> : null}
        <DataTable<User> label="ユーザー一覧" rows={items.slice(Math.min(page, Math.max(0, Math.ceil(items.length / pageSize) - 1)) * pageSize,
          (Math.min(page, Math.max(0, Math.ceil(items.length / pageSize) - 1)) + 1) * pageSize)} columns={[
          { id: "account", label: "メールアドレス", render: user => <strong>{user.account}</strong> },
          { id: "role", label: "権限", render: user => user.role === "admin" ? "管理者" : "一般利用者" },
          { id: "status", label: "状態", render: user => user.status === "active" ? "有効" : "停止" },
          { id: "systems", label: "利用システム", render: user => user.systems.map(system => isKnownSystem(system) ? systemLabels[system] : system).join(" / ") || "なし" },
          { id: "edit", label: "操作", render: user => <Button className="sawa-button admin-ordinary-control" onClick={() => chooseEditor(user)}>編集</Button> },
        ]} rowKey={user => user.id} total={items.length} page={Math.min(page, Math.max(0, Math.ceil(items.length / pageSize) - 1))} rowsPerPage={pageSize}
          onPageChange={setPage} onRowsPerPageChange={value => { setPageSize(value); setPage(0); }} pending={listLoading}
          error={users.error instanceof UsersReadError && users.error.current() ? users.error.message : undefined}
          onRetry={() => void users.refetch()} />
      </section>
      </fieldset>
      </div>

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
  );
}

export default function CommonUsersPage() {
  const session = useSyncExternalStore(subscribeBrowserSession, browserSessionKey, () => "server");
  return <SawaProvider sessionKey={`hospital-admin-users:${session}`}><UsersContent key={session} session={session} /></SawaProvider>;
}
