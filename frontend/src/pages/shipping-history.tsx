import { useEffect, useMemo, useState } from "react";
import Link from "next/link";

import TopNav from "../components/TopNav";
import { useCurrentUserRole } from "../hooks/useCurrentUserRole";
import { apiClient } from "../services/apiClient";
import {
  describeAttentionReasons,
  type ShippingHistoryEvent,
  normalizeLatestResponse,
  type QuotaStatus,
  type ShippingHistorySummary,
  type ShippingLatestResponse,
  type ShippingLatestView,
  type ShippingLatestItem,
} from "../features/shipping/shippingHistory";

type HistoryTab = ShippingLatestView | "logs";

type ShippingHistoryItem = {
  id: string;
  tracking_key: string;
  tracking_number: string;
  ship_date?: string | null;
  facility_name?: string | null;
  status: string;
  delivered: boolean;
  arrival_text?: string | null;
  error?: string | null;
  source?: string | null;
  looked_up_at?: string | null;
};

type TrackingNumberCard = ShippingLatestItem & {
  events: ShippingHistoryEvent[];
};

const toTrackingNumberCardList = (response: ShippingLatestResponse | null): TrackingNumberCard[] => {
  if (!response) return [];
  const flattened: ShippingLatestItem[] = [];
  response.date_groups.forEach((dateGroup) => {
    dateGroup.facilities.forEach((facility) => {
      facility.items.forEach((item) => {
        flattened.push({
          ...item,
          facility_name: item.facility_name || facility.facility_name || null,
          facility_name_source:
            item.facility_name_source || facility.facility_name_source || item.facility_name_source,
        });
      });
    });
  });

  const byTracking = new Map<string, TrackingNumberCard>();
  for (const item of flattened) {
    const trackingKey = item.tracking_key || item.tracking_number;
    if (!trackingKey) continue;
    const existing = byTracking.get(trackingKey);
    if (!existing || compareDateDesc(item.looked_up_at, existing.looked_up_at) < 0) {
      byTracking.set(trackingKey, {
        ...item,
        events: [...(item.events || [])].sort((left, right) => {
          return compareDateDesc(left.occurred_at, right.occurred_at);
        }),
      });
    }
  }

  return Array.from(byTracking.values()).sort((left, right) => {
    const byFacility =
      (left.facility_name || "").localeCompare(right.facility_name || "", "ja", { sensitivity: "base" });
    if (byFacility !== 0) return byFacility;
    const byStatus = Number(left.delivered) - Number(right.delivered);
    if (byStatus !== 0) return byStatus;
    return (left.tracking_key || left.tracking_number).localeCompare(
      right.tracking_key || right.tracking_number,
      "ja",
    );
  });
};

