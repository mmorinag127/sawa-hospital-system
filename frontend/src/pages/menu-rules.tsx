import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { Box, Button, Divider, MenuItem, Stack, Typography } from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { DataTable, EditForm, FormTextField, RequestState, SawaProvider, type TableColumn } from "@sawa/ui";
import TopNav from "../components/TopNav";
import { browserSessionKey, subscribeBrowserSession } from "../services/browserSession";
import { apiClient, hasActiveSessionAuthHeader } from "../services/apiClient";
import { DIET_TYPE_OPTIONS } from "../services/menuVocabulary";
import { useH1EditorOwner } from "../hooks/useH1EditorOwner";
import { useH1LeaveGuard } from "../hooks/useH1LeaveGuard";
import type { components } from "../generated/menu-rule-api";

type RuleListResponse = components["schemas"]["MenuRuleListResponse"];
type RuleSaveResponse = components["schemas"]["MenuRuleSaveResponse"];
type RuleUpdateResponse = components["schemas"]["MenuRuleUpdateResponse"];
type RuleDeleteResponse = components["schemas"]["MenuRuleDeleteResponse"];

type RuleType = "global" | "menu" | "facility";
type MenuRule = { id?: string; rule_type: RuleType; match_type?: string | null; menu_pattern?: string | null; facility_id?: string | null; daypart?: string | null; category?: string | null; diet_type?: string | null; unit_type?: string | null; qty_per_serving?: number | string | null; priority?: number | string | null; active?: boolean };
type RuleSnapshot = { rules: MenuRule[]; revision: string };
type SelectedRule = { item: MenuRule; baseline: RuleSnapshot };
const units = [{ value: "g", label: "グラム(g)" }, { value: "count", label: "個数" }, { value: "cut", label: "切" }];
const matches = [{ value: "exact", label: "完全一致" }, { value: "contains", label: "部分一致" }, { value: "regex", label: "正規表現" }];
const title: Record<RuleType, string> = { global: "基本ルール", menu: "メニュー例外", facility: "施設例外" };
const menuRulePaths = ["/menu-rules", "/hospital/menu-rules"];
const blank = (rule_type: RuleType): MenuRule => ({ rule_type, match_type: rule_type === "global" ? null : "contains", menu_pattern: "", facility_id: "", daypart: "", category: "", diet_type: "", unit_type: "g", qty_per_serving: "", priority: "", active: true });
const numberOrNull = (value: unknown) => value === "" || value == null ? null : Number.isFinite(Number(value)) ? Number(value) : null;
const requireRules = (value: unknown, ruleType: RuleType) => {
  if (!Array.isArray(value)) throw new Error("メニュールール一覧の応答に rules 配列がありません。");
  const ids = new Set<string>();
  for (const rule of value) {
    if (!rule || typeof rule.id !== "string" || !rule.id.trim() || ids.has(rule.id) || rule.rule_type !== ruleType)
      throw new Error("メニュールールのIDまたは種類を一意に確認できませんでした。");
    ids.add(rule.id);
  }
  return value as MenuRule[];
};
const toSnapshot = (value: { rules?: unknown; revision?: unknown }, ruleType: RuleType): RuleSnapshot => { if (typeof value.revision !== "string" || !value.revision) throw new Error("メニュールール一覧の読取版がありません。"); return { rules: requireRules(value.rules, ruleType), revision: value.revision }; };
const payload = (rule: MenuRule) => ({ ...rule, match_type: rule.match_type || null, menu_pattern: rule.menu_pattern || null, facility_id: rule.facility_id || null, daypart: rule.daypart || null, category: rule.category || null, diet_type: rule.diet_type || null, unit_type: rule.unit_type || null, qty_per_serving: numberOrNull(rule.qty_per_serving), priority: numberOrNull(rule.priority), active: rule.active ?? true });
const errorText = (error: any) => error?.response?.status === 409 ? "他の編集が保存されています。入力値は保持されています。再読込して内容を確認してください。" : typeof error?.response?.data?.detail === "string" ? error.response.data.detail : error instanceof Error && error.message ? error.message : "保存に失敗しました。入力値を確認して再試行してください。";
const validate = (values: MenuRule) => {
  if (values.rule_type !== "global" && !values.match_type) throw new Error("マッチ方法を選択してください。");
  if (values.rule_type !== "global" && !String(values.menu_pattern || "").trim()) throw new Error("メニュー名またはパターンを入力してください。");
  if (values.rule_type === "facility" && !String(values.facility_id || "").trim()) throw new Error("施設IDを入力してください。");
  if (values.qty_per_serving !== "" && values.qty_per_serving != null && numberOrNull(values.qty_per_serving) == null) throw new Error("数量は数値で入力してください。");
  if (values.priority !== "" && values.priority != null && numberOrNull(values.priority) == null) throw new Error("優先度は数値で入力してください。");
};

