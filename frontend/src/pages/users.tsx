import { useEffect, useMemo, useState } from "react";

import TopNav from "../components/TopNav";
import { apiClient } from "../services/apiClient";

type UserItem = {
  id: string;
  account: string;
  role: "admin" | "operator" | string;
  status: "active" | "inactive" | string;
  created_at?: string | null;
};

const ROLE_OPTIONS = ["operator", "admin"];
const STATUS_OPTIONS = ["active", "inactive"];

export default function UsersPage() {
  const [items, setItems] = useState<UserItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [savingId, setSavingId] = useState<string>("");
  const [message, setMessage] = useState("");

  const [account, setAccount] = useState("");
  const [role, setRole] = useState("operator");

  const loadUsers = async () => {
    setLoading(true);
    setMessage("");
    try {
      const res = await apiClient.get("/users");
      setItems((res.data?.items || []) as UserItem[]);
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setMessage(detail ? `取得に失敗しました: ${detail}` : "取得に失敗しました。");
      setItems([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadUsers();
  }, []);

  const addUser = async () => {
    setMessage("");
    if (!account.trim()) {
      setMessage("メールアドレスを入力してください。");
      return;
    }
    try {
      const res = await apiClient.post("/users", {
        account: account.trim(),
        role,
        status: "active",
      });
      const created = !!res.data?.created;
      setMessage(created ? "ユーザーを登録しました。" : "既存ユーザーを更新しました。");
      setAccount("");
      setRole("operator");
      await loadUsers();
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setMessage(detail ? `登録に失敗しました: ${detail}` : "登録に失敗しました。");
    }
  };

  const updateItem = (id: string, field: "role" | "status", value: string) => {
    setItems((prev) =>
      prev.map((item) => {
        if (item.id !== id) return item;
        return { ...item, [field]: value };
      })
    );
  };

  const saveItem = async (item: UserItem) => {
    setSavingId(item.id);
    setMessage("");
    try {
      await apiClient.put(`/users/${item.id}`, {
        role: item.role,
        status: item.status,
      });
      setMessage("更新しました。");
      await loadUsers();
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setMessage(detail ? `更新に失敗しました: ${detail}` : "更新に失敗しました。");
    } finally {
      setSavingId("");
    }
  };

  const sortedItems = useMemo(
    () =>
      [...items].sort((a, b) => {
        if ((a.status || "") !== (b.status || "")) {
          return (a.status || "").localeCompare(b.status || "");
        }
        return (a.account || "").localeCompare(b.account || "");
      }),
    [items]
  );

  return (
    <main className="page">
      <header className="hero">
        <div>
          <p className="eyebrow">Users</p>
          <h1>ユーザー管理</h1>
          <p className="subtle">Googleログインを許可するメールアドレスを管理します。</p>
        </div>
        <TopNav />
      </header>

      <section className="panel">
        <header className="panel-header">
          <h2>新規登録</h2>
        </header>
        <div className="form-grid">
          <label className="field">
            <span className="field-label">メールアドレス</span>
            <input
              className="input"
              value={account}
              onChange={(e) => setAccount(e.target.value)}
              placeholder="addonmeal2023@gmail.com"
            />
          </label>
          <label className="field">
            <span className="field-label">権限</span>
            <select className="input" value={role} onChange={(e) => setRole(e.target.value)}>
              {ROLE_OPTIONS.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </label>
          <button className="btn primary" onClick={addUser}>
            登録
          </button>
          <button className="btn" onClick={loadUsers} disabled={loading}>
            {loading ? "読込中..." : "再読込"}
          </button>
        </div>
        {message ? <p className="message">{message}</p> : null}
      </section>

      <section className="panel">
        <header className="panel-header">
          <h2>一覧</h2>
          <span className="badge">合計 {sortedItems.length} 件</span>
        </header>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>メールアドレス</th>
                <th>権限</th>
                <th>状態</th>
                <th>登録日時</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {sortedItems.map((item) => (
                <tr key={item.id}>
                  <td>{item.account}</td>
                  <td>
                    <select
                      className="input"
                      value={item.role}
                      onChange={(e) => updateItem(item.id, "role", e.target.value)}
                    >
                      {ROLE_OPTIONS.map((option) => (
                        <option key={option} value={option}>
                          {option}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <select
                      className="input"
                      value={item.status}
                      onChange={(e) => updateItem(item.id, "status", e.target.value)}
                    >
                      {STATUS_OPTIONS.map((option) => (
                        <option key={option} value={option}>
                          {option}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>{item.created_at ? new Date(item.created_at).toLocaleString() : "-"}</td>
                  <td>
                    <button
                      className="btn"
                      onClick={() => saveItem(item)}
                      disabled={savingId === item.id}
                    >
                      {savingId === item.id ? "保存中..." : "保存"}
                    </button>
                  </td>
                </tr>
              ))}
              {!sortedItems.length ? (
                <tr>
                  <td colSpan={5} className="empty">
                    ユーザーがありません。
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
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

        .eyebrow {
          letter-spacing: 0.12em;
          text-transform: uppercase;
          font-size: 12px;
          color: #5f7b74;
          margin-bottom: 8px;
        }

        h1 {
          font-size: clamp(26px, 4vw, 36px);
          margin: 0 0 12px;
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
          margin-bottom: 16px;
        }

        h2 {
          font-size: 18px;
          margin: 0;
        }

        .badge {
          font-size: 12px;
          background: #ecf4f2;
          color: #35534b;
          border-radius: 999px;
          padding: 6px 12px;
        }

        .form-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
          gap: 12px;
          align-items: end;
        }

        .field {
          display: flex;
          flex-direction: column;
          gap: 6px;
        }

        .field-label {
          font-size: 12px;
          color: #4d625c;
        }

        .input {
          width: 100%;
          border: 1px solid #cbd7d3;
          border-radius: 10px;
          padding: 8px 10px;
          background: #fff;
        }

        .btn {
          border: 1px solid #334540;
          background: #ffffff;
          color: #243430;
          border-radius: 999px;
          padding: 9px 16px;
          font-weight: 600;
          cursor: pointer;
        }

        .btn.primary {
          background: #243430;
          color: #f7f2e7;
          border-color: #243430;
        }

        .btn:disabled {
          opacity: 0.6;
          cursor: default;
        }

        .message {
          margin: 12px 0 0;
          color: #a03636;
        }

        .table-wrap {
          overflow-x: auto;
        }

        table {
          width: 100%;
          border-collapse: collapse;
          min-width: 720px;
        }

        th,
        td {
          text-align: left;
          border-bottom: 1px solid #e4ece9;
          padding: 10px 8px;
          vertical-align: middle;
        }

        th {
          font-size: 12px;
          color: #51615c;
          text-transform: uppercase;
          letter-spacing: 0.04em;
        }

        .empty {
          text-align: center;
          color: #5f706b;
          padding: 20px;
        }
      `}</style>
    </main>
  );
}