const extractFilename = (value?: string | null) => {
  if (!value) return "";
  const match = value.match(/filename\\*=UTF-8''([^;]+)|filename=\"?([^\";]+)\"?/i);
  const rawName = match?.[1] || match?.[2] || "";
  if (!rawName) return "";
  try {
    return decodeURIComponent(rawName);
  } catch {
    return rawName;
  }
};

const formatLocalDate = (value?: string | null) => {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ja-JP", { timeZone: "Asia/Tokyo" });
};

const formatCompactDateTime = (value?: string | null) => {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ja-JP", {
    timeZone: "Asia/Tokyo",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
};

const formatCompactEventTime = (value?: string | null) => {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ja-JP", {
    timeZone: "Asia/Tokyo",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
};

const formatDateOnly = (value?: string | null) => {
  if (!value) return "未設定";
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return toDateInput(date);
};

const formatRatio = (value?: number | null) => {
  if (value == null || Number.isNaN(value)) return "-";
  return `${(value * 100).toFixed(1)}%`;
};

const extractEventFacility = (event: ShippingHistoryEvent) => {
  return event.facility_name || event.facility || "-";
};

const normalizeSortDate = (value?: string | null) => {
  const timestamp = value ? Date.parse(value) : NaN;
  return Number.isFinite(timestamp) ? timestamp : NaN;
};

const compareDateDesc = (left?: string | null, right?: string | null) => {
  const leftTime = normalizeSortDate(left);
  const rightTime = normalizeSortDate(right);
  if (Number.isNaN(leftTime) && Number.isNaN(rightTime)) return 0;
  if (Number.isNaN(leftTime)) return 1;
  if (Number.isNaN(rightTime)) return -1;
  return rightTime - leftTime;
};

const toDateInput = (date: Date) => {
  const formatter = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Tokyo",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  const parts = formatter.formatToParts(date);
  const year = parts.find((item) => item.type === "year")?.value ?? "1970";
  const month = parts.find((item) => item.type === "month")?.value ?? "01";
  const day = parts.find((item) => item.type === "day")?.value ?? "01";
  return `${year}-${month}-${day}`;
};

const buildDefaultRange = () => {
  const end = new Date();
  const start = new Date(end.getTime() - 29 * 24 * 60 * 60 * 1000);
  return { start: toDateInput(start), end: toDateInput(end) };
};

const parseFacilityFilter = (value: string) =>
  value
    .split(/[,\n、]+/)
    .map((item) => item.trim())
    .filter(Boolean);

const buildLatestPath = (params: {
  view: ShippingLatestView;
  limit: number;
  baseDate: string;
  windowDays: number;
  facilityFilter: string;
  source: string;
  attentionStaleHours: number;
}) => {
  const search = new URLSearchParams();
  search.set("view", params.view);
  search.set("limit", String(Math.max(1, Math.min(params.limit, 1000))));
  search.set("base_date", params.baseDate);
  search.set("window_days", String(Math.max(0, Math.min(params.windowDays, 90))));
  if (params.source) search.set("source", params.source);
  if (params.view === "attention") {
    search.set("attention_stale_hours", String(Math.max(1, Math.min(params.attentionStaleHours, 336))));
  }
  parseFacilityFilter(params.facilityFilter).forEach((facilityName) => {
    search.append("facility_name", facilityName);
  });
  return `/shipping/status/latest?${search.toString()}`;
};

const buildHistoryPath = (params: { dateFrom: string; dateTo: string; limit: number }) => {
  const search = new URLSearchParams();
  search.set("limit", String(Math.max(1, Math.min(params.limit, 1000))));
  if (params.dateFrom) search.set("date_from", params.dateFrom);
  if (params.dateTo) search.set("date_to", params.dateTo);
  return `/shipping/status/history?${search.toString()}`;
};

const summarizeHistory = (summary: ShippingHistorySummary | null) => {
  if (!summary) return [];
  return [
    { label: "総件数", value: summary.total },
    { label: "有効中", value: summary.pending },
    { label: "配達完了", value: summary.delivered },
    { label: "照会失敗", value: summary.errors },
    { label: "要確認", value: summary.attention ?? 0 },
    { label: "施設未設定", value: summary.facility_missing ?? 0 },
  ];
};

const quotaTone = (quota?: QuotaStatus | null) => quota?.alert_level || "unknown";

export default function ShippingHistoryPage() {
  const { isAdmin } = useCurrentUserRole();
  const defaultRange = buildDefaultRange();
  const [activeTab, setActiveTab] = useState<HistoryTab>("active");
  const [limit, setLimit] = useState<number>(200);
  const [baseDate, setBaseDate] = useState<string>(defaultRange.end);
  const [windowDays, setWindowDays] = useState<number>(3);
  const [facilityFilter, setFacilityFilter] = useState<string>("");
  const [source, setSource] = useState<string>("");
  const [attentionStaleHours, setAttentionStaleHours] = useState<number>(24);
  const [dateFrom, setDateFrom] = useState<string>(defaultRange.start);
  const [dateTo, setDateTo] = useState<string>(defaultRange.end);
  const [loading, setLoading] = useState<boolean>(false);
  const [message, setMessage] = useState<string>("");
  const [latest, setLatest] = useState<ShippingLatestResponse | null>(null);
  const [historyItems, setHistoryItems] = useState<ShippingHistoryItem[]>([]);
  const [historySummary, setHistorySummary] = useState<ShippingHistorySummary | null>(null);
  const [historyQuota, setHistoryQuota] = useState<QuotaStatus | null>(null);
  const trackingCards = useMemo(() => toTrackingNumberCardList(latest), [latest]);

  const loadLatest = async (viewOverride?: ShippingLatestView) => {
    const view = viewOverride || (activeTab === "logs" ? "active" : activeTab);
    setLoading(true);
    setMessage("集約済みの追跡状況を取得中です...");
    try {
      const res = await apiClient.get(
        buildLatestPath({
          view,
          limit,
          baseDate,
          windowDays,
          facilityFilter,
          source,
          attentionStaleHours,
        }),
      );
      const normalized = normalizeLatestResponse(res.data, {
        view,
        base_date: baseDate,
        window_days: windowDays,
      });
      setLatest(normalized);
      setMessage(
        view === "recent"
          ? `基準日 ${baseDate} の±${normalized.window_days}日を取得しました。`
          : `集約済みの追跡状況を取得しました（${normalized.summary.total}件）。`,
      );
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setLatest(null);
      setMessage(detail ? `取得に失敗しました: ${detail}` : "取得に失敗しました。");
    } finally {
      setLoading(false);
    }
  };

  const loadHistory = async () => {
    setLoading(true);
    setMessage("監査ログを取得中です...");
    try {
      const res = await apiClient.get(buildHistoryPath({ dateFrom, dateTo, limit }));
      const nextItems = Array.isArray(res.data?.items) ? res.data.items : [];
      setHistoryItems(nextItems);
      setHistorySummary(res.data?.summary || null);
      setHistoryQuota(res.data?.quota || null);
      setMessage(`監査ログを取得しました（${nextItems.length}件）。`);
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setHistoryItems([]);
      setHistorySummary(null);
      setHistoryQuota(null);
      setMessage(detail ? `取得に失敗しました: ${detail}` : "取得に失敗しました。");
    } finally {
      setLoading(false);
    }
  };

  const downloadHistory = async (format: "csv" | "json") => {
    setMessage(`監査ログ${format.toUpperCase()}を作成中です...`);
    try {
      const res = await apiClient.get(
        `/shipping/status/export?${new URLSearchParams({
          format,
          limit: "1000000",
          ...(dateFrom ? { date_from: dateFrom } : {}),
          ...(dateTo ? { date_to: dateTo } : {}),
        }).toString()}`,
        { responseType: "blob" },
      );
      const contentDisposition =
        res.headers?.["content-disposition"] || res.headers?.["Content-Disposition"];
      const filename = extractFilename(contentDisposition) || `shipping_tracking_history.${format}`;
      const blob = res.data instanceof Blob ? res.data : new Blob([res.data]);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
      setMessage(`監査ログ${format.toUpperCase()}をダウンロードしました。`);
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setMessage(detail ? `ダウンロードに失敗しました: ${detail}` : "ダウンロードに失敗しました。");
    }
  };

  const clearAllHistory = async () => {
    const ok = window.confirm(
      "佐川追跡の監査ログを全件削除します。取り消せません。実行してよいですか？",
    );
    if (!ok) return;
    setLoading(true);
    setMessage("監査ログを削除中です...");
    try {
      const res = await apiClient.delete("/shipping/status/history");
      const removed = Number(res.data?.removed || 0);
      setMessage(`監査ログを削除しました（${removed}件）。`);
      await loadHistory();
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setMessage(detail ? `削除に失敗しました: ${detail}` : "削除に失敗しました。");
      setLoading(false);
    }
  };

  useEffect(() => {
    if (activeTab === "logs") {
      loadHistory();
      return;
    }
    loadLatest(activeTab);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab]);

  const currentSummary = activeTab === "logs" ? historySummary : latest?.summary || null;
  const currentQuota = activeTab === "logs" ? historyQuota : latest?.quota || null;

  return (
    <main className="page">
      <header className="hero">
        <div>
          <p className="eyebrow">Shipping Status</p>
          <h1>佐川出荷状況</h1>
          <p className="subtle">
            伝票番号ごとの最新状態を中心に表示し、履歴は「佐川履歴」があれば下部に表示します。
          </p>
        </div>
        <TopNav />
      </header>

      <section className="panel">
        <header className="panel-header">
          <div>
            <h2>表示モード</h2>
            <p className="panel-copy">既定は有効中です。監査ログは後段の補助タブです。</p>
          </div>
          <Link href="/shipping" className="ghost-link">
            送り状ページへ
          </Link>
        </header>
        <div className="tab-row">
          {[
            { id: "active", label: "有効中" },
            { id: "recent", label: "直近 ±日数" },
            { id: "attention", label: "要確認" },
            { id: "all", label: "全件" },
            { id: "logs", label: "監査ログ" },
          ].map((tab) => (
            <button
              key={tab.id}
              type="button"
              className={`tab ${activeTab === tab.id ? "active" : ""}`}
              onClick={() => setActiveTab(tab.id as HistoryTab)}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {activeTab === "logs" ? (
          <div className="filters">
            <label className="field">
              <span className="field-label">開始日</span>
              <input
                className="input"
                type="date"
                value={dateFrom}
                onChange={(e) => setDateFrom(e.target.value)}
              />
            </label>
            <label className="field">
              <span className="field-label">終了日</span>
              <input
                className="input"
                type="date"
                value={dateTo}
                onChange={(e) => setDateTo(e.target.value)}
              />
            </label>
            <label className="field">
              <span className="field-label">件数上限</span>
              <input
                className="input"
                type="number"
                min={1}
                max={1000}
                value={limit}
                onChange={(e) => setLimit(Number(e.target.value || 200))}
              />
            </label>
            <button className="btn primary" onClick={loadHistory} disabled={loading}>
              {loading ? "取得中..." : "監査ログを更新"}
            </button>
            {isAdmin ? (
              <>
                <button className="btn ghost" onClick={() => downloadHistory("csv")} disabled={loading}>
                  CSV保存
                </button>
                <button className="btn ghost" onClick={() => downloadHistory("json")} disabled={loading}>
                  JSON保存
                </button>
                <button className="btn danger" onClick={clearAllHistory} disabled={loading}>
                  全件クリア
                </button>
              </>
            ) : null}
          </div>
        ) : (
          <div className="filters">
            <label className="field">
              <span className="field-label">基準日</span>
              <input
                className="input"
                type="date"
                value={baseDate}
                onChange={(e) => setBaseDate(e.target.value)}
              />
            </label>
            <label className="field narrow">
              <span className="field-label">±日数</span>
              <input
                className="input"
                type="number"
                min={0}
                max={90}
                value={windowDays}
                onChange={(e) => setWindowDays(Number(e.target.value || 0))}
              />
            </label>
            <label className="field">
              <span className="field-label">施設</span>
              <input
                className="input"
                type="text"
                placeholder="施設名、複数はカンマ区切り"
                value={facilityFilter}
                onChange={(e) => setFacilityFilter(e.target.value)}
              />
            </label>
            <label className="field">
              <span className="field-label">取得元</span>
              <select className="input" value={source} onChange={(e) => setSource(e.target.value)}>
                <option value="">すべて</option>
                <option value="shipping_pdf_parse">PDF解析</option>
                <option value="excel_enrich">Excel更新</option>
                <option value="manual_track">手動照会</option>
                <option value="scheduled_refresh">自動再照会</option>
              </select>
            </label>
            <label className="field narrow">
              <span className="field-label">停滞時間(h)</span>
              <input
                className="input"
                type="number"
                min={1}
                max={336}
                value={attentionStaleHours}
                onChange={(e) => setAttentionStaleHours(Number(e.target.value || 24))}
              />
            </label>
            <label className="field narrow">
              <span className="field-label">件数上限</span>
              <input
                className="input"
                type="number"
                min={1}
                max={1000}
                value={limit}
                onChange={(e) => setLimit(Number(e.target.value || 200))}
              />
            </label>
            <button className="btn primary" onClick={() => loadLatest()} disabled={loading}>
              {loading ? "取得中..." : "状態を更新"}
            </button>
          </div>
        )}

        {activeTab === "recent" ? (
          <p className="subtle note">
            `発送日` が保存されている行は発送日ベース、旧データは最終照会日のローカル日付で補完して表示します。
          </p>
        ) : null}
        {!isAdmin && activeTab === "logs" ? (
          <p className="subtle note">監査ログの保存と全件クリアは管理者のみ表示されます。</p>
        ) : null}
        {message ? <p className="message">{message}</p> : null}
      </section>

        {currentSummary ? (
        <section className="panel">
          <header className="panel-header">
            <div>
              <h2>{activeTab === "logs" ? "監査ログの集計" : "現在の集計"}</h2>
              <p className="panel-copy">
                {activeTab === "logs"
                  ? "生ログ件数です。重めの参照データなので運用主画面としては補助用途です。"
                  : "伝票番号を主軸に、最新状態を1件ずつ表示します。"}
              </p>
            </div>
          </header>
          <div className="summary-grid">
            {summarizeHistory(currentSummary).map((item) => (
              <article className="summary-card" key={item.label}>
                <p className="summary-label">{item.label}</p>
                <p className="summary-value">{item.value}</p>
              </article>
            ))}
          </div>
          {activeTab !== "logs" && latest ? (
            <div className="meta-grid">
              <p>生成時刻: {formatLocalDate(latest.generated_at)}</p>
              <p>最終自動照会: {formatLocalDate(latest.last_scheduled_refresh_at)}</p>
              <p>次回自動照会: {formatLocalDate(latest.next_scheduled_refresh_at)}</p>
            </div>
          ) : null}
        </section>
      ) : null}

      {currentQuota ? (
        <section className={`panel quota-alert quota-${quotaTone(currentQuota)}`}>
          <header className="panel-header">
            <div>
              <h2>Quota 状態</h2>
              <p className="panel-copy">生ログ（lookup）ベースの使用量です。</p>
            </div>
          </header>
          <div className="meta-grid">
            <p>
              使用量: {currentQuota.used ?? "-"} / 上限: {currentQuota.limit ?? "-"} (
              {formatRatio(currentQuota.ratio)})
            </p>
            <p>
              対象: {currentQuota.resource || "-"} / 単位: {currentQuota.unit || "-"}
            </p>
            <p>{currentQuota.message || "quota情報なし"}</p>
          </div>
        </section>
      ) : null}

      {activeTab === "logs" ? (
        <section className="panel">
          <header className="panel-header">
            <div>
              <h2>監査ログ</h2>
              <p className="panel-copy">補助として照会ログを時系列で表示します。</p>
            </div>
          </header>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>発送日</th>
                  <th>照会日時</th>
                  <th>伝票番号</th>
                  <th>施設名</th>
                  <th>状態</th>
                  <th>到着日時</th>
                  <th>取得元</th>
                  <th>エラー</th>
                </tr>
              </thead>
              <tbody>
                {historyItems.length === 0 ? (
                  <tr>
                    <td colSpan={8}>監査ログがありません。</td>
                  </tr>
                ) : (
                  historyItems.map((item) => (
                    <tr key={item.id}>
                      <td>{formatDateOnly(item.ship_date)}</td>
                      <td>{formatLocalDate(item.looked_up_at)}</td>
                      <td>{item.tracking_number || item.tracking_key}</td>
                      <td>{item.facility_name || "-"}</td>
                      <td>{item.status || "-"}</td>
                      <td>{item.arrival_text || "-"}</td>
                      <td>{item.source || "-"}</td>
                      <td>{item.error || "-"}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </section>
      ) : (
        <section className="panel">
          <header className="panel-header">
            <div>
              <h2>伝票番号ベースの状態</h2>
              <p className="panel-copy">
                最新状態は伝票ごとに1カードで表示し、佐川履歴があれば下部に展開します。
              </p>
            </div>
          </header>

          {trackingCards.length ? (
            <div className="tracking-stack">
              {trackingCards.map((item) => {
                const reasons = describeAttentionReasons(item, attentionStaleHours);
                const tone =
                  item.error ? "danger" : item.delivered ? "ok" : reasons.length > 0 ? "warning" : "neutral";
                const eventCountText =
                  item.events.length === 0 ? "" : ` (${item.events.length}件)`;
                const latestEvent = item.events[0] || null;
                return (
                  <article
                    key={item.tracking_key || item.tracking_number}
                    className={`tracking-card tracking-card-${tone}`}
                  >
                    <header className="tracking-header">
                      <div className="tracking-identity">
                        <h3>{item.tracking_number || item.tracking_key || "-"}</h3>
                        <p className="tracking-subline">{item.facility_name || "施設未設定"}</p>
                      </div>
                      <span className={`status-pill tone-${tone}`}>{item.status || "-"}</span>
                    </header>
                    <dl className="tracking-facts">
                      <div className="fact-card">
                        <dt>最終照会</dt>
                        <dd>{formatCompactDateTime(item.looked_up_at)}</dd>
                      </div>
                      <div className="fact-card">
                        <dt>到着日時</dt>
                        <dd>{item.arrival_text || "-"}</dd>
                      </div>
                      <div className="fact-card">
                        <dt>取得元</dt>
                        <dd>{item.source || "-"}</dd>
                      </div>
                    </dl>
                    <div className="tag-row">
                      <span className="meta-pill meta-pill-info">出荷日 {formatDateOnly(item.ship_date)}</span>
                      <span className="meta-pill meta-pill-info">履歴 {item.events.length}件</span>
                      {reasons.length ? (
                        reasons.map((reason) => (
                          <span key={reason} className="meta-pill meta-pill-warning">
                            {reason}
                          </span>
                        ))
                      ) : (
                        <span className="meta-pill">要確認なし</span>
                      )}
                    </div>
                    {latestEvent ? (
                      <div className="event-preview">
                        <span className="event-preview-label">最新履歴</span>
                        <p>
                          <strong>{latestEvent.status || "-"}</strong>
                          <span>{formatCompactEventTime(latestEvent.occurred_at || null)}</span>
                          <span>{extractEventFacility(latestEvent)}</span>
                        </p>
                      </div>
                    ) : null}
                    {item.events.length ? (
                      <details className="tracking-events">
                        <summary>荷物履歴{eventCountText}</summary>
                        <ul className="event-list">
                          {item.events.map((event, eventIndex) => (
                            <li key={`${event.occurred_at || "-"}-${eventIndex}-${event.status}`}>
                              <strong>{event.status || "-"}</strong>
                              <span>{formatCompactEventTime(event.occurred_at || null)}</span>
                              <span>{extractEventFacility(event)}</span>
                            </li>
                          ))}
                        </ul>
                      </details>
                    ) : (
                      <p className="subtle compact-subtle">佐川履歴は未取得です。</p>
                    )}
                  </article>
                );
              })}
            </div>
          ) : (
            <p className="empty">条件に一致する伝票はありません。</p>
          )}
        </section>
      )}

      <style jsx>{`
        :global(body) {
          background: radial-gradient(circle at top left, #f8f4ea, #f4f7f6 40%, #eef1f0 100%);
          color: #1f2a2a;
          font-family: "Manrope", "Noto Sans JP", sans-serif;
        }

        :global(*) {
          box-sizing: border-box;
        }

        :global(a) {
          color: inherit;
          text-decoration: none;
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
          margin: 0 0 8px;
        }

        h1 {
          font-size: clamp(26px, 4vw, 36px);
          margin: 0 0 12px;
        }

        h2,
        h3,
        h4 {
          margin: 0;
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
          align-items: flex-start;
          gap: 16px;
          margin-bottom: 16px;
        }

        .panel-copy {
          color: #51615c;
          font-size: 13px;
          margin: 6px 0 0;
        }

        .ghost-link {
          font-size: 13px;
          color: #5f7b74;
          white-space: nowrap;
        }

        .tab-row {
          display: flex;
          flex-wrap: wrap;
          gap: 10px;
          margin-bottom: 16px;
        }

        .tab {
          border: 1px solid rgba(25, 32, 30, 0.12);
          border-radius: 999px;
          padding: 8px 14px;
          background: #f3f6f4;
          color: #31423d;
          font-weight: 600;
          cursor: pointer;
        }

        .tab.active {
          background: #1f2a2a;
          border-color: #1f2a2a;
          color: #f7f2e7;
        }

        .filters {
          display: grid;
          gap: 16px;
          grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
          align-items: end;
        }

        .field {
          display: flex;
          flex-direction: column;
          gap: 6px;
          font-size: 13px;
        }

        .field.narrow {
          max-width: 160px;
        }

        .field-label {
          color: #5f7b74;
          font-size: 12px;
          letter-spacing: 0.06em;
          text-transform: uppercase;
        }

        .input {
          border: 1px solid rgba(25, 32, 30, 0.14);
          border-radius: 10px;
          padding: 8px 10px;
          background: #fbfbf9;
          min-height: 40px;
        }

        .btn {
          border: none;
          border-radius: 999px;
          padding: 10px 16px;
          background: #e6ebe9;
          color: #1f2a2a;
          font-weight: 600;
          cursor: pointer;
          justify-self: start;
        }

        .btn.primary {
          background: #1f2a2a;
          color: #f7f2e7;
        }

        .btn.ghost {
          background: #eef3f1;
        }

        .btn.danger {
          background: #7a2d2d;
          color: #fff6f6;
        }

        .btn:disabled {
          opacity: 0.6;
          cursor: not-allowed;
        }

        .message {
          margin-top: 12px;
          padding: 8px 12px;
          border-radius: 10px;
          background: #f0f4f2;
          font-size: 13px;
        }

        .note {
          margin-top: 12px;
          font-size: 13px;
        }

        .summary-grid {
          display: grid;
          gap: 12px;
          grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
        }

        .summary-card {
          border-radius: 14px;
          padding: 14px;
          background: #fbf8ef;
          border: 1px solid rgba(25, 32, 30, 0.08);
        }

        .summary-label {
          font-size: 12px;
          letter-spacing: 0.06em;
          text-transform: uppercase;
          color: #5f7b74;
          margin: 0 0 8px;
        }

        .summary-value {
          font-size: 24px;
          font-weight: 700;
          margin: 0;
        }

        .meta-grid {
          margin-top: 14px;
          display: grid;
          gap: 8px;
          grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
          font-size: 13px;
        }

        .quota-alert {
          background: #f5f8f6;
        }

        .quota-warning {
          background: #fff4df;
          border-color: rgba(173, 102, 0, 0.35);
        }

        .quota-critical {
          background: #ffe7e7;
          border-color: rgba(170, 45, 45, 0.35);
        }

        .table-wrap {
          overflow-x: auto;
        }

        .tracking-stack {
          display: grid;
          gap: 16px;
          grid-template-columns: repeat(4, minmax(0, 1fr));
          align-items: stretch;
        }

        .tracking-card {
          border-radius: 18px;
          border: 1px solid rgba(25, 32, 30, 0.08);
          background: linear-gradient(180deg, rgba(247, 250, 249, 0.96), rgba(255, 255, 255, 1));
          padding: 13px;
          display: grid;
          gap: 9px;
          box-shadow: 0 10px 18px rgba(27, 35, 33, 0.05);
          min-height: 100%;
        }

        .tracking-card-neutral {
          border-top: 4px solid #a7bbb5;
        }

        .tracking-card-ok {
          border-top: 4px solid #4b8f63;
        }

        .tracking-card-warning {
          border-top: 4px solid #d49d30;
        }

        .tracking-card-danger {
          border-top: 4px solid #bb5757;
        }

        .tracking-header {
          display: flex;
          justify-content: space-between;
          align-items: flex-start;
          gap: 10px;
        }

        .tracking-identity {
          min-width: 0;
        }

        .tracking-identity h3 {
          font-size: 17px;
          line-height: 1.15;
          word-break: break-all;
        }

        .tracking-subline {
          color: #51615c;
          font-size: 11px;
          margin: 4px 0 0;
          line-height: 1.4;
        }

        .tracking-facts {
          display: grid;
          grid-template-columns: repeat(1, minmax(0, 1fr));
          gap: 7px;
          margin: 0;
        }

        .fact-card {
          background: rgba(243, 246, 244, 0.9);
          border: 1px solid rgba(25, 32, 30, 0.06);
          border-radius: 12px;
          padding: 8px 9px;
        }

        .fact-card dt {
          font-size: 10px;
          color: #6a7a75;
          text-transform: uppercase;
          letter-spacing: 0.06em;
          margin: 0 0 3px;
        }

        .fact-card dd {
          margin: 0;
          font-size: 12px;
          color: #22302d;
          line-height: 1.3;
          word-break: break-word;
        }

        .tag-row {
          display: flex;
          flex-wrap: wrap;
          gap: 6px;
        }

        .meta-pill {
          display: inline-flex;
          align-items: center;
          border-radius: 999px;
          padding: 4px 8px;
          font-size: 10px;
          background: #edf2f0;
          color: #31423d;
          line-height: 1;
        }

        .meta-pill-warning {
          background: #fff1d6;
          color: #7a5300;
        }

        .meta-pill-info {
          background: #e8f0ed;
          color: #24433d;
        }

        .event-preview {
          display: grid;
          gap: 4px;
          padding-top: 2px;
        }

        .event-preview-label {
          color: #6a7a75;
          font-size: 11px;
          text-transform: uppercase;
          letter-spacing: 0.06em;
        }

        .event-preview p {
          margin: 0;
          display: flex;
          flex-wrap: wrap;
          gap: 6px;
          color: #31423d;
          font-size: 12px;
        }

        .tracking-events {
          margin: 0;
          border-top: 1px solid rgba(25, 32, 30, 0.08);
          padding-top: 8px;
          align-self: end;
        }

        .tracking-events summary {
          cursor: pointer;
          color: #31423d;
          font-weight: 600;
          font-size: 12px;
          list-style: none;
        }

        .tracking-events summary::-webkit-details-marker {
          display: none;
        }

        .event-list {
          list-style: none;
          padding: 8px 0 0;
          margin: 0;
          display: grid;
          gap: 6px;
        }

        .event-list li {
          display: grid;
          grid-template-columns: minmax(0, 76px) minmax(0, 76px) minmax(0, 1fr);
          gap: 7px;
          align-items: start;
          padding: 6px 7px;
          border-radius: 10px;
          background: #f7faf8;
          font-size: 11px;
          color: #31423d;
        }

        .compact-subtle {
          font-size: 12px;
        }

        table {
          width: 100%;
          border-collapse: collapse;
          font-size: 13px;
        }

        th,
        td {
          padding: 10px;
          text-align: left;
          white-space: nowrap;
          border-bottom: 1px solid rgba(25, 32, 30, 0.08);
          vertical-align: top;
        }

        thead {
          background: #f4f1ea;
        }

        .date-stack {
          display: grid;
          gap: 18px;
        }

        .date-group {
          border: 1px solid rgba(25, 32, 30, 0.08);
          border-radius: 16px;
          padding: 16px;
          background: linear-gradient(180deg, rgba(248, 244, 234, 0.5), rgba(255, 255, 255, 0.9));
        }

        .date-header,
        .facility-header {
          display: flex;
          justify-content: space-between;
          gap: 12px;
          align-items: flex-start;
          margin-bottom: 12px;
        }

        .facility-stack {
          display: grid;
          gap: 12px;
        }

        .facility-card {
          border-radius: 14px;
          border: 1px solid rgba(25, 32, 30, 0.08);
          background: #ffffff;
          padding: 14px;
        }

        .status-pill {
          display: inline-flex;
          align-items: center;
          min-height: 28px;
          padding: 4px 10px;
          border-radius: 999px;
          font-weight: 700;
          background: #edf1ef;
        }

        .tone-ok {
          background: #e2f3e4;
          color: #1d5f33;
        }

        .tone-warning {
          background: #fff0cf;
          color: #8a5b00;
        }

        .tone-danger {
          background: #ffe3e3;
          color: #8d2323;
        }

        .tone-neutral {
          background: #edf1ef;
          color: #3f5350;
        }

        .empty {
          margin: 0;
          color: #51615c;
        }

        @media (max-width: 760px) {
          .page {
            padding: 24px 16px 48px;
          }

          .panel-header,
          .date-header,
          .facility-header {
            flex-direction: column;
          }

          .field.narrow {
            max-width: none;
          }

          .event-list li {
            grid-template-columns: 1fr;
            gap: 2px;
          }
        }

        @media (max-width: 1380px) {
          .tracking-stack {
            grid-template-columns: repeat(3, minmax(0, 1fr));
          }
        }

        @media (max-width: 980px) {
          .tracking-stack {
            grid-template-columns: repeat(2, minmax(0, 1fr));
          }
        }

        @media (max-width: 640px) {
          .tracking-stack {
            grid-template-columns: 1fr;
          }
        }
      `}</style>
      <style jsx global>{`
        @import url("https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;700&family=Noto+Sans+JP:wght@400;600&display=swap");
      `}</style>
    </main>
  );
}