function Fields({ ruleType }: { ruleType: RuleType }) { return <>
  {ruleType === "facility" && <FormTextField<MenuRule> name="facility_id" label="施設ID" rules={{ required: "施設IDは必須です。" }} />}
  {ruleType !== "global" && <><FormTextField<MenuRule> name="match_type" label="マッチ方法" select rules={{ required: "マッチ方法を選択してください。" }}><MenuItem value="">未選択</MenuItem>{matches.map((choice) => <MenuItem key={choice.value} value={choice.value}>{choice.label}</MenuItem>)}</FormTextField><FormTextField<MenuRule> name="menu_pattern" label="メニュー名またはパターン" rules={{ required: "メニュー名またはパターンを入力してください。" }} /></>}
  <FormTextField<MenuRule> name="daypart" label="朝昼夕" /><FormTextField<MenuRule> name="category" label="区分" /><FormTextField<MenuRule> name="diet_type" label="食種" select><MenuItem value="">未選択</MenuItem>{DIET_TYPE_OPTIONS.map((choice) => <MenuItem key={choice.value} value={choice.value}>{choice.label}</MenuItem>)}</FormTextField><FormTextField<MenuRule> name="unit_type" label="単位" select><MenuItem value="">未選択</MenuItem>{units.map((choice) => <MenuItem key={choice.value} value={choice.value}>{choice.label}</MenuItem>)}</FormTextField><FormTextField<MenuRule> name="qty_per_serving" label="数量" type="number" parseValue={numberOrNull} formatValue={(value) => value == null ? "" : String(value)} /><FormTextField<MenuRule> name="priority" label="優先度" type="number" parseValue={numberOrNull} formatValue={(value) => value == null ? "" : String(value)} /><FormTextField<MenuRule> name="active" label="有効" select parseValue={(value) => value === "true"} formatValue={(value) => value === false ? "false" : "true"}><MenuItem value="true">有効</MenuItem><MenuItem value="false">無効</MenuItem></FormTextField>
</>; }

