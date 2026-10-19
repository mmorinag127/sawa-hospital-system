import { useRouter } from "next/router";
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import Link from "next/link";
import { Button, MenuItem, Stack } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import { FormTextField, SawaProvider } from "@sawa/ui";
import { FormProvider, useForm } from "react-hook-form";
import TopNav from "../components/TopNav";
import { apiClient } from "../services/apiClient";
import { browserSessionKey, subscribeBrowserSession } from "../services/browserSession";
import { businessPeriodForDate, currentBusinessWeek, storedWeeklyWeightPeriod,
  weekInputForPeriod, weeklyWeightConditionsPeriod, weeklyWeightDates,
  weeklyWeightPeriod } from "../services/weeklyWeightPeriod";

const queryValue = (value: string | string[] | undefined) => {
  if (Array.isArray(value)) return value[0] || "";
  return value || "";
};

const extractFilename = (contentDisposition?: string | null) => {
  if (!contentDisposition) return "";
  const utf8Match = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match?.[1]) return decodeURIComponent(utf8Match[1]);
  const asciiMatch = contentDisposition.match(/filename="?([^";]+)"?/i);
  return asciiMatch?.[1] || "";
};

const extractErrorDetail = async (err: any) => {
  const data = err?.response?.data;
  if (data instanceof Blob) {
    const text = await data.text();
    try {
      const parsed = JSON.parse(text);
      return parsed?.detail ? String(parsed.detail) : text;
    } catch {
      return text;
    }
  }
  if (data?.detail) return String(data.detail);
  return "";
};

type OrderSummary = {
  id: string;
  facility?: string | null;
  week?: string | null;
  week_value?: string | null;
  week_label?: string | null;
  status?: string | null;
  received_at?: string | null;
  line_count?: number | null;
};

type WeekHistoryItem = {
  week: string;
  label: string;
  orderCount: number;
  latestReceivedAt: string;
};

type WeeklyWeightConditions = {
  week: string;
  savedWeek: string;
  status: string;
};

