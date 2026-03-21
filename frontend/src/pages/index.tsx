import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { apiClient } from "../services/apiClient";
import TopNav from "../components/TopNav";
import {
  fetchFacilityNameMap,
  fetchOrderFacilityCandidates,
  pickBestFacilityCandidate,
  type FacilityHint,
  type FacilityNameMap,
} from "../services/facilityData";

type Order = {
  id?: string;
  status: string;
  facility?: string | null;
  week?: string | null;
  received_at?: string | null;
};

type MenuInfo = {
  status: "登録済み" | "未登録" | "確認中";
  itemCount?: number;
  filename?: string | null;
  displayName?: string | null;
};

type SystemStatus = {
  intake?: {
    mode?: string | null;
    manual_upload_enabled?: boolean;
    manual_upload_storage?: {
      configured?: boolean;
      mode?: string | null;
      persisted?: boolean;
      bucket?: string | null;
    } | null;
  };
  oauth_config?: {
    configured?: boolean;
  };
  ocr_pipeline?: {
    status?: string | null;
    updated_at?: string | null;
    last_success_at?: string | null;
    last_error_at?: string | null;
    last_error?: string | null;
    configured?: boolean;
    url_set?: boolean;
    bucket_set?: boolean;
    inflight?: number | null;
    max_inflight?: number | null;
  };
};

type ShippingTodayItem = {
  id: string;
  tracking_number: string;
  facility_name?: string | null;
  status: string;
  delivered: boolean;
  arrival_text?: string | null;
  error?: string | null;
  looked_up_at?: string | null;
};

type ShippingTodayPayload = {
  date?: string;
  summary?: {
    total: number;
    delivered: number;
    pending: number;
    errors: number;
    all_delivered: boolean;
  };
  quota?: {
    resource?: string;
    unit?: string;
    used?: number;
    limit?: number;
    ratio?: number | null;
    alert_level?: "ok" | "warning" | "critical" | "unknown" | string;
    message?: string;
  };
  items?: ShippingTodayItem[];
};

const STATUS_KEYS = ["未着", "要確認", "確定", "エラー"] as const;

const buildMonthId = () => {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
};

const formatDate = (value?: string | null) => {
  if (!value) return "未取得";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "未取得";
  return date.toLocaleString("ja-JP", { timeZone: "Asia/Tokyo" });
};

const formatSystemStatus = (value?: string | null) => {
  const raw = (value || "").toLowerCase();
  if (!raw) return "未取得";
  if (raw === "ok") return "OK";
  if (raw === "error") return "エラー";
  if (raw === "expired") return "期限切れ";
  if (raw === "misconfigured") return "未設定";
  if (raw === "running") return "実行中";
  return value || "未取得";
};

