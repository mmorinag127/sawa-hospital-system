import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import TopNav from "../components/TopNav";
import { apiClient } from "../services/apiClient";
import {
  fetchFacilityNameMap,
  fetchOrderFacilityCandidates,
  pickBestFacilityCandidate,
  type FacilityHint,
  type FacilityNameMap,
} from "../services/facilityData";

type OrderSummary = {
  id: string;
  facility?: string | null;
  week?: string | null;
  status?: string | null;
  received_at?: string | null;
  line_count?: number | null;
};

type DailyBagBreakdown = {
  amount_label?: string | null;
  count?: number | null;
  order_refs?: {
    order_id?: string | null;
    facility_label?: string | null;
    area_id?: string | null;
    quantity?: number | null;
  }[];
};

type DailyBagTypeGroup = {
  bag_type?: string | null;
  bag_count?: number | null;
  total_quantity?: number | null;
  total_amount_label?: string | null;
  breakdowns?: DailyBagBreakdown[];
};

type DailyBagDietGroup = {
  diet_type?: string | null;
  total_quantity?: number | null;
  total_amount_label?: string | null;
  bag_type_groups?: DailyBagTypeGroup[];
};

type DailyBagMenuGroup = {
  daypart?: string | null;
  daypart_key?: string | null;
  menu_name?: string | null;
  diet_groups?: DailyBagDietGroup[];
};

type DailyBagSummaryResponse = {
  date?: string | null;
  order_count?: number | null;
  groups?: DailyBagMenuGroup[];
};

type TotalRow = {
  date?: string | null;
  daypart?: string | null;
  menu_category?: string | null;
  menu_name?: string | null;
  diet_type?: string | null;
  quantity?: number | null;
};

const dietTypeLabels: Record<string, string> = {
  regular: "常食",
  regular_bag: "常食(袋分け)",
  soft: "軟菜",
  mixer: "ミキサー",
  daycare: "通所",
  staff: "職員",
  tea: "お茶",
  business: "事業",
  diabetes: "糖尿",
  pregnancy: "妊娠",
  sesame_allergy: "ゴマアレルギー",
  no_meat: "禁食(肉禁)",
  no_fish: "禁食(魚禁)",
  change_1: "変更1",
  change_2: "変更2",
  placeholder: "-",
  unknown: "不明",
};

const bagTypeLabels: Record<string, string> = {
  standard: "標準",
  condiment: "付属品",
  small: "小",
  medium: "中",
  large: "大",
};

const preferredDietOrder = [
  "regular",
  "regular_bag",
  "soft",
  "mixer",
  "daycare",
  "staff",
  "tea",
  "business",
  "diabetes",
  "pregnancy",
  "sesame_allergy",
  "no_meat",
  "no_fish",
  "change_1",
  "change_2",
  "placeholder",
  "unknown",
];

const formatTimestamp = (value?: string | null) => {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ja-JP");
};