function RuleSection({ ruleType, authenticated }: { ruleType: RuleType; authenticated: boolean }) {
  const queryClient = useQueryClient(); const [selected, setSelected] = useState<SelectedRule | null>(null); const [dirty, setDirty] = useState(false); const [notice, setNotice] = useState(""); const [page, setPage] = useState(0); const [rowsPerPage, setRowsPerPage] = useState(25); const generation = useRef(0); const active = useRef(false); useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  const ownership = useH1EditorOwner(generation.current);
  useH1LeaveGuard(dirty, menuRulePaths);
  const reloadTargetRef = useRef<string | undefined>(undefined);
  const list = useQuery({ queryKey: ["menu-rules", ruleType], enabled: authenticated, queryFn: async ({ signal }) => {
    const targetId = reloadTargetRef.current;
    const snapshot = toSnapshot((await apiClient.get<RuleListResponse>("/menu-rules", { params: { rule_type: ruleType }, signal })).data || {}, ruleType);
    if (targetId && !snapshot.rules.some((rule) => rule.id === targetId)) throw new Error("再読込対象のメニュールールIDを確認できませんでした。");
    return snapshot;
  } });
  const reloadRules = async () => {
    reloadTargetRef.current = selected?.item.id;
    try { const result = await list.refetch(); if (result.error) throw result.error; if (!result.data) throw new Error("メニュールール一覧の再読込結果がありません。"); return result.data; }
    finally { reloadTargetRef.current = undefined; }
  };
  const reloadList = async () => { const owner = ownership.start(); try { owner.requireCurrent(); await reloadRules(); owner.requireCurrent(); } catch (error) { if (owner.current()) setNotice(errorText(error)); } };
  const save = useMutation({ mutationFn: async ({ values, baseline, current }: { values: MenuRule; baseline: RuleSnapshot; current: () => void }) => { validate(values); const body = payload(values); const headers = { "If-Match": baseline.revision }; current(); const response = values.id ? await apiClient.put<RuleUpdateResponse>(`/menu-rules/${values.id}`, body, { headers }) : await apiClient.post<RuleSaveResponse>("/menu-rules", body, { headers }); if ((values.id && response.data?.updated !== true) || !response.data?.rule?.id || typeof response.data?.revision !== "string") throw new Error("保存結果を確認できませんでした。"); const saved = response.data.rule as MenuRule; requireRules([saved], ruleType); if (values.id && saved.id !== values.id) throw new Error("保存結果のメニュールールIDが一致しません。"); const snapshot = toSnapshot({ rules: values.id ? baseline.rules.map((rule) => rule.id === saved.id ? saved : rule) : [...baseline.rules, saved], revision: response.data.revision }, ruleType); return { saved, snapshot }; } });
  const remove = useMutation({ mutationFn: async ({ item, baseline, current }: { item: MenuRule; baseline: RuleSnapshot; current: () => void }) => { if (!item.id) throw new Error("未保存のルールは保存前に削除してください。"); current(); const response = await apiClient.delete<RuleDeleteResponse>(`/menu-rules/${item.id}`, { headers: { "If-Match": baseline.revision } }); if (response.data?.deleted !== true) throw new Error("削除結果を確認できませんでした。"); return item.id; } });
  const choose = (item: MenuRule | null, baseline = list.data) => { if (dirty && !window.confirm("未保存の変更を破棄して別のルールを開きますか？")) return; if (item && !baseline) { setNotice("読取版がありません。"); return; } ownership.edit(); generation.current += 1; setDirty(false); setSelected(item && baseline ? { item, baseline } : null); };
  const rows = useMemo(() => list.data?.rules || [], [list.data]); const visibleRows = rows.slice(page * rowsPerPage, (page + 1) * rowsPerPage);
  const deleteRule = (rule: MenuRule) => {
    const selectedTarget = selected?.item.id === rule.id ? selected : null;
    if (selectedTarget && dirty && !window.confirm("未保存の変更を破棄して削除しますか？")) return;
    if (!window.confirm("このルールを削除しますか？")) return;
    const baseline = selectedTarget?.baseline || list.data;
    if (!baseline) { setNotice("読取版がありません。"); return; }
    const owner = ownership.start();
    void remove.mutateAsync({ item: rule, baseline, current: owner.requireCurrent }).then(async () => {
      owner.requireCurrent();
      await queryClient.invalidateQueries({ queryKey: ["menu-rules", ruleType] });
      owner.requireCurrent();
      if (selectedTarget) { ownership.edit(); generation.current += 1; setDirty(false); setSelected(null); }
    }).catch((error) => { if (owner.current()) setNotice(errorText(error)); });
  };
  const columns: readonly TableColumn<MenuRule>[] = [
    ...(ruleType === "facility" ? [{ id: "facility", label: "施設ID", render: (rule: MenuRule) => rule.facility_id || "-" }] : []),
    ...(ruleType !== "global" ? [{ id: "match", label: "マッチ", render: (rule: MenuRule) => matches.find((choice) => choice.value === rule.match_type)?.label || rule.match_type || "-" }, { id: "pattern", label: "メニュー", render: (rule: MenuRule) => rule.menu_pattern || "-" }] : []),
    { id: "daypart", label: "朝昼夕", render: (rule) => rule.daypart || "-" }, { id: "category", label: "区分", render: (rule) => rule.category || "-" }, { id: "diet", label: "食種", render: (rule) => DIET_TYPE_OPTIONS.find((choice) => choice.value === rule.diet_type)?.label || rule.diet_type || "-" }, { id: "unit", label: "単位", render: (rule) => units.find((choice) => choice.value === rule.unit_type)?.label || rule.unit_type || "-" }, { id: "qty", label: "数量", align: "right", render: (rule) => rule.qty_per_serving ?? "-" }, { id: "priority", label: "優先度", align: "right", render: (rule) => rule.priority ?? "-" }, { id: "active", label: "有効", render: (rule) => rule.active === false ? "無効" : "有効" }, { id: "actions", label: "", render: (rule) => <Stack direction="row"><Button size="small" onClick={() => choose(rule)}>編集</Button><Button size="small" color="error" onClick={() => deleteRule(rule)}>削除</Button></Stack> },
  ];
  return <Box component="section"><Stack direction={{ xs: "column", sm: "row" }} spacing={1} sx={{ mb: 2, justifyContent: "space-between" }}><Typography component="h2" variant="h6">{title[ruleType]}</Typography><Button onClick={() => choose(blank(ruleType))}>追加</Button></Stack>{notice && <Typography color="error">{notice}</Typography>}{list.data ? <DataTable label={`${title[ruleType]}一覧`} rows={visibleRows} columns={columns} rowKey={(rule) => rule.id || `${rule.rule_type}-${rule.menu_pattern}-${rule.facility_id}-${rule.priority}`} total={rows.length} page={page} rowsPerPage={rowsPerPage} onPageChange={setPage} onRowsPerPageChange={(next) => { setRowsPerPage(next); setPage(0); }} pending={list.isFetching} error={list.isError ? errorText(list.error) : undefined} onRetry={() => void reloadList()} /> : <RequestState pending={list.isFetching} error={list.isError ? errorText(list.error) : undefined} onRetry={() => void reloadList()} />}{selected && <Box sx={{ mt: 2 }}><EditForm<MenuRule> key={`${selected.item.id || "new"}-${generation.current}`} label={selected.item.id ? `${title[ruleType]}を編集` : `${title[ruleType]}を追加`} defaultValues={selected.item} beginOperation={ownership.beginOperation} onDirtyChange={setDirty} onValuesChange={(values) => ownership.edit(values)} classifyError={(error) => ({ kind: (error as { response?: { status?: unknown } } | null | undefined)?.response?.status === 409 ? "conflict" as const : "error" as const, message: errorText(error) })} onSave={async (values) => { const owner = ownership.formOperation(); const result = await save.mutateAsync({ values: { ...selected.item, ...values, rule_type: ruleType }, baseline: selected.baseline, current: owner.requireCurrent }); if (!owner.current()) throw new Error("選択が変わったため保存結果を適用しませんでした。"); queryClient.setQueryData(["menu-rules", ruleType], result.snapshot); setSelected({ item: result.saved, baseline: result.snapshot }); setDirty(false); return result.saved; }} onReload={async () => { if (!selected.item.id) return selected.item; if (dirty && !window.confirm("未保存の変更を破棄して再読込しますか？")) throw new Error("再読込を取り消しました。"); const owner = ownership.formOperation(); const target = selected.item.id; const refreshed = await reloadRules(); const current = refreshed.rules.find((rule) => rule.id === target); if (!current || !owner.current()) throw new Error("選択が変わったため再読込結果を適用しませんでした。"); generation.current += 1; setDirty(false); setSelected({ item: current, baseline: refreshed }); return current; }}><Fields ruleType={ruleType} /></EditForm></Box>}</Box>;
}

function Screen({ authenticated }: { authenticated: boolean }) { if (!authenticated) return <Box sx={{ p: 3 }}>ログインを確認しています。</Box>; return <Box sx={{ maxWidth: 1440, mx: "auto", px: { xs: 2, md: 3 }, py: 3, overflowWrap: "anywhere" }}><Stack spacing={3}><Box component="header"><Typography component="h1" variant="h5">メニュールール管理</Typography><Typography color="text.secondary">基本量・メニュー例外・施設例外を管理します。</Typography></Box><TopNav /><RuleSection ruleType="global" authenticated={authenticated} /><Divider /><RuleSection ruleType="menu" authenticated={authenticated} /><Divider /><RuleSection ruleType="facility" authenticated={authenticated} /></Stack></Box>; }
export default function MenuRulesPage() { const sessionKey = useSyncExternalStore(subscribeBrowserSession, browserSessionKey, () => "server"); const authenticated = useSyncExternalStore(subscribeBrowserSession, hasActiveSessionAuthHeader, () => false); return <SawaProvider sessionKey={`hospital-menu-rules:${sessionKey}`}><Screen authenticated={authenticated} /></SawaProvider>; }