export default function HomePage() {
  const [orders, setOrders] = useState<Order[]>([]);
  const [menuInfo, setMenuInfo] = useState<Record<string, MenuInfo>>({});
  const [error, setError] = useState("");
  const [ordersLoading, setOrdersLoading] = useState(false);
  const [systemStatus, setSystemStatus] = useState<SystemStatus | null>(null);
  const [shippingToday, setShippingToday] = useState<ShippingTodayPayload | null>(null);
  const [facilityNameMap, setFacilityNameMap] = useState<FacilityNameMap>({});
  const [facilityHints, setFacilityHints] = useState<Record<string, FacilityHint>>({});

  useEffect(() => {
    let cancelled = false;
    const loadOrders = async () => {
      setOrdersLoading(true);
      const maxAttempts = 2;
      for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
        try {
          const res = await apiClient.get("/orders", { params: { include_ocr: false } });
          if (cancelled) return;
          setOrders(res.data.orders || []);
          setError("");
          setOrdersLoading(false);
          return;
        } catch {
          if (attempt < maxAttempts) {
            await new Promise((resolve) => window.setTimeout(resolve, 800));
            if (cancelled) return;
            continue;
          }
          if (cancelled) return;
          setError("注文一覧の取得に一時失敗しました。再試行してください。");
        }
      }
      if (!cancelled) {
        setOrdersLoading(false);
      }
    };
    void loadOrders();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    apiClient
      .get("/system/status")
      .then((res) => {
        setSystemStatus(res.data || null);
      })
      .catch(() => {
        setSystemStatus(null);
      });
  }, []);

  useEffect(() => {
    apiClient
      .get("/shipping/status/today", { params: { limit: 12 } })
      .then((res) => {
        setShippingToday(res.data || null);
      })
      .catch(() => {
        setShippingToday(null);
      });
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetchFacilityNameMap()
      .then((map) => {
        if (!cancelled) setFacilityNameMap(map);
      })
      .catch(() => {
        if (!cancelled) setFacilityNameMap({});
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const stats = useMemo(() => {
    const counts: Record<string, number> = {};
    STATUS_KEYS.forEach((key) => {
      counts[key] = 0;
    });
    let unresolved = 0;
    let latestReceived = "";
    orders.forEach((order) => {
      if (counts[order.status] != null) {
        counts[order.status] += 1;
      }
      if (!order.facility) {
        unresolved += 1;
      }
      if (order.received_at && (!latestReceived || order.received_at > latestReceived)) {
        latestReceived = order.received_at;
      }
    });
    return {
      counts,
      unresolved,
      latestReceived,
      total: orders.length,
    };
  }, [orders]);

  const weekRows = useMemo(() => {
    const map = new Map<
      string,
      { week: string; counts: Record<string, number>; unresolved: number }
    >();
    orders.forEach((order) => {
      const week = order.week || "未確定";
      const current =
        map.get(week) || {
          week,
          counts: { 未着: 0, 要確認: 0, 確定: 0, エラー: 0 },
          unresolved: 0,
        };
      if (current.counts[order.status] != null) {
        current.counts[order.status] += 1;
      }
      if (!order.facility) {
        current.unresolved += 1;
      }
      map.set(week, current);
    });
    return Array.from(map.values()).sort((a, b) => a.week.localeCompare(b.week));
  }, [orders]);

  const weekList = useMemo(() => {
    const weeks = new Set<string>();
    orders.forEach((order) => {
      if (order.week) {
        weeks.add(order.week);
      }
    });
    return Array.from(weeks).sort();
  }, [orders]);

  useEffect(() => {
    if (weekList.length === 0) {
      setMenuInfo({});
      return;
    }
    let cancelled = false;
    const baseInfo: Record<string, MenuInfo> = {};
    weekList.forEach((week) => {
      baseInfo[week] = { status: "確認中" };
    });
    setMenuInfo(baseInfo);
    const loadMenus = async () => {
      const entries = await Promise.all(
        weekList.map(async (week) => {
          try {
            const res = await apiClient.get(`/monthly-menus/${week}`);
            const items = res.data?.items || [];
            return [
              week,
              {
                status: "登録済み",
                itemCount: items.length,
                filename: res.data?.menu?.filename || null,
                displayName: res.data?.menu?.display_name || res.data?.menu?.filename || null,
              },
            ] as const;
          } catch {
            return [week, { status: "未登録" }] as const;
          }
        })
      );
      if (cancelled) return;
      const next: Record<string, MenuInfo> = {};
      entries.forEach(([week, info]) => {
        next[week] = info;
      });
      setMenuInfo(next);
    };
    loadMenus();
    return () => {
      cancelled = true;
    };
  }, [weekList.join("|")]);

  const pendingOrders = useMemo(() => {
    return orders.filter((order) => order.status === "要確認" || !order.facility).slice(0, 8);
  }, [orders]);

  useEffect(() => {
    let cancelled = false;
    const unresolved = pendingOrders
      .filter((order) => !order.facility && order.id)
      .map((order) => String(order.id || ""))
      .filter((orderId) => orderId && !facilityHints[orderId]);

    if (unresolved.length === 0) return;

    const queue = [...unresolved];
    const results: Record<string, FacilityHint> = {};

    const workers = Array.from({ length: 2 }, async () => {
      while (queue.length > 0) {
        const orderId = queue.shift();
        if (!orderId) continue;
        try {
          const candidates = await fetchOrderFacilityCandidates(orderId);
          const best = pickBestFacilityCandidate(candidates);
          if (best) {
            results[orderId] = { ...best, order_id: orderId };
          }
        } catch {
          // ignore
        }
      }
    });

    Promise.all(workers).then(() => {
      if (cancelled) return;
      if (Object.keys(results).length === 0) return;
      setFacilityHints((prev) => ({ ...prev, ...results }));
    });

    return () => {
      cancelled = true;
    };
  }, [pendingOrders, facilityHints]);

  const facilityLabel = (order: Order) => {
    const facilityId = order.facility || "";
    if (facilityId) {
      const name = facilityNameMap[facilityId];
      return name ? `${name} (${facilityId})` : facilityId;
    }
    const orderId = order.id || "";
    const hint = orderId ? facilityHints[orderId] : null;
    if (hint?.facility_name) {
      const score = hint.score != null ? ` / score=${hint.score}` : "";
      return `推定: ${hint.facility_name} (${hint.facility_id}${score})`;
    }
    return "未確定";
  };

  return (
    <main className="dashboard">
      <header className="hero">
        <div>
          <p className="eyebrow">Hospital Order Dashboard</p>
          <h1>月次の進捗と未確定を一画面で確認</h1>
          <p className="subtle">
            施設×月の状態、未確定申請、OCR/取込状況をまとめて把握できます。
          </p>
        </div>
        <TopNav />
      </header>

      {error && (
        <div className="error-banner">
          <p className="error">{error}</p>
          <button
            type="button"
            className="retry-button"
            onClick={() => window.location.reload()}
            disabled={ordersLoading}
          >
            {ordersLoading ? "再試行中..." : "再読み込み"}
          </button>
        </div>
      )}

      <section className="role-grid">
        <article className="role-card">
          <p className="role-label">注文系</p>
          <h2>注文確認と確定</h2>
          <ol>
            <li>注文一覧で「要確認」を開く</li>
            <li>注文詳細でシートを確認して確定する</li>
            <li>日別出力で袋分け・ラベル・納品書を確認する</li>
          </ol>
          <div className="role-actions">
            <Link href="/orders?status=要確認" className="mini-link">
              注文一覧へ
            </Link>
            <Link href="/daily-delivery-notes" className="mini-link">
              日別出力へ
            </Link>
          </div>
        </article>
        <article className="role-card">
          <p className="role-label">メニュー・施設系</p>
          <h2>注文書作成と取り込み</h2>
          <ol>
            <li>月次メニューや注文書を作る</li>
            <li>施設から届いたPDFをアップロードする</li>
            <li>注文一覧で取り込み結果を確認する</li>
          </ol>
          <div className="role-actions">
            <Link href="/pdf-upload" className="mini-link">
              注文書アップロードへ
            </Link>
            <Link href={`/menus/${buildMonthId()}`} className="mini-link">
              月次メニューへ
            </Link>
          </div>
        </article>
      </section>

      <section className="summary-strip">
        <article className="summary-tile tone-new" style={{ animationDelay: "40ms" }}>
          <p className="summary-label">未着</p>
          <p className="summary-value">{stats.counts["未着"]}</p>
        </article>
        <article className="summary-tile tone-warn" style={{ animationDelay: "80ms" }}>
          <p className="summary-label">未確定申請</p>
          <p className="summary-value">{stats.counts["要確認"]}</p>
        </article>
        <article className="summary-tile tone-ok" style={{ animationDelay: "120ms" }}>
          <p className="summary-label">確定</p>
          <p className="summary-value">{stats.counts["確定"]}</p>
        </article>
        <article className="summary-tile tone-error" style={{ animationDelay: "160ms" }}>
          <p className="summary-label">エラー</p>
          <p className="summary-value">{stats.counts["エラー"]}</p>
        </article>
        <article className="summary-tile tone-muted" style={{ animationDelay: "200ms" }}>
          <p className="summary-label">施設未確定</p>
          <p className="summary-value">{stats.unresolved}</p>
        </article>
        <article className="summary-tile summary-tile-wide" style={{ animationDelay: "240ms" }}>
          <p className="summary-label">OCR / 取込状況</p>
          <div className="summary-inline">
            <span>最新取込: {formatDate(stats.latestReceived)}</span>
            <span>総注文数: {stats.total}</span>
            <span>エラー件数: {stats.counts["エラー"]}</span>
          </div>
        </article>
      </section>

      <section className="columns">
        <article className="panel system-panel">
          <header className="panel-header">
            <h2>システム状態</h2>
            <span className="badge">
              {systemStatus?.intake?.mode === "manual_upload" ? "PDF取込" : "自動取込"}
            </span>
            <Link href="/system-status" className="ghost-link">
              管理画面
            </Link>
          </header>
          <div className="system-grid">
            <div className="system-card">
              <p className="system-label">取込方式</p>
              <p className="system-value">
                {systemStatus?.intake?.mode === "manual_upload" ? "注文書アップロード" : "未取得"}
              </p>
              <p className="system-meta">
                保存先: {systemStatus?.intake?.manual_upload_storage?.mode ?? "未取得"}
              </p>
              <p className="system-meta">
                永続化: {systemStatus?.intake?.manual_upload_storage?.persisted ? "あり" : "なし"}
              </p>
              {systemStatus?.intake?.manual_upload_enabled &&
                !systemStatus?.intake?.manual_upload_storage?.configured && (
                <p className="system-meta warn">
                  注文書アップロード保存先を確認してください
                </p>
              )}
            </div>
            <div className="system-card">
              <p className="system-label">Google 認証</p>
              <p className="system-value">
                {systemStatus?.oauth_config?.configured ? "OK" : "未設定"}
              </p>
              <p className="system-meta">
                ログイン不具合時はOAuth Client ID / Origin / Redirect設定を確認
              </p>
            </div>
            <div className="system-card">
              <p className="system-label">OCRパイプライン</p>
              <p className="system-value">
                {systemStatus?.ocr_pipeline?.configured
                  ? formatSystemStatus(systemStatus?.ocr_pipeline?.status)
                  : "未設定"}
              </p>
              <p className="system-meta">
                最終成功: {formatDate(systemStatus?.ocr_pipeline?.last_success_at)}
              </p>
              <p className="system-meta">
                稼働中:{" "}
                {systemStatus?.ocr_pipeline?.inflight != null &&
                systemStatus?.ocr_pipeline?.max_inflight != null
                  ? `${systemStatus.ocr_pipeline.inflight}/${systemStatus.ocr_pipeline.max_inflight}`
                  : "未取得"}
              </p>
              {systemStatus?.ocr_pipeline?.last_error && (
                <p className="system-meta warn">
                  エラー: {systemStatus.ocr_pipeline.last_error}
                </p>
              )}
            </div>
          </div>
        </article>

        <article className="panel">
          <header className="panel-header">
            <h2>月ごとの進捗表</h2>
            <Link href="/orders" className="ghost-link">
              注文一覧へ
            </Link>
          </header>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>月ID</th>
                  <th>未着</th>
                  <th>未確定</th>
                  <th>確定</th>
                  <th>エラー</th>
                  <th>施設未確定</th>
                </tr>
              </thead>
              <tbody>
                {weekRows.length === 0 ? (
                  <tr>
                    <td colSpan={6}>データがありません。</td>
                  </tr>
                ) : (
                  weekRows.map((row) => (
                    <tr key={row.week}>
                      <td>{row.week}</td>
                      <td>{row.counts["未着"]}</td>
                      <td>{row.counts["要確認"]}</td>
                      <td>{row.counts["確定"]}</td>
                      <td>{row.counts["エラー"]}</td>
                      <td>{row.unresolved}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </article>

        <article className="panel">
          <header className="panel-header">
            <h2>未確定申請</h2>
            <span className="badge">{stats.counts["要確認"]}</span>
          </header>
          <div className="pending-list">
            {pendingOrders.length === 0 ? (
              <p className="subtle">未確定申請はありません。</p>
            ) : (
              pendingOrders.map((order) => (
                <Link key={order.id} href={`/orders/${order.id}`} className="pending-item">
                  <div>
                    <p className="pending-title">{order.id}</p>
                    <p className="pending-meta">
                      施設: {facilityLabel(order)} / 月: {order.week || "未確定"}
                    </p>
                  </div>
                  <span className="status-tag">{order.status}</span>
                </Link>
              ))
            )}
          </div>
        </article>

        <article className="panel">
          <header className="panel-header">
            <h2>佐川追跡（本日）</h2>
            <Link href="/shipping-history" className="ghost-link">
              履歴を見る
            </Link>
          </header>
          {shippingToday?.summary ? (
            <div className="shipping-summary">
              <p>総件数: {shippingToday.summary.total}</p>
              <p>配達完了: {shippingToday.summary.delivered}</p>
              <p>未完了: {shippingToday.summary.pending}</p>
              <p>照会失敗: {shippingToday.summary.errors}</p>
            </div>
          ) : (
            <p className="subtle">本日の追跡データはありません。</p>
          )}
          <div className="shipping-list">
            {shippingToday?.quota &&
            (shippingToday.quota.alert_level === "warning" ||
              shippingToday.quota.alert_level === "critical") ? (
              <div className={`shipping-quota quota-${shippingToday.quota.alert_level}`}>
                <p className="pending-title">Quotaアラート: {shippingToday.quota.alert_level}</p>
                <p className="pending-meta">
                  使用量: {shippingToday.quota.used ?? "-"} / 上限: {shippingToday.quota.limit ?? "-"}
                </p>
                <p className="pending-meta">{shippingToday.quota.message || "quotaが上限に近づいています。"}</p>
              </div>
            ) : null}
            {(shippingToday?.items || []).length === 0 ? (
              <p className="subtle">表示できる伝票がありません。</p>
            ) : (
              (shippingToday?.items || []).map((item) => (
                <Link key={item.id} href="/shipping-history" className="shipping-item">
                  <div>
                    <p className="pending-title">{item.tracking_number}</p>
                    <p className="pending-meta">
                      施設: {item.facility_name || "未設定"} / 状態: {item.status}
                    </p>
                    <p className="pending-meta">
                      到着: {item.arrival_text || "未取得"} / 更新: {formatDate(item.looked_up_at)}
                    </p>
                    {item.error ? <p className="pending-meta warn">エラー: {item.error}</p> : null}
                  </div>
                  <span className="status-tag">{item.delivered ? "完了" : "未完了"}</span>
                </Link>
              ))
            )}
          </div>
        </article>
      </section>

      <section className="menu-section">
        <header className="panel-header">
          <h2>月ごとのメニュー</h2>
          <Link href="/orders" className="ghost-link">
            月を確認する
          </Link>
        </header>
        <div className="menu-grid">
          {weekList.length === 0 ? (
            <p className="subtle">月次メニューの対象月がありません。</p>
          ) : (
            weekList.map((week) => {
              const info = menuInfo[week];
              const status = info?.status || "未登録";
              const statusClass =
                status === "登録済み" ? "status-ok" : status === "未登録" ? "status-missing" : "status-pending";
              return (
                <Link key={week} href={`/menus/${week}`} className="menu-card">
                  <div>
                    <p className="menu-week">{week}</p>
                    <p className="menu-meta">
                      {status === "登録済み" ? `登録済み (${info?.itemCount ?? 0}件)` : status}
                    </p>
                    {info?.displayName && <p className="menu-file">{info.displayName}</p>}
                  </div>
                  <span className={`menu-status ${statusClass}`}>{status}</span>
                </Link>
              );
            })
          )}
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

        .dashboard {
          min-height: 100vh;
          padding: 48px 6vw 80px;
        }

        :global(a) {
          color: inherit;
          text-decoration: none;
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
          font-size: clamp(28px, 4vw, 40px);
          margin: 0 0 12px;
        }

        .subtle {
          color: #51615c;
          margin: 0;
        }
        .role-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
          gap: 18px;
          margin-bottom: 24px;
        }
        .role-card {
          background: #ffffff;
          border-radius: 18px;
          padding: 20px;
          box-shadow: 0 10px 30px rgba(27, 35, 33, 0.08);
          border: 1px solid rgba(25, 32, 30, 0.1);
        }
        .role-card h2 {
          margin: 0 0 12px;
          font-size: 20px;
        }
        .role-label {
          margin: 0 0 8px;
          letter-spacing: 0.08em;
          text-transform: uppercase;
          color: #7b5c25;
          font-size: 12px;
          font-weight: 800;
        }
        .role-card ol {
          margin: 0;
          padding-left: 20px;
          color: #374240;
          line-height: 1.7;
        }
        .role-actions {
          display: flex;
          gap: 10px;
          flex-wrap: wrap;
          margin-top: 16px;
        }
        .mini-link {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          min-height: 38px;
          padding: 0 14px;
          border-radius: 999px;
          background: #f6f1e6;
          color: #1f2a2a;
          font-weight: 700;
          text-decoration: none;
        }

        .nav {
          display: flex;
          gap: 12px;
        }

        .nav-link {
          padding: 10px 18px;
          border-radius: 999px;
          background: #1f2a2a;
          color: #f7f2e7;
          font-weight: 600;
          transition: transform 0.2s ease;
        }

        .nav-link:hover {
          transform: translateY(-2px);
        }

        .summary-strip {
          display: grid;
          gap: 12px;
          grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
          margin-bottom: 28px;
        }

        .summary-tile {
          background: #ffffff;
          border-radius: 14px;
          padding: 14px 16px;
          box-shadow: 0 8px 18px rgba(27, 35, 33, 0.05);
          border: 1px solid rgba(25, 32, 30, 0.08);
          animation: rise 0.6s ease both;
          position: relative;
          border-top: 3px solid var(--tone, #c7d2ce);
        }

        .summary-tile::after {
          content: "";
          position: absolute;
          top: 12px;
          right: 12px;
          width: 7px;
          height: 7px;
          border-radius: 999px;
          background: var(--tone, #c7d2ce);
          box-shadow: 0 0 0 4px var(--tone-bg, #f2f4f3);
        }

        .tone-new {
          --tone: #c7a86b;
          --tone-bg: #f3ede1;
        }

        .tone-warn {
          --tone: #d69e2e;
          --tone-bg: #f8edd6;
        }

        .tone-ok {
          --tone: #3aa66b;
          --tone-bg: #e7f4ec;
        }

        .tone-error {
          --tone: #c04343;
          --tone-bg: #f5e3e3;
        }

        .tone-muted {
          --tone: #667a74;
          --tone-bg: #e8eceb;
        }

        .summary-label {
          margin: 0 0 8px;
          font-size: 11px;
          letter-spacing: 0.08em;
          text-transform: uppercase;
          color: inherit;
          opacity: 0.7;
        }

        .summary-value {
          font-size: 24px;
          font-weight: 700;
          margin: 0;
        }

        .summary-tile-wide {
          grid-column: span 2;
        }

        .summary-inline {
          display: flex;
          flex-wrap: wrap;
          gap: 10px 16px;
          color: #41514d;
          font-size: 13px;
          line-height: 1.5;
        }

        .mini-label {
          font-size: 11px;
          letter-spacing: 0.08em;
          text-transform: uppercase;
          margin: 0 0 6px;
          color: inherit;
          opacity: 0.7;
        }

        .mini-value {
          margin: 0;
          font-weight: 600;
        }

        .columns {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
          gap: 20px;
        }

        .panel {
          background: #ffffff;
          border-radius: 18px;
          padding: 20px;
          border: 1px solid rgba(25, 32, 30, 0.06);
          box-shadow: 0 12px 26px rgba(27, 35, 33, 0.06);
          animation: rise 0.6s ease both;
        }

        .system-panel {
          grid-column: 1 / -1;
        }

        .system-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
          gap: 12px;
          margin-top: 12px;
        }

        .system-recovery {
          margin-top: 16px;
          border-radius: 16px;
          padding: 16px;
          border: 1px solid rgba(31, 42, 42, 0.12);
          background: #fbfbf9;
        }

        .system-recovery h3 {
          margin: 0 0 10px;
          font-size: 14px;
        }

        .system-card {
          border-radius: 14px;
          border: 1px solid rgba(31, 42, 42, 0.12);
          padding: 12px 14px;
          background: #f8f4ec;
        }

        .system-label {
          margin: 0 0 6px;
          font-size: 12px;
          letter-spacing: 0.1em;
          text-transform: uppercase;
          color: #5f7b74;
        }

        .system-value {
          margin: 0 0 6px;
          font-weight: 700;
          font-size: 16px;
        }

        .system-meta {
          margin: 0;
          font-size: 12px;
          color: #51615c;
        }

        .system-meta.warn {
          color: #b24500;
          font-weight: 600;
        }

        .system-steps {
          margin-top: 14px;
          font-size: 12px;
          display: flex;
          flex-direction: column;
          gap: 2px;
          color: #5a4d3b;
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

        .ghost-link {
          font-size: 13px;
          color: #5f7b74;
        }

        .table-wrap {
          overflow-x: auto;
        }

        table {
          width: 100%;
          border-collapse: collapse;
          font-size: 14px;
        }

        th,
        td {
          padding: 10px;
          text-align: left;
        }

        thead {
          background: #f4f1ea;
        }

        tbody tr:nth-child(even) {
          background: #faf9f5;
        }

        .pending-list {
          display: grid;
          gap: 12px;
        }

        .pending-item {
          display: flex;
          justify-content: space-between;
          align-items: center;
          padding: 12px 14px;
          border-radius: 12px;
          border: 1px solid rgba(25, 32, 30, 0.06);
          background: #fbfbf9;
          transition: transform 0.2s ease;
        }

        .shipping-summary {
          margin-bottom: 12px;
          padding: 10px 12px;
          border-radius: 10px;
          border: 1px solid rgba(25, 32, 30, 0.12);
          background: #fbf8ef;
          font-size: 13px;
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
          gap: 6px;
        }

        .shipping-list {
          display: grid;
          gap: 10px;
        }

        .shipping-item {
          display: flex;
          justify-content: space-between;
          align-items: flex-start;
          gap: 10px;
          padding: 12px 14px;
          border-radius: 12px;
          border: 1px solid rgba(25, 32, 30, 0.06);
          background: #fbfbf9;
        }

        .shipping-quota {
          padding: 10px 12px;
          border-radius: 12px;
          border: 1px solid rgba(25, 32, 30, 0.12);
          background: #f5f8f6;
        }

        .shipping-quota.quota-warning {
          background: #fff4df;
          border-color: rgba(173, 102, 0, 0.35);
        }

        .shipping-quota.quota-critical {
          background: #ffe7e7;
          border-color: rgba(170, 45, 45, 0.35);
        }

        .pending-item:hover {
          transform: translateY(-2px);
        }

        .pending-title {
          margin: 0 0 4px;
          font-weight: 600;
        }

        .pending-meta {
          margin: 0;
          font-size: 12px;
          color: #5f7b74;
        }

        .pending-meta.warn {
          color: #b24500;
        }

        .status-tag {
          background: #e6ebe9;
          padding: 4px 10px;
          border-radius: 999px;
          font-size: 12px;
        }

        .badge {
          background: #1f2a2a;
          color: #f7f2e7;
          padding: 4px 10px;
          border-radius: 999px;
          font-size: 12px;
        }

        .menu-section {
          margin-top: 28px;
          background: #ffffff;
          border-radius: 18px;
          padding: 20px;
          border: 1px solid rgba(25, 32, 30, 0.06);
          box-shadow: 0 12px 26px rgba(27, 35, 33, 0.06);
        }

        .menu-grid {
          display: grid;
          gap: 12px;
          grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        }

        .menu-card {
          display: flex;
          justify-content: space-between;
          gap: 12px;
          padding: 14px;
          border-radius: 14px;
          border: 1px solid rgba(25, 32, 30, 0.06);
          background: #fbfbf9;
          transition: transform 0.2s ease;
        }

        .menu-card:hover {
          transform: translateY(-2px);
        }

        .menu-week {
          margin: 0 0 6px;
          font-weight: 600;
        }

        .menu-meta {
          margin: 0;
          font-size: 12px;
          color: #5f7b74;
        }

        .menu-file {
          margin: 6px 0 0;
          font-size: 11px;
          color: #8a9d97;
        }

        .menu-status {
          align-self: flex-start;
          padding: 4px 10px;
          border-radius: 999px;
          font-size: 11px;
          background: #e6ebe9;
        }

        .status-ok {
          background: #d7ece4;
          color: #1f5b46;
        }

        .status-missing {
          background: #f5d9d7;
          color: #7a2d2d;
        }

        .status-pending {
          background: #ece5d7;
          color: #7a5a2d;
        }

        .error-banner {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          margin-bottom: 16px;
          flex-wrap: wrap;
        }

        .error {
          background: #ffe3e3;
          padding: 10px 14px;
          border-radius: 12px;
          margin: 0;
          flex: 1 1 320px;
        }

        .retry-button {
          border: none;
          border-radius: 999px;
          min-height: 40px;
          padding: 0 16px;
          background: #1f2a2a;
          color: #f7f2e7;
          font-weight: 700;
          cursor: pointer;
        }

        .retry-button:disabled {
          opacity: 0.6;
          cursor: progress;
        }

        @keyframes rise {
          from {
            opacity: 0;
            transform: translateY(10px);
          }
          to {
            opacity: 1;
            transform: translateY(0);
          }
        }

        @media (max-width: 720px) {
          .card.wide {
            grid-column: span 1;
          }

          .nav {
            width: 100%;
            justify-content: flex-start;
          }
        }
      `}</style>
      <style jsx global>{`
        @import url("https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;700&family=Noto+Sans+JP:wght@400;600&display=swap");
      `}</style>
    </main>
  );
}