const formatQuantity = (value?: number | null) => {
  if (value == null || Number.isNaN(value)) return "-";
  return Number(value).toLocaleString("ja-JP");
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

const headerValueToString = (value: unknown) => {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map((item) => String(item)).join("; ");
  if (value == null) return "";
  return String(value);
};

const extractErrorDetail = async (err: any) => {
  const detail = err?.response?.data?.detail;
  if (typeof detail === "string" && detail) return detail;
  const data = err?.response?.data;
  if (typeof Blob !== "undefined" && data instanceof Blob) {
    try {
      const text = await data.text();
      if (!text) return "";
      const parsed = JSON.parse(text);
      if (typeof parsed?.detail === "string" && parsed.detail) return parsed.detail;
      return text;
    } catch {
      return "";
    }
  }
  return "";
};

const normalizeDietType = (value?: string | null) => {
  const token = String(value || "").trim();
  return token || "unknown";
};

const formatDietType = (value?: string | null) => {
  const token = normalizeDietType(value);
  return dietTypeLabels[token] || token;
};

const formatBagType = (value?: string | null) => {
  const token = String(value || "").trim();
  if (!token) return "-";
  return bagTypeLabels[token] || bagTypeLabels[token.toLowerCase()] || token;
};

const sumDietQuantity = (group?: DailyBagMenuGroup | null) => {
  const diets = Array.isArray(group?.diet_groups) ? group?.diet_groups : [];
  return diets.reduce((sum, diet) => sum + Number(diet?.total_quantity || 0), 0);
};

const buildDaypartGroups = (groups: DailyBagMenuGroup[]) => {
  const map = new Map<string, { daypart: string; rows: DailyBagMenuGroup[] }>();
  groups.forEach((group) => {
    const daypart = String(group.daypart || group.daypart_key || "-").trim() || "-";
    const existing = map.get(daypart) || { daypart, rows: [] as DailyBagMenuGroup[] };
    existing.rows.push(group);
    map.set(daypart, existing);
  });
  return Array.from(map.values()).sort((left, right) => left.daypart.localeCompare(right.daypart, "ja"));
};

export default function DailyDeliveryNotesPage() {
  const [date, setDate] = useState<string>("");
  const [status, setStatus] = useState<string>("");
  const [orders, setOrders] = useState<OrderSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [bagMessage, setBagMessage] = useState("");
  const [totalsMessage, setTotalsMessage] = useState("");
  const [facilityNameMap, setFacilityNameMap] = useState<FacilityNameMap>({});
  const [facilityHints, setFacilityHints] = useState<Record<string, FacilityHint>>({});
  const [dailyBagSummary, setDailyBagSummary] = useState<DailyBagSummaryResponse>({});
  const [totalsRows, setTotalsRows] = useState<TotalRow[]>([]);

  useEffect(() => {
    if (!date) {
      const today = new Date();
      setDate(today.toISOString().slice(0, 10));
    }
  }, [date]);

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

  useEffect(() => {
    let cancelled = false;
    const unresolved = orders
      .filter((order) => !order.facility && order.id)
      .slice(0, 50)
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
          if (best) results[orderId] = { ...best, order_id: orderId };
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
  }, [orders, facilityHints]);

  const facilityLabel = (order: OrderSummary) => {
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

  const loadOrders = async () => {
    if (!date) return;
    setLoading(true);
    setMessage("");
    setBagMessage("");
    setTotalsMessage("");
    setDailyBagSummary({});
    setTotalsRows([]);
    try {
      const params: Record<string, string> = { date };
      if (status) params.status = status;
      const [ordersRes, bagRes, totalsRes] = await Promise.allSettled([
        apiClient.get("/orders/by-line-date", { params }),
        apiClient.get("/orders/daily-bags", { params }),
        apiClient.get("/totals", { params: { date } }),
      ]);

      if (ordersRes.status === "fulfilled") {
        const items = Array.isArray(ordersRes.value.data?.orders) ? ordersRes.value.data.orders : [];
        setOrders(items);
        if (!items.length) {
          setMessage("該当する注文がありません。");
        }
      } else {
        throw ordersRes.reason;
      }

      if (bagRes.status === "fulfilled") {
        const payload = bagRes.value.data || {};
        setDailyBagSummary(payload);
        const count = Array.isArray(payload.groups) ? payload.groups.length : 0;
        if (!count) {
          setBagMessage("袋分け結果がまだ生成されていません。");
        }
      } else {
        setDailyBagSummary({});
        setBagMessage("袋分け結果の取得に失敗しました。");
      }

      if (totalsRes.status === "fulfilled") {
        const rows = Array.isArray(totalsRes.value.data?.rows) ? totalsRes.value.data.rows : [];
        setTotalsRows(rows);
        if (!rows.length) {
          setTotalsMessage("総量は確定注文のみ集計されるため、対象データがありません。");
        }
      } else {
        setTotalsRows([]);
        setTotalsMessage("総量の取得に失敗しました。");
      }
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setMessage(detail ? `取得に失敗しました: ${detail}` : "取得に失敗しました。");
      setOrders([]);
      setDailyBagSummary({});
      setTotalsRows([]);
    } finally {
      setLoading(false);
    }
  };

  const openOutput = async (path: string, label: string) => {
    const timestamp = new Date().toLocaleString("ja-JP");
    setMessage(`${label}のダウンロードを開始します。 (${timestamp})`);
    try {
      const res = await apiClient.get(path, { responseType: "blob" });
      const contentDisposition = res.headers?.["content-disposition"] || res.headers?.["Content-Disposition"];
      const filename = extractFilename(contentDisposition) || "output";
      const blob = res.data instanceof Blob ? res.data : new Blob([res.data]);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (err: any) {
      const detail = await extractErrorDetail(err);
      setMessage(detail ? `ダウンロードに失敗しました: ${detail}` : "ダウンロードに失敗しました。");
    }
  };

  const downloadDailyBundle = async (bundleType: "labels" | "delivery" | "both") => {
    if (!date) {
      setMessage("日付を指定してください。");
      return;
    }
    const label =
      bundleType === "labels"
        ? "当日ラベルExcel"
        : bundleType === "delivery"
          ? "当日納品書Excel"
          : "当日一括Excel（ラベル+納品書）";
    setMessage(`${label}を作成中です...`);
    try {
      const res = await apiClient.get("/outputs/daily-bundle", {
        params: { date, bundle_type: bundleType, status: status || undefined },
        responseType: "blob",
      });
      const contentDisposition = headerValueToString(
        res.headers?.["content-disposition"] || res.headers?.["Content-Disposition"],
      );
      const filename = extractFilename(contentDisposition) || `daily_outputs_${date}_${bundleType}.xlsx`;
      const blob = res.data instanceof Blob ? res.data : new Blob([res.data]);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      const successOrders = Number(res.headers?.["x-daily-bundle-success-orders"] || 0);
      const errorOrders = Number(res.headers?.["x-daily-bundle-error-orders"] || 0);
      setMessage(`${label}をダウンロードしました。成功 ${successOrders}件 / 失敗 ${errorOrders}件`);
    } catch (err: any) {
      const detail = await extractErrorDetail(err);
      setMessage(detail ? `一括ダウンロードに失敗しました: ${detail}` : "一括ダウンロードに失敗しました。");
    }
  };

  const bagDayparts = useMemo(
    () => buildDaypartGroups(Array.isArray(dailyBagSummary.groups) ? dailyBagSummary.groups : []),
    [dailyBagSummary],
  );

  const totalsSummaryRows = useMemo(() => {
    const rows = [...totalsRows];
    const dietIndex = new Map(preferredDietOrder.map((value, index) => [value, index]));
    rows.sort((left, right) => {
      const daypart = String(left.daypart || "").localeCompare(String(right.daypart || ""), "ja");
      if (daypart !== 0) return daypart;
      const category = String(left.menu_category || "").localeCompare(String(right.menu_category || ""), "ja");
      if (category !== 0) return category;
      const menu = String(left.menu_name || "").localeCompare(String(right.menu_name || ""), "ja");
      if (menu !== 0) return menu;
      return (dietIndex.get(normalizeDietType(left.diet_type)) ?? 99) - (dietIndex.get(normalizeDietType(right.diet_type)) ?? 99);
    });
    return rows;
  }, [totalsRows]);

  return (
    <main className="page">
      <header className="hero">
        <div>
          <p className="eyebrow">Daily Outputs</p>
          <h1>日別出力</h1>
          <p className="subtle">日付を軸に、注文・袋分け・ラベル・納品書・総量を全施設横断で確認します。</p>
        </div>
        <TopNav />
      </header>

      <section className="panel">
        <header className="panel-header">
          <h2>この画面で見ること</h2>
        </header>
        <div className="guide-grid">
          <article className="guide-card">
            <p className="guide-title">発送前の最終確認</p>
            <p className="guide-text">その日に出す注文、袋分け、納品書、ラベルをまとめて確認します。</p>
          </article>
          <article className="guide-card">
            <p className="guide-title">袋分けを見る</p>
            <p className="guide-text">「当日袋分け一覧」でメニューごとの袋数と計算結果を確認します。</p>
          </article>
          <article className="guide-card">
            <p className="guide-title">迷ったとき</p>
            <p className="guide-text">その注文の「詳細」を開いて、元のシートとOCR結果を確認します。</p>
          </article>
        </div>
      </section>

      <section className="panel">
        <header className="panel-header">
          <h2>フィルタ</h2>
          <span className="badge">合計 {orders.length} 件</span>
        </header>
        <div className="filters">
          <label className="field">
            <span className="field-label">日付</span>
            <input className="input" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          </label>
          <label className="field">
            <span className="field-label">ステータス</span>
            <select className="input" value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">全て</option>
              <option value="未着">未着</option>
              <option value="要確認">要確認</option>
              <option value="確定">確定</option>
              <option value="エラー">エラー</option>
            </select>
          </label>
          <button className="btn primary" onClick={loadOrders} disabled={loading}>
            {loading ? "取得中..." : "取得"}
          </button>
          <button className="btn ghost" type="button" onClick={() => downloadDailyBundle("labels")} disabled={loading}>
            当日ラベルExcel
          </button>
          <button className="btn ghost" type="button" onClick={() => downloadDailyBundle("delivery")} disabled={loading}>
            当日納品書Excel
          </button>
          <button className="btn ghost" type="button" onClick={() => downloadDailyBundle("both")} disabled={loading}>
            当日一括Excel
          </button>
        </div>
        <p className="subtle helper-text">一括Excelと袋分けは選択したステータス、総量は確定注文ベースです。</p>
      </section>

      {message ? <p className="message">{message}</p> : null}

      <section className="panel">
        <header className="panel-header">
          <h2>当日注文一覧</h2>
        </header>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>施設</th>
                <th>週</th>
                <th>ステータス</th>
                <th>受信日時</th>
                <th>行数</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {orders.length === 0 ? (
                <tr>
                  <td colSpan={6}>該当データなし</td>
                </tr>
              ) : (
                orders.map((order) => (
                  <tr key={order.id}>
                    <td>{facilityLabel(order)}</td>
                    <td>{order.week || "未確定"}</td>
                    <td>{order.status || "-"}</td>
                    <td>{formatTimestamp(order.received_at)}</td>
                    <td>{order.line_count ?? "-"}</td>
                    <td className="actions">
                      <button
                        className="btn ghost"
                        type="button"
                        onClick={() => openOutput(`/outputs/labels?order_id=${order.id}`, "ラベルCSV")}
                      >
                        ラベル
                      </button>
                      <button
                        className="btn ghost"
                        type="button"
                        onClick={() => openOutput(`/outputs/delivery-notes?order_id=${order.id}`, "納品書Excel")}
                      >
                        納品書
                      </button>
                      <button
                        className="btn ghost"
                        type="button"
                        onClick={() => openOutput(`/outputs/manufacturing-aggregate?order_id=${order.id}`, "総量CSV")}
                      >
                        総量CSV
                      </button>
                      <Link href={`/orders/${order.id}`} className="link">
                        詳細
                      </Link>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel">
        <header className="panel-header">
          <h2>当日袋分け一覧</h2>
          <span className="badge">{Array.isArray(dailyBagSummary.groups) ? dailyBagSummary.groups.length : 0} メニュー</span>
        </header>
        {bagMessage ? <p className="subtle">{bagMessage}</p> : null}
        {bagDayparts.length === 0 ? (
          <p className="subtle">該当データなし</p>
        ) : (
          <div className="bag-daypart-list">
            {bagDayparts.map((daypartGroup) => (
              <section key={daypartGroup.daypart} className="daypart-block">
                <header className="panel-header">
                  <h3 className="daypart-title">{daypartGroup.daypart}</h3>
                  <span className="badge">{daypartGroup.rows.length} メニュー</span>
                </header>
                <div className="menu-bag-grid">
                  {daypartGroup.rows.map((menuGroup) => (
                    <details key={`${daypartGroup.daypart}-${menuGroup.menu_name}`} className="menu-bag-card" open>
                      <summary className="menu-bag-summary">
                        <div>
                          <p className="menu-bag-name">{menuGroup.menu_name || "-"}</p>
                          <p className="menu-bag-meta">
                            {Array.isArray(menuGroup.diet_groups) ? menuGroup.diet_groups.length : 0}区分 /{" "}
                            {formatQuantity(sumDietQuantity(menuGroup))}食
                          </p>
                        </div>
                      </summary>
                      <div className="menu-bag-body">
                        <table className="menu-bag-table">
                          <thead>
                            <tr>
                              <th>区分</th>
                              <th>注文数</th>
                              <th>計算結果</th>
                              <th>袋種</th>
                            </tr>
                          </thead>
                          <tbody>
                            {(menuGroup.diet_groups || []).map((dietGroup) => (
                              <tr key={`${menuGroup.menu_name}-${dietGroup.diet_type}`}>
                                <td>{formatDietType(dietGroup.diet_type)}</td>
                                <td className="numeric">{formatQuantity(dietGroup.total_quantity)}</td>
                                <td>{dietGroup.total_amount_label || "計算不可"}</td>
                                <td>
                                  <div className="bag-type-stack">
                                    {(dietGroup.bag_type_groups || []).map((bagTypeGroup) => (
                                      <details
                                        key={`${dietGroup.diet_type}-${bagTypeGroup.bag_type}`}
                                        className="bag-type-detail"
                                      >
                                        <summary className="bag-type-summary">
                                          <span className="bag-type-main">
                                            {formatBagType(bagTypeGroup.bag_type)} {bagTypeGroup.bag_count || 0}袋
                                          </span>
                                          <span className="bag-type-sub">
                                            {bagTypeGroup.total_amount_label || "計算不可"}
                                          </span>
                                        </summary>
                                        <div className="bag-breakdown-list">
                                          {(bagTypeGroup.breakdowns || []).map((breakdown, index) => (
                                            <div
                                              key={`${bagTypeGroup.bag_type}-${breakdown.amount_label}-${index}`}
                                              className="bag-breakdown-row"
                                            >
                                              <span>{breakdown.amount_label || "計算不可"}</span>
                                              <strong>x {breakdown.count || 0}</strong>
                                            </div>
                                          ))}
                                        </div>
                                      </details>
                                    ))}
                                  </div>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </details>
                  ))}
                </div>
              </section>
            ))}
          </div>
        )}
      </section>

      <section className="panel">
        <header className="panel-header">
          <h2>当日総量</h2>
        </header>
        {totalsMessage ? <p className="subtle">{totalsMessage}</p> : null}
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>食区</th>
                <th>献立区分</th>
                <th>メニュー</th>
                <th>区分</th>
                <th>注文数</th>
              </tr>
            </thead>
            <tbody>
              {totalsSummaryRows.length === 0 ? (
                <tr>
                  <td colSpan={5}>該当データなし</td>
                </tr>
              ) : (
                totalsSummaryRows.map((row, index) => (
                  <tr
                    key={[
                      row.date || "-",
                      row.daypart || "-",
                      row.menu_category || "-",
                      row.menu_name || "-",
                      row.diet_type || "-",
                      index,
                    ].join("__")}
                  >
                    <td>{row.daypart || "-"}</td>
                    <td>{row.menu_category || "-"}</td>
                    <td>{row.menu_name || "-"}</td>
                    <td>{formatDietType(row.diet_type)}</td>
                    <td className="numeric">{formatQuantity(row.quantity)}</td>
                  </tr>
                ))
              )}
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

        .helper-text {
          margin-top: 12px;
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
          gap: 12px;
        }
        .guide-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
          gap: 14px;
        }
        .guide-card {
          border-radius: 16px;
          border: 1px solid rgba(25, 32, 30, 0.1);
          background: #fcfbf7;
          padding: 16px;
        }
        .guide-title {
          margin: 0 0 8px;
          font-weight: 800;
        }
        .guide-text {
          margin: 0;
          color: #51615c;
          line-height: 1.6;
        }

        h2 {
          font-size: 18px;
          margin: 0;
        }

        .daypart-title {
          font-size: 17px;
          margin: 0;
        }

        .badge {
          background: #1f2a2a;
          color: #f7f2e7;
          padding: 4px 10px;
          border-radius: 999px;
          font-size: 12px;
          white-space: nowrap;
        }

        .filters {
          display: grid;
          gap: 16px;
          grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
          align-items: center;
        }

        .field {
          display: flex;
          flex-direction: column;
          gap: 6px;
          font-size: 13px;
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
        }

        .btn {
          border: none;
          border-radius: 999px;
          padding: 8px 14px;
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
          background: #eef2f0;
          color: #1f2a2a;
          border: 1px solid rgba(25, 32, 30, 0.12);
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

        .table-wrap {
          overflow-x: auto;
        }

        table {
          width: 100%;
          border-collapse: collapse;
          font-size: 14px;
          min-width: 720px;
        }

        th,
        td {
          padding: 10px;
          text-align: left;
          vertical-align: middle;
          white-space: nowrap;
        }

        thead {
          background: #f4f1ea;
        }

        tbody tr:nth-child(even) {
          background: #faf9f5;
        }

        .actions {
          display: flex;
          gap: 8px;
          align-items: center;
          flex-wrap: wrap;
        }

        .link {
          color: #1f2a2a;
          text-decoration: underline;
          font-weight: 600;
        }

        .numeric {
          font-weight: 700;
          color: #1f2a2a;
        }

        .bag-daypart-list {
          display: flex;
          flex-direction: column;
          gap: 20px;
        }

        .daypart-block + .daypart-block {
          padding-top: 20px;
          border-top: 1px solid rgba(25, 32, 30, 0.08);
        }

        .menu-bag-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(min(420px, 100%), 1fr));
          gap: 16px;
        }

        .menu-bag-card {
          border: 1px solid rgba(25, 32, 30, 0.08);
          border-radius: 16px;
          background: #fcfbf8;
          overflow: hidden;
        }

        .menu-bag-summary {
          cursor: pointer;
          list-style: none;
          padding: 16px 18px;
          background: linear-gradient(135deg, #f5efe2, #f8fbfa);
        }

        .menu-bag-summary::-webkit-details-marker {
          display: none;
        }

        .menu-bag-name {
          margin: 0;
          font-size: 16px;
          font-weight: 700;
          color: #1f2a2a;
        }

        .menu-bag-meta {
          margin: 6px 0 0;
          font-size: 12px;
          color: #5b6a66;
        }

        .menu-bag-body {
          padding: 12px 16px 16px;
        }

        .menu-bag-table {
          min-width: 100%;
        }

        .menu-bag-table th,
        .menu-bag-table td {
          white-space: normal;
          vertical-align: top;
        }

        .bag-type-stack {
          display: flex;
          flex-direction: column;
          gap: 8px;
          min-width: 220px;
        }

        .bag-type-detail {
          border: 1px solid rgba(25, 32, 30, 0.08);
          border-radius: 12px;
          background: #ffffff;
        }

        .bag-type-summary {
          display: flex;
          justify-content: space-between;
          align-items: center;
          gap: 12px;
          cursor: pointer;
          padding: 10px 12px;
          list-style: none;
          font-size: 13px;
          font-weight: 600;
        }

        .bag-type-summary::-webkit-details-marker {
          display: none;
        }

        .bag-type-main {
          color: #1f2a2a;
        }

        .bag-type-sub {
          color: #5f7b74;
          font-size: 12px;
        }

        .bag-breakdown-list {
          display: flex;
          flex-direction: column;
          gap: 6px;
          padding: 0 12px 12px;
          border-top: 1px solid rgba(25, 32, 30, 0.06);
        }

        .bag-breakdown-row {
          display: flex;
          justify-content: space-between;
          gap: 12px;
          font-size: 12px;
          color: #51615c;
          padding-top: 8px;
        }
      `}</style>
      <style jsx global>{`
        @import url("https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;700&family=Noto+Sans+JP:wght@400;600&display=swap");
      `}</style>
    </main>
  );
}
