import Link from "next/link";
import { useMemo, useRef, useState, useSyncExternalStore } from "react";
import { Box, Button, Checkbox, MenuItem, Stack, Typography } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import { DataTable, FormTextField, RequestState, SawaProvider, type TableColumn } from "@sawa/ui";
import { FormProvider, useForm } from "react-hook-form";
import TopNav from "./TopNav";
import { apiClient } from "../services/apiClient";
import { browserSessionKey, subscribeBrowserSession } from "../services/browserSession";
import { fetchFacilityNameMap, fetchOrderFacilityCandidateBatch, type FacilityCandidate } from "../services/facilityData";

type OrderSummary = {
  source_kind?: string;
  uploaded_pdf_id?: string;
  id: string;
  facility?: string | null;
  week?: string | null;
  status?: string | null;
  received_at?: string | null;
  line_count?: number | null;
  quantity?: number | null;
  unit?: string | null;
};
type Conditions = { week: string; facility: string; status: string; date: string };
type AppliedConditions = Conditions & { generation: number };
type WeeklyProgress = { week: string; pending: number; review: number; confirmed: number; error: number };
type OrderCursor = { id: string; source_kind: string };
type OrderOverview = {orders: OrderSummary[]; total: number; facility_options: string[]; week_options: string[]; progress: WeeklyProgress[]; next_cursor?: OrderCursor | null};

const statuses = ["", "未着", "要確認", "確定", "エラー"];
const time = (value?: string | null) => value ? new Date(value).toLocaleString("ja-JP") : "-";
const error = (reason: any) => String(reason?.response?.data?.detail || reason?.message || "取得に失敗しました。");