const formatTimestamp = (value?: string | null) => {
  if (!value) return "-";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString("ja-JP", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
};

const weekHistoryFromOrder = (order: OrderSummary) => {
  const weekValue = String(order.week_value || order.week || "").trim();
  const period = storedWeeklyWeightPeriod(weekValue);
  if (!period) return null;
  return {
    week: weekValue,
    label: `${period.start} - ${period.end}`,
    receivedAt: order.received_at || "",
  };
};

function WeeklyWeightOutputScreen() {
  const router = useRouter();
  const routeIdentity = (target = router) => JSON.stringify([target.asPath, target.query.date, target.query.week, target.query.status]);
  const queryIdentity = routeIdentity();
  const [resolvedQuery, setResolvedQuery] = useState<string | null>(null);
  const periodReady = router.isReady && resolvedQuery === queryIdentity;
  const liveRoute = useRef({ router, queryIdentity, periodReady });
  liveRoute.current = { router, queryIdentity, periodReady };
  const [week, setWeek] = useState(currentBusinessWeek());
  const [status, setStatus] = useState("");
  const conditionForm = useForm<WeeklyWeightConditions>({ defaultValues: { week: currentBusinessWeek(), savedWeek: "", status: "" } });
  const [message, setMessage] = useState("");
  const [downloading, setDownloading] = useState(false);
  const [orders, setOrders] = useState<OrderSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [weekHistory, setWeekHistory] = useState<WeekHistoryItem[]>([]);
  const loadGeneration = useRef(0);
  const downloadGeneration = useRef(0);
  const inputGeneration = useRef(0);
  const messageGeneration = useRef(0);
  const mounted = useRef(false);

  useEffect(() => {
    mounted.current = true;
    let observedConditions = { ...conditionForm.getValues() };
    let observedSession = browserSessionKey();
    const retire = () => {
      inputGeneration.current += 1;
      loadGeneration.current += 1;
      downloadGeneration.current += 1;
      messageGeneration.current += 1;
      setLoading(false);
      setDownloading(false);
      setMessage("");
    };
    const subscription = conditionForm.watch(() => {
      const values = conditionForm.getValues();
      if (values.week !== observedConditions.week && values.savedWeek
          && values.savedWeek === observedConditions.savedWeek) {
        conditionForm.setValue("savedWeek", "");
        return;
      }
      if (values.week === observedConditions.week && values.savedWeek === observedConditions.savedWeek
          && values.status === observedConditions.status) return;
      observedConditions = { ...values };
      retire();
    });
    const stopSession = subscribeBrowserSession(() => {
      const session = browserSessionKey();
      if (session === observedSession) return;
      observedSession = session;
      retire();
    });
    router.events.on("routeChangeStart", retire);
    return () => {
      mounted.current = false;
      inputGeneration.current += 1;
      loadGeneration.current += 1;
      downloadGeneration.current += 1;
      messageGeneration.current += 1;
      subscription.unsubscribe();
      stopSession();
      router.events.off("routeChangeStart", retire);
    };
  }, [conditionForm, router.events]);

  const beginOperation = (kind: "list" | "output", conditions: WeeklyWeightConditions) => {
    const captured = { ...conditions };
    const ownsInput = () => {
      const live = liveRoute.current;
      const values = conditionForm.getValues();
      return mounted.current && live.periodReady && live.router.isReady
        && queryIdentity === live.queryIdentity && live.queryIdentity === routeIdentity(live.router)
        && captured.week === values.week && captured.savedWeek === values.savedWeek
        && captured.status === values.status;
    };
    if (!ownsInput()) return { current: () => false, ownsMessage: () => false };
    const operations = kind === "list" ? loadGeneration : downloadGeneration;
    const operation = ++operations.current;
    const input = inputGeneration.current;
    const messageOwner = ++messageGeneration.current;
    const session = browserSessionKey();
    const current = () => ownsInput() && input === inputGeneration.current && operation === operations.current
      && session === browserSessionKey();
    if (current()) {
      setMessage("");
      if (kind === "list") setLoading(false);
      else setDownloading(false);
    }
    return { current, ownsMessage: () => current() && messageOwner === messageGeneration.current };
  };

  useEffect(() => {
    if (!router.isReady) return;
    const queryDate = queryValue(router.query.date);
    const queryWeek = queryValue(router.query.week);
    const queryStatus = queryValue(router.query.status);
    const initialWeek = currentBusinessWeek();
    const hasWeek = router.query.week !== undefined;
    const hasDate = router.query.date !== undefined;
    const period = hasWeek ? weeklyWeightPeriod(queryWeek) : hasDate ? businessPeriodForDate(queryDate) : weeklyWeightPeriod(initialWeek);
    if (Array.isArray(router.query.week) || Array.isArray(router.query.date)
      || !period || (hasDate && (!businessPeriodForDate(queryDate) || queryDate < period.start || queryDate > period.end))) {
      conditionForm.reset({ week: "", savedWeek: "", status: queryStatus });
      setMessage("対象期間を確認してください。");
      setResolvedQuery(queryIdentity);
      return;
    }
    const nextWeek = weekInputForPeriod(period);
    const nextStatus = queryStatus || "";
    const selection = queryWeek || (queryDate ? "" : initialWeek);
    conditionForm.reset({ week: nextWeek, savedWeek: selection.includes("@") ? selection : "", status: nextStatus });
    setResolvedQuery(queryIdentity);
  }, [router.isReady, queryIdentity]);

  const weekDates = useMemo(() => {
    const period = weeklyWeightPeriod(week);
    return period ? weeklyWeightDates(period) : [];
  }, [week]);

  const weekLabel = useMemo(() => {
    if (weekDates.length === 0) return "";
    return `${weekDates[0]} から ${weekDates.at(-1)}`;
  }, [weekDates]);

  const weekHistoryQuery = useQuery({
    queryKey: ["weekly-weight-output", "history"],
    queryFn: async () => {
      const response = await apiClient.get("/orders", { params: { include_ocr: false, limit: 500 } });
      const grouped = new Map<string, WeekHistoryItem>();
      (response.data?.orders || []).forEach((order: OrderSummary) => {
        const history = weekHistoryFromOrder(order);
        if (!history?.week) return;
        const current = grouped.get(history.week);
        const latestReceivedAt = [current?.latestReceivedAt || "", history.receivedAt]
          .filter(Boolean)
          .sort()
          .at(-1) || "";
        grouped.set(history.week, {
          week: history.week,
          label: history.label,
          orderCount: (current?.orderCount || 0) + 1,
          latestReceivedAt,
        });
      });
      return Array.from(grouped.values()).sort((left, right) => {
          return right.week.localeCompare(left.week);
      });
    },
  });

  useEffect(() => {
    if (weekHistoryQuery.data) setWeekHistory(weekHistoryQuery.data);
  }, [weekHistoryQuery.data]);

  const loadWeeklyOrders = async (conditions: WeeklyWeightConditions) => {
    const owner = beginOperation("list", conditions);
    if (!owner.current()) return;
    const period = weeklyWeightConditionsPeriod(conditions);
    if (!period) {
      if (owner.ownsMessage()) setMessage("対象期間を確認してください。");
      return;
    }
    const targetDates = weeklyWeightDates(period);
    setLoading(true);
    try {
      const responses = await Promise.all(
        targetDates.map((dateValue) =>
          apiClient.get("/orders/by-line-date", {
            params: { date: dateValue, status: conditions.status || undefined },
          }),
        ),
      );
      const byId = new Map<string, OrderSummary>();
      responses.forEach((response) => {
        (response.data?.orders || []).forEach((order: OrderSummary) => {
          if (order?.id) byId.set(order.id, order);
        });
      });
      if (!owner.current()) return;
      setWeek(conditions.savedWeek || conditions.week);
      setStatus(conditions.status);
      setOrders(Array.from(byId.values()));
    } catch (error) {
      if (owner.ownsMessage()) {
        setMessage("対象週の注文一覧を取得できませんでした。");
      }
      throw error;
    } finally {
      if (owner.current()) setLoading(false);
    }
  };

  useEffect(() => {
    if (periodReady && weeklyWeightConditionsPeriod(conditionForm.getValues())) {
      void loadWeeklyOrders(conditionForm.getValues()).catch(() => undefined);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resolvedQuery, periodReady]);

  const downloadWeeklyWeight = async (conditions: WeeklyWeightConditions) => {
    const owner = beginOperation("output", conditions);
    if (!owner.current()) return;
    const period = weeklyWeightConditionsPeriod(conditions);
    if (!period) {
      setMessage("対象期間を確認してください。");
      return;
    }
    setDownloading(true);
    setMessage("週別重量表Excelを作成中です。");
    try {
      const res = await apiClient.get("/outputs/weekly-weight", {
        params: { start: period.start, end: period.end, status: conditions.status || undefined },
        responseType: "blob",
        timeout: 0,
      });
      if (!owner.current()) return;
      const contentDisposition = res.headers?.["content-disposition"] || res.headers?.["Content-Disposition"];
      const filename = extractFilename(contentDisposition) || `weekly_weight_${period.start}_${period.end}.xlsx`;
      const blob = res.data instanceof Blob ? res.data : new Blob([res.data]);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      if (owner.ownsMessage()) setMessage("週別重量表Excelをダウンロードしました。");
    } catch (err: any) {
      if (!owner.ownsMessage()) return;
      const detail = await extractErrorDetail(err);
      if (!owner.ownsMessage()) return;
      setMessage(detail ? `週別重量表Excelの作成に失敗しました: ${detail}` : "週別重量表Excelの作成に失敗しました。");
    } finally {
      if (owner.current()) setDownloading(false);
    }
  };

  return (
    <main className="page">
      <header className="hero">
        <div>
          <p className="eyebrow">週別出力</p>
          <h1>週別重量表</h1>
          <p className="subtle">日別出力と同じ条件で、対象週の重量表Excelを作成します。</p>
        </div>
        <TopNav />
      </header>

      <section className="panel">
        <header className="panel-header">
          <h2>フィルタ</h2>
          <span className="badge">対象 {orders.length} 件</span>
        </header>
        <FormProvider {...conditionForm}>
          <Stack component="form" direction={{ xs: "column", sm: "row" }} spacing={2} onSubmit={conditionForm.handleSubmit((values) => void loadWeeklyOrders(values).catch(() => undefined))}>
            <FormTextField<WeeklyWeightConditions> name="week" label="週" type="week" rules={{ required: "週を指定してください。" }} />
            <FormTextField<WeeklyWeightConditions> name="status" label="ステータス" select>
              <MenuItem value="">全て</MenuItem><MenuItem value="未着">未着</MenuItem><MenuItem value="要確認">要確認</MenuItem><MenuItem value="確定">確定</MenuItem><MenuItem value="エラー">エラー</MenuItem>
            </FormTextField>
            <Button type="submit" variant="contained" disabled={!periodReady || loading}>{loading ? "集計中..." : "表示・集計"}</Button>
            <Button type="button" variant="outlined" disabled={!periodReady || downloading} onClick={() => void conditionForm.handleSubmit(downloadWeeklyWeight)()}>{downloading ? "出力中..." : "Excel出力"}</Button>
          </Stack>
        </FormProvider>
        <div className="filters">
          <Link href="/daily-delivery-notes" className="btn ghost">
            日別出力へ
          </Link>
        </div>
        <p className="subtle helper-text">
          適用済み対象週: {weekLabel || "-"}。入力条件は「表示・集計」を実行するまで現在の結果を置き換えません。
        </p>
      </section>

      <section className="panel">
        <header className="panel-header">
          <h2>過去の週次</h2>
          <span className="badge">{weekHistory.length} 週</span>
        </header>
        <div className="week-history">
          {weekHistory.length === 0 ? (
            <p className="subtle">履歴なし</p>
          ) : (
            weekHistory.map((item) => (
              <Button
                className={`week-chip${item.week === week ? " active" : ""} delivery-ordinary-control`}
                key={item.week}
                type="button"
                onClick={() => {
                  const period = storedWeeklyWeightPeriod(item.week);
                  if (period) conditionForm.reset({ week: weekInputForPeriod(period), savedWeek: item.week, status: conditionForm.getValues("status") });
                }}
              >
                <span>{item.label}</span>
                <small>{item.orderCount}件</small>
              </Button>
            ))
          )}
        </div>
      </section>

      {message ? <p className="message">{message}</p> : null}

      <section className="panel">
        <header className="panel-header">
          <h2>対象週の注文一覧</h2>
          <span className="badge">{loading ? "更新中" : "自動更新"}</span>
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
                    <td>{order.facility || "-"}</td>
                    <td>{order.week || "未確定"}</td>
                    <td>{order.status || "-"}</td>
                    <td>{formatTimestamp(order.received_at)}</td>
                    <td className="numeric">{order.line_count ?? "-"}</td>
                    <td className="actions">
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

      <style jsx>{`
        :global(.delivery-ordinary-control.MuiButton-root) {
          text-transform: none;
          letter-spacing: inherit;
          min-width: 0;
        }
        .page {
          min-height: 100vh;
          padding: 48px 6vw 80px;
        }
        :global(body) {
          background: radial-gradient(circle at top left, #f8f4ea, #f4f7f6 40%, #eef1f0 100%);
          color: #1f2a2a;
          font-family: "Manrope", "Noto Sans JP", sans-serif;
        }
        :global(*) {
          box-sizing: border-box;
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
        h1,
        h2 {
          margin: 0;
        }
        h1 {
          font-size: clamp(26px, 4vw, 36px);
          margin-bottom: 12px;
        }
        h2 {
          font-size: 18px;
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
          gap: 16px;
          align-items: center;
          margin-bottom: 16px;
        }
        .badge {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          min-height: 30px;
          border-radius: 999px;
          padding: 0 12px;
          background: #f0f4f2;
          color: #33443f;
          font-size: 12px;
          font-weight: 800;
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
          font-weight: 800;
        }
        .input {
          border: 1px solid rgba(25, 32, 30, 0.14);
          border-radius: 10px;
          font-size: 14px;
          min-height: 40px;
          padding: 8px 10px;
        }
        .btn {
          min-height: 40px;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          border-radius: 999px;
          border: none;
          padding: 0 14px;
          font-size: 14px;
          font-weight: 700;
          text-decoration: none;
          cursor: pointer;
        }
        .btn.primary {
          background: #1f2a2a;
          color: #f7f2e7;
        }
        .btn.ghost {
          background: #e6ebe9;
          color: #1f2a2a;
        }
        .btn:disabled {
          opacity: 0.55;
          cursor: not-allowed;
        }
        .helper-text {
          margin-top: 12px;
        }
        .message {
          margin: 0 0 20px;
          padding: 12px 14px;
          border-radius: 12px;
          background: #eff7f4;
          border: 1px solid rgba(31, 42, 42, 0.1);
          color: #1f2a2a;
        }
        .week-history {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
          gap: 10px;
        }
        .week-chip,
        :global(.delivery-ordinary-control.week-chip) {
          min-height: 58px;
          border: 1px solid rgba(25, 32, 30, 0.1);
          border-radius: 12px;
          background: #fbfbf9;
          color: #1f2a2a;
          cursor: pointer;
          display: flex;
          flex-direction: column;
          justify-content: center;
          gap: 4px;
          padding: 10px 12px;
          text-align: left;
        }
        .week-chip.active,
        :global(.delivery-ordinary-control.week-chip.active) {
          background: #1f2a2a;
          border-color: #1f2a2a;
          color: #f7f2e7;
        }
        .week-chip span,
        :global(.delivery-ordinary-control.week-chip span) {
          font-size: 13px;
          font-weight: 800;
        }
        .week-chip small,
        :global(.delivery-ordinary-control.week-chip small) {
          color: inherit;
          font-size: 12px;
          opacity: 0.75;
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
          border-bottom: 1px solid rgba(25, 32, 30, 0.08);
          padding: 12px 10px;
          text-align: left;
          white-space: nowrap;
        }
        th {
          color: #5f7b74;
          font-size: 12px;
          font-weight: 800;
        }
        .numeric {
          text-align: right;
        }
        .actions {
          text-align: right;
        }
        .link {
          color: #1f6f64;
          font-weight: 800;
          text-decoration: none;
        }
        @media (max-width: 720px) {
          .page {
            padding: 28px 16px 48px;
          }
          .hero {
            display: grid;
          }
          .panel-header {
            align-items: flex-start;
            flex-direction: column;
          }
          .filters,
          .field,
          .btn {
            width: 100%;
          }
        }
      `}</style>
    </main>
  );
}

export default function WeeklyWeightOutputPage() {
  const sessionKey = useSyncExternalStore(subscribeBrowserSession, browserSessionKey, () => "server");
  return <SawaProvider sessionKey={`hospital-weekly-weight-output:${sessionKey}`}><WeeklyWeightOutputScreen /></SawaProvider>;
}
