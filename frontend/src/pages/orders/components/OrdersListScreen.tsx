import Link from "next/link";
import { useMemo, useState, useSyncExternalStore } from "react";
import { Box, Button, MenuItem, Stack, Typography } from "@mui/material";
import { useQueries, useQuery } from "@tanstack/react-query";
import { DataTable, FormTextField, RequestState, SawaProvider, type TableColumn } from "@sawa/ui";
import { FormProvider, useForm } from "react-hook-form";
import TopNav from "../../../components/TopNav";
import { apiClient } from "../../../services/apiClient";
import { browserSessionKey, subscribeBrowserSession } from "../../../services/browserSession";
import { fetchFacilityNameMap, fetchOrderFacilityCandidates, type FacilityCandidate } from "../../../services/facilityData";

type OrderSummary = {
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

const statuses = ["", "未着", "要確認", "確定", "エラー"];
const time = (value?: string | null) => value ? new Date(value).toLocaleString("ja-JP") : "-";
const error = (reason: any) => String(reason?.response?.data?.detail || reason?.message || "取得に失敗しました。");

function OrdersListContent() {
  const form = useForm<Conditions>({ defaultValues: { week: "", facility: "", status: "", date: new Date().toISOString().slice(0, 10) } });
  const [applied, setApplied] = useState<AppliedConditions>({ week: "", facility: "", status: "", date: new Date().toISOString().slice(0, 10), generation: 0 });
  const [page, setPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(25);
  const [progressPage, setProgressPage] = useState(0);
  const [progressRowsPerPage, setProgressRowsPerPage] = useState(25);
  const [dailyPage, setDailyPage] = useState(0);
  const [dailyRowsPerPage, setDailyRowsPerPage] = useState(25);
  const facilities = useQuery({ queryKey: ["order-list", "facilities"], queryFn: fetchFacilityNameMap });
  const orders = useQuery({
    queryKey: ["facility-order-list", applied],
    queryFn: async ({ signal }) => (await apiClient.get<{ orders?: OrderSummary[] }>("/orders", { params: { include_ocr: false }, signal })).data?.orders || [],
  });
  const daily = useQuery({
    queryKey: ["facility-order-list", "daily", applied],
    enabled: Boolean(applied.date),
    queryFn: async ({ signal }) => (await apiClient.get<{ orders?: OrderSummary[] }>("/orders/by-line-date", {
      params: { date: applied.date, ...(applied.facility && applied.facility !== "__unresolved__" ? { facility: applied.facility } : {}) },
      signal,
    })).data?.orders || [],
  });
  const rows = useMemo(() => (orders.data || []).filter((order) => {
    if (applied.week && order.week !== applied.week) return false;
    if (applied.facility === "__unresolved__" && order.facility) return false;
    if (applied.facility && applied.facility !== "__unresolved__" && order.facility !== applied.facility) return false;
    return !applied.status || order.status === applied.status;
  }), [orders.data, applied]);
  const candidates = useQueries({
    queries: rows.filter((row) => !row.facility).map((row) => ({
      queryKey: ["order-facility-candidates", row.id],
      queryFn: () => fetchOrderFacilityCandidates(row.id),
      staleTime: 30_000,
    })),
  });
  const candidateByOrder = useMemo(() => {
    const result: Record<string, { values?: FacilityCandidate[]; failure?: string; pending?: boolean }> = {};
    rows.filter((row) => !row.facility).forEach((row, index) => {
      const query = candidates[index];
      if (query?.isPending || query?.isFetching) result[row.id] = { pending: true };
      else if (query?.isError) result[row.id] = { failure: error(query.error) };
      else if (query?.data) result[row.id] = { values: query.data };
    });
    return result;
  }, [rows, candidates]);
  const facilityOptions = useMemo(() => Array.from(new Set((orders.data || []).map((row) => row.facility).filter(Boolean) as string[])).sort(), [orders.data]);
  const weekOptions = useMemo(() => Array.from(new Set((orders.data || []).map((row) => String(row.week || "").trim()).filter(Boolean))).sort(), [orders.data]);
  const progressRows = useMemo<WeeklyProgress[]>(() => {
    const grouped: Record<string, WeeklyProgress> = {};
    rows.forEach((row) => {
      const week = row.week || "未確定";
      const current = grouped[week] || { week, pending: 0, review: 0, confirmed: 0, error: 0 };
      if (row.status === "未着") current.pending += 1;
      if (row.status === "要確認") current.review += 1;
      if (row.status === "確定") current.confirmed += 1;
      if (row.status === "エラー") current.error += 1;
      grouped[week] = current;
    });
    return Object.values(grouped).sort((left, right) => left.week.localeCompare(right.week));
  }, [rows]);
  const tableColumns: readonly TableColumn<OrderSummary>[] = [
    { id: "facility", label: "施設", render: (row) => row.facility ? (facilities.data?.[row.facility] ? `${facilities.data[row.facility]} (${row.facility})` : row.facility) : "未確定" },
    { id: "candidate", label: "未確定候補", render: (row) => {
      if (row.facility) return "-";
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
    { id: "open", label: "出力・詳細", render: (row) => <Link href={`/orders/${row.id}`}>詳細</Link> },
  ];
  const submit = form.handleSubmit((values) => {
    setApplied({ ...values, generation: applied.generation + 1 });
    setPage(0);
    setProgressPage(0);
    setDailyPage(0);
  });
  const appliedLabel = [applied.week && `週: ${applied.week}`, applied.facility && `施設: ${applied.facility === "__unresolved__" ? "未確定" : applied.facility}`, applied.status && `状態: ${applied.status}`].filter(Boolean).join(" / ") || "全件";
  const dailyRows = useMemo(() => (daily.data || []).filter((row) => (
    (!applied.status || row.status === applied.status)
    && !(applied.facility === "__unresolved__" && row.facility)
  )), [daily.data, applied.facility, applied.status]);

  const visibleRows = rows.slice(page * rowsPerPage, (page + 1) * rowsPerPage);
  const visibleProgressRows = progressRows.slice(progressPage * progressRowsPerPage, (progressPage + 1) * progressRowsPerPage);
  const visibleDailyRows = dailyRows.slice(dailyPage * dailyRowsPerPage, (dailyPage + 1) * dailyRowsPerPage);
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
      </Stack></Box></FormProvider>
      {facilities.isError ? <RequestState pending={false} error={error(facilities.error)} onRetry={() => void facilities.refetch()} /> : null}
      <DataTable label="週次進捗" rows={visibleProgressRows} columns={progressColumns} rowKey={(row) => row.week} total={progressRows.length} page={progressPage} rowsPerPage={progressRowsPerPage} onPageChange={setProgressPage} onRowsPerPageChange={(next) => { setProgressRowsPerPage(next); setProgressPage(0); }} pending={orders.isFetching} />
      {orders.data ? <DataTable label="施設別注文一覧" rows={visibleRows} columns={tableColumns} rowKey={(row) => row.id} total={rows.length} page={page} rowsPerPage={rowsPerPage} onPageChange={setPage} onRowsPerPageChange={(next) => { setRowsPerPage(next); setPage(0); }} pending={orders.isFetching} error={orders.isError ? error(orders.error) : undefined} onRetry={() => void orders.refetch()} /> : <RequestState pending={orders.isFetching} error={orders.isError ? error(orders.error) : undefined} onRetry={() => void orders.refetch()} />}
      <Box component="section"><Typography variant="h6">日別注文</Typography><Typography color="text.secondary">適用日: {applied.date}</Typography>{daily.data ? <DataTable label="日別注文一覧" rows={visibleDailyRows} columns={tableColumns} rowKey={(row) => row.id} total={dailyRows.length} page={dailyPage} rowsPerPage={dailyRowsPerPage} onPageChange={setDailyPage} onRowsPerPageChange={(next) => { setDailyRowsPerPage(next); setDailyPage(0); }} pending={daily.isFetching} error={daily.isError ? error(daily.error) : undefined} onRetry={() => void daily.refetch()} /> : <RequestState pending={daily.isFetching} error={daily.isError ? error(daily.error) : undefined} onRetry={() => void daily.refetch()} />}</Box>
    </Stack>
  </Box>;
}

export function OrdersListScreen() {
  const sessionKey = useSyncExternalStore(subscribeBrowserSession, browserSessionKey, () => "server");
  return <SawaProvider sessionKey={`hospital-facility-orders:${sessionKey}`}><OrdersListContent /></SawaProvider>;
}