function OrdersListContent() {
  const form = useForm<Conditions>({ defaultValues: { week: "", facility: "", status: "", date: new Date().toISOString().slice(0, 10) } });
  const [applied, setApplied] = useState<AppliedConditions>({ week: "", facility: "", status: "", date: new Date().toISOString().slice(0, 10), generation: 0 });
  const [page, setPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(25);
  const cursors = useRef(new Map<string, OrderCursor>());
  const cursorScope = JSON.stringify([applied, rowsPerPage]);
  const [progressPage, setProgressPage] = useState(0);
  const [progressRowsPerPage, setProgressRowsPerPage] = useState(25);
  const [dailyPage, setDailyPage] = useState(0);
  const [dailyRowsPerPage, setDailyRowsPerPage] = useState(25);
  const [showFacilityHints, setShowFacilityHints] = useState(false);
  const facilities = useQuery({ queryKey: ["order-list", "facilities"], queryFn: fetchFacilityNameMap });
  const orders = useQuery({
    queryKey: ["facility-order-list", applied, page, rowsPerPage],
    queryFn: async ({ signal }) => {
      const before = page ? cursors.current.get(`${cursorScope}:${page}`) : undefined;
      const result = (await apiClient.get<OrderOverview>("/orders/facility-overview", {
        params: {facility: applied.facility || undefined, week: applied.week || undefined, status: applied.status || undefined,
          limit: rowsPerPage, offset: before ? undefined : page * rowsPerPage,
          before_id: before?.id, before_source_kind: before?.source_kind}, signal,
      })).data;
      if (!signal.aborted) {
        if (result.next_cursor) cursors.current.set(`${cursorScope}:${page + 1}`, result.next_cursor);
        else cursors.current.delete(`${cursorScope}:${page + 1}`);
      }
      return result;
    },
  });
  const daily = useQuery({
    queryKey: ["facility-order-list", "daily", applied],
    enabled: Boolean(applied.date),
    queryFn: async ({ signal }) => (await apiClient.get<{ orders?: OrderSummary[] }>("/orders/by-line-date", {
      params: { date: applied.date, summary_only: true, ...(applied.facility && applied.facility !== "__unresolved__" ? { facility: applied.facility } : {}) },
      signal,
    })).data?.orders || [],
  });
  const rows = orders.data?.orders || [];
  const dailyRows = useMemo(() => (daily.data || []).filter((row) => (
    (!applied.status || row.status === applied.status)
    && !(applied.facility === "__unresolved__" && row.facility)
  )), [daily.data, applied.facility, applied.status]);
  const visibleRows = rows;
  const changeOrderPage = (next: number) => {
    if (!Number.isInteger(next) || next < 0) return;
    if (next === page + 1 && orders.data?.next_cursor) cursors.current.set(`${cursorScope}:${next}`, orders.data.next_cursor);
    if (next === 0 || cursors.current.has(`${cursorScope}:${next}`)) setPage(next);
  };
  const visibleDailyRows = dailyRows.slice(dailyPage * dailyRowsPerPage, (dailyPage + 1) * dailyRowsPerPage);
  const candidateRows = Array.from(new Map([...visibleRows, ...visibleDailyRows]
    .filter(row => !row.facility && row.source_kind !== "uploaded_pdf").map(row => [row.id, row])).values());
  const candidates = useQuery({
    queryKey: ["order-facility-candidates", showFacilityHints, candidateRows.map(row => row.id), orders.dataUpdatedAt, daily.dataUpdatedAt],
    enabled: showFacilityHints && candidateRows.length > 0,
    queryFn: ({ signal }) => fetchOrderFacilityCandidateBatch(candidateRows.map(row => row.id), signal),
  });
  const candidateByOrder = useMemo(() => {
    const result: Record<string, { values?: FacilityCandidate[]; failure?: string; pending?: boolean }> = {};
    candidateRows.forEach(row => {
      const item = candidates.data?.[row.id];
      if (candidates.isPending || candidates.isFetching) result[row.id] = { pending: true };
      else if (candidates.isError) result[row.id] = { failure: error(candidates.error) };
      else if (item?.error) result[row.id] = { failure: item.error };
      else if (item) result[row.id] = { values: item.candidates };
    });
    return result;
  }, [candidateRows, candidates]);
  const facilityOptions = orders.data?.facility_options || [];
  const weekOptions = orders.data?.week_options || [];
  const progressRows = orders.data?.progress || [];
  const tableColumns: readonly TableColumn<OrderSummary>[] = [
    { id: "facility", label: "施設", render: (row) => row.facility ? (facilities.data?.[row.facility] ? `${facilities.data[row.facility]} (${row.facility})` : row.facility) : "未確定" },
    { id: "candidate", label: "未確定候補", render: (row) => {
      if (row.facility) return "-";
      if (!showFacilityHints) return "未取得";
      const state = candidateByOrder[row.id];
      if (state?.pending) return "候補取得中...";
      if (state?.failure) return `候補取得失敗: ${state.failure}`;
      return state?.values?.map((item) => item.facility_name || item.facility_id).join(" / ") || "候補なし";
    } },
    { id: "week", label: "週", render: (row) => row.week || "未確定" },
    { id: "status", label: "状態", render: (row) => row.status || "-" },
    { id: "quantity", label: "数量", align: "right", render: (row) => row.quantity ?? "-" },
    { id: "unit", label: "単位", render: (row) => row.unit || "-" },
    { id: "lines", label: "行数", align: "right", render: (row) => row.line_count ?? "-" },
    { id: "received", label: "受信日時", render: (row) => time(row.received_at) },
    { id: "open", label: "出力・詳細", render: (row) => <Link href={row.source_kind === "uploaded_pdf" ? `/pdf-upload?uploaded_pdf_id=${encodeURIComponent(row.uploaded_pdf_id || "")}` : `/orders/${row.id}`}>詳細</Link> },
  ];
  const submit = form.handleSubmit((values) => {
    cursors.current.clear();
    setApplied({ ...values, generation: applied.generation + 1 });
    setPage(0);
    setProgressPage(0);
    setDailyPage(0);
  });
  const appliedLabel = [applied.week && `週: ${applied.week}`, applied.facility && `施設: ${applied.facility === "__unresolved__" ? "未確定" : applied.facility}`, applied.status && `状態: ${applied.status}`].filter(Boolean).join(" / ") || "全件";
  const visibleProgressRows = progressRows.slice(progressPage * progressRowsPerPage, (progressPage + 1) * progressRowsPerPage);
  const progressColumns: readonly TableColumn<WeeklyProgress>[] = [
    { id: "week", label: "週", render: (row) => row.week },
    { id: "pending", label: "未着", align: "right", render: (row) => row.pending },
    { id: "review", label: "要確認", align: "right", render: (row) => row.review },
    { id: "confirmed", label: "確定", align: "right", render: (row) => row.confirmed },
    { id: "error", label: "エラー", align: "right", render: (row) => row.error },
  ];
  return <Box sx={{ maxWidth: 1440, mx: "auto", px: 3, py: 3 }}>
    <Stack spacing={3}>
      <Box component="header"><Typography variant="h5">施設別注文</Typography><Typography color="text.secondary">適用条件: {appliedLabel} / {rows.length}件</Typography></Box>
      <TopNav />
      <FormProvider {...form}><Box component="form" onSubmit={submit}><Stack direction={{ xs: "column", md: "row" }} spacing={2}>
        <FormTextField<Conditions> name="week" label="週" select><MenuItem value="">全週</MenuItem>{weekOptions.map((week) => <MenuItem key={week} value={week}>{week}</MenuItem>)}</FormTextField>
        <FormTextField<Conditions> name="date" label="日付" type="date" rules={{ required: "日付を指定してください。" }} />
        <FormTextField<Conditions> name="facility" label="施設" select><MenuItem value="">全施設</MenuItem><MenuItem value="__unresolved__">未確定</MenuItem>{facilityOptions.map((id) => <MenuItem key={id} value={id}>{facilities.data?.[id] || id} ({id})</MenuItem>)}</FormTextField>
        <FormTextField<Conditions> name="status" label="状態" select>{statuses.map((value) => <MenuItem key={value || "all"} value={value}>{value || "全状態"}</MenuItem>)}</FormTextField>
        <Button type="submit" variant="contained" disabled={orders.isFetching}>表示</Button>
        <label><Checkbox checked={showFacilityHints} onChange={event => setShowFacilityHints(event.target.checked)} />施設候補</label>
      </Stack></Box></FormProvider>
      {facilities.isError ? <RequestState pending={false} error={error(facilities.error)} onRetry={() => void facilities.refetch()} /> : null}
      <DataTable label="週次進捗" rows={visibleProgressRows} columns={progressColumns} rowKey={(row) => row.week} total={progressRows.length} page={progressPage} rowsPerPage={progressRowsPerPage} onPageChange={setProgressPage} onRowsPerPageChange={(next) => { setProgressRowsPerPage(next); setProgressPage(0); }} pending={orders.isFetching} />
      {orders.data ? <DataTable label="施設別注文一覧" rows={visibleRows} columns={tableColumns} rowKey={(row) => row.id} total={orders.data.total} page={page} rowsPerPage={rowsPerPage} onPageChange={changeOrderPage} onRowsPerPageChange={(next) => { cursors.current.clear(); setRowsPerPage(next); setPage(0); }} pending={orders.isFetching} error={orders.isError ? error(orders.error) : undefined} onRetry={() => void orders.refetch()} /> : <RequestState pending={orders.isFetching} error={orders.isError ? error(orders.error) : undefined} onRetry={() => void orders.refetch()} />}
      <Box component="section"><Typography variant="h6">日別注文</Typography><Typography color="text.secondary">適用日: {applied.date}</Typography>{daily.data ? <DataTable label="日別注文一覧" rows={visibleDailyRows} columns={tableColumns} rowKey={(row) => row.id} total={dailyRows.length} page={dailyPage} rowsPerPage={dailyRowsPerPage} onPageChange={setDailyPage} onRowsPerPageChange={(next) => { setDailyRowsPerPage(next); setDailyPage(0); }} pending={daily.isFetching} error={daily.isError ? error(daily.error) : undefined} onRetry={() => void daily.refetch()} /> : <RequestState pending={daily.isFetching} error={daily.isError ? error(daily.error) : undefined} onRetry={() => void daily.refetch()} />}</Box>
    </Stack>
  </Box>;
}

export function OrdersListScreen() {
  const sessionKey = useSyncExternalStore(subscribeBrowserSession, browserSessionKey, () => "server");
  return <SawaProvider sessionKey={`hospital-facility-orders:${sessionKey}`}><OrdersListContent key={sessionKey} /></SawaProvider>;
}
