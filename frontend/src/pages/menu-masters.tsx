import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { useRouter } from "next/router";
import {
  Box,
  Button,
  Divider,
  MenuItem,
  Stack,
  Typography,
} from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  DataTable,
  EditForm,
  FormTextField,
  RequestState,
  SawaProvider,
  SearchBar,
  type TableColumn,
} from "@sawa/ui";
import TopNav from "../components/TopNav";
import { menuMasterOptionLabel, menuMasterTemperatures, menuMasterUnits } from "../components/menuMasterOptions";
import { apiClient, hasActiveSessionAuthHeader } from "../services/apiClient";
import {
  browserSessionKey,
  subscribeBrowserSession,
} from "../services/browserSession";
import { useMenuMasterLeaveGuard } from "../hooks/useMenuMasterLeaveGuard";
import {
  MENU_MASTER_PAGE_SIZE,
  type MenuMaster,
  type MenuMasterDraft,
  createMenuMasterPayload,
  listMenuMasterParams,
  menuMasterListUrl,
  menuMasterErrorMessage,
  parseMenuMaster,
  parseMenuMasterList,
  parseMenuMasterUrl,
  updateMenuMasterPayload,
} from "../services/menuMasters";

type ListResponse = { items: MenuMaster[]; total: number };
type ItemResponse = { item: MenuMaster };
const blank: MenuMasterDraft = {
  name: "",
  unit_type: null,
  qty_per_serving: null,
  bag_max_qty: null,
  bag_max_unit: null,
  temp_type: null,
  daypart: null,
  category: null,
  condiments: [],
};
const draft = (item: MenuMaster): MenuMasterDraft => ({
  name: item.name,
  unit_type: item.unit_type,
  qty_per_serving: item.qty_per_serving,
  bag_max_qty: item.bag_max_qty,
  bag_max_unit: item.bag_max_unit,
  temp_type: item.temp_type,
  daypart: item.daypart,
  category: item.category,
  condiments: item.condiments || [],
});
const number = (value: string) => (value.trim() === "" ? null : Number(value));
const condiments = (value: string) =>
  value
    .split(",")
    .map((v) => v.trim())
    .filter(Boolean);
const renderOptionLabel = (options: readonly { value: string; label: string }[], value: string | null) => (
  <Box component="span" sx={{ whiteSpace: options.some(option => option.value === value) ? "nowrap" : "normal" }}>
    {menuMasterOptionLabel(options, value)}
  </Box>
);
const classify = (error: any) => ({
  kind:
    error?.response?.status === 409
      ? ("conflict" as const)
      : ("error" as const),
  message:
    error?.response?.status === 409
      ? "他の編集が保存されています。再読込して内容を確認してください。"
      : menuMasterErrorMessage(error),
});

function Fields() {
  return (
    <>
      <FormTextField<MenuMasterDraft>
        name="name"
        label="メニュー名"
        rules={{ required: "メニュー名は必須です。" }}
      />
      <FormTextField<MenuMasterDraft> name="unit_type" label="単位" select>
        <MenuItem value="">未選択</MenuItem>
        {menuMasterUnits.map(option => <MenuItem key={option.value} value={option.value}>{option.label}</MenuItem>)}
      </FormTextField>
      <FormTextField<MenuMasterDraft>
        name="qty_per_serving"
        label="1人前数量"
        type="number"
        parseValue={number}
        formatValue={(value) => (typeof value === "number" ? value : "")}
      />
      <FormTextField<MenuMasterDraft>
        name="bag_max_qty"
        label="袋上限数量"
        type="number"
        parseValue={number}
        formatValue={(value) => (typeof value === "number" ? value : "")}
      />
      <FormTextField<MenuMasterDraft> name="bag_max_unit" label="袋単位" select>
        <MenuItem value="">未選択</MenuItem>
        {menuMasterUnits.map(option => <MenuItem key={option.value} value={option.value}>{option.label}</MenuItem>)}
      </FormTextField>
      <FormTextField<MenuMasterDraft> name="temp_type" label="温冷" select>
        <MenuItem value="">未選択</MenuItem>
        {menuMasterTemperatures.map(option => <MenuItem key={option.value} value={option.value}>{option.label}</MenuItem>)}
      </FormTextField>
      <FormTextField<MenuMasterDraft> name="daypart" label="食事帯" />
      <FormTextField<MenuMasterDraft> name="category" label="分類" />
      <FormTextField<MenuMasterDraft>
        name="condiments"
        label="付属品"
        helperText="カンマ区切り"
        parseValue={condiments}
        formatValue={(value) => (Array.isArray(value) ? value.join(", ") : "")}
      />
    </>
  );
}

function Screen({ authenticated }: { authenticated: boolean }) {
  const router = useRouter();
  const client = useQueryClient();
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<MenuMaster | null>(null);
  const [editDirty, setEditDirty] = useState(false);
  const [newDirty, setNewDirty] = useState(false);
  const editor = useRef({ generation: 0, id: null as string | null, dirty: false, active: true });
  useEffect(() => {
    editor.current.active = true;
    return () => { editor.current.active = false; };
  }, []);
  const changeDirty = useCallback((dirty: boolean) => {
    editor.current.dirty = dirty;
    setEditDirty(dirty);
  }, []);
  const choose = (item: MenuMaster) => {
    editor.current = { generation: editor.current.generation + 1, id: item.id, dirty: false, active: true };
    setEditDirty(false);
    setSelected(item);
  };
  const owns = (owner: { generation: number; id: string | null }) =>
    editor.current.active && owner.generation === editor.current.generation && owner.id === editor.current.id;
  useMenuMasterLeaveGuard({ dirty: editDirty || newDirty });
  const urlState = useMemo(() => {
    if (!router.isReady)
      return { ok: false as const, error: "一覧を準備しています。" };
    try {
      return { ok: true as const, value: parseMenuMasterUrl(router.asPath) };
    } catch (error) {
      return {
        ok: false as const,
        error:
          error instanceof Error
            ? error.message
            : "一覧のページ指定を確認してください。",
      };
    }
  }, [router.isReady, router.asPath]);
  useEffect(() => {
    if (urlState.ok) setSearch(urlState.value.q);
  }, [urlState]);
  const params = useMemo(
    () =>
      urlState.ok
        ? listMenuMasterParams(
            urlState.value.q,
            urlState.value.page,
            urlState.value.pageSize,
          )
        : null,
    [urlState],
  );
  const list = useQuery({
    queryKey: ["menu-masters", params],
    enabled: !!params && authenticated,
    queryFn: async ({ signal }) =>
      parseMenuMasterList(
        (
          await apiClient.get<ListResponse>("/menu-masters", {
            params: params!,
            signal,
          })
        ).data,
      ),
  });
  const create = useMutation({
    mutationFn: async (values: MenuMasterDraft) =>
      authenticated ?
      parseMenuMaster(
        (
          await apiClient.post<ItemResponse>(
            "/menu-masters",
            createMenuMasterPayload(values),
          )
        ).data.item,
      ) : Promise.reject(new Error("ログインを確認してください。")),
  });
  const refresh = () =>
    client.invalidateQueries({ queryKey: ["menu-masters"] });
  const replaceListUrl = (next: {
    q: string;
    page: number;
    pageSize: number;
  }) =>
    void router.push(menuMasterListUrl(router.asPath, next), undefined, {
      shallow: true,
    });
  const page = urlState.ok ? urlState.value.page : 0;
  const size = urlState.ok ? urlState.value.pageSize : MENU_MASTER_PAGE_SIZE;
  const applied = urlState.ok ? urlState.value.q : "";
  const pageOutOfRange = !!list.data && page > 0 && page * size >= list.data.total;
  const listError = !urlState.ok ? urlState.error : list.isError ? menuMasterErrorMessage(list.error) :
    pageOutOfRange ? "一覧のページが範囲外です。検索条件を確認してください。" : undefined;
  const retryList = () => { if (urlState.ok) void list.refetch(); };
  const searchNow = () =>
    replaceListUrl({ q: search, page: 0, pageSize: size });
  const select = (next: MenuMaster) => {
    if (selected?.id === next.id) return;
    if (
      editDirty &&
      !window.confirm("未保存の変更を破棄して別のメニューを開きますか？")
    )
      return;
    choose(next);
  };
  const columns: readonly TableColumn<MenuMaster>[] = [
    { id: "name", label: "メニュー名", render: (r) => r.name },
    { id: "unit", label: "単位", render: (r) => renderOptionLabel(menuMasterUnits, r.unit_type) },
    {
      id: "qty",
      label: "1人前",
      align: "right",
      render: (r) => r.qty_per_serving ?? "—",
    },
    {
      id: "bag",
      label: "袋上限",
      align: "right",
      render: (r) => r.bag_max_qty ?? "—",
    },
    { id: "bag-unit", label: "袋単位", render: (r) => renderOptionLabel(menuMasterUnits, r.bag_max_unit) },
    { id: "temp", label: "温冷", render: (r) => renderOptionLabel(menuMasterTemperatures, r.temp_type) },
    { id: "daypart", label: "食事帯", render: (r) => r.daypart || "—" },
    { id: "category", label: "分類", render: (r) => r.category || "—" },
    {
      id: "condiments",
      label: "付属品",
      render: (r) => r.condiments.join("、") || "—",
    },
    {
      id: "edit",
      label: "",
      render: (r) => (
        <Button size="small" onClick={() => select(r)}>
          編集
        </Button>
      ),
    },
  ];
  if (!authenticated) return <Box sx={{ p: 3 }}>ログインを確認しています。</Box>;
  return (
    <Box sx={{ maxWidth: 1440, mx: "auto", px: { xs: 2, md: 3 }, py: 3 }}>
      <Stack spacing={3}>
        <Box component="header">
          <Typography component="h1" variant="h5">
            メニューマスター
          </Typography>
          <Typography color="text.secondary">登録・検索・編集</Typography>
        </Box>
        <TopNav />
        <SearchBar
          value={search}
          onChange={setSearch}
          onSearch={searchNow}
          onClear={() => replaceListUrl({ q: "", page: 0, pageSize: size })}
          pending={list.isFetching}
          label="メニュー名で検索"
        />
        {list.data && !pageOutOfRange && urlState.ok ? <DataTable
          label="メニューマスター一覧"
          rows={list.data.items}
          columns={columns}
          rowKey={(r) => r.id}
          total={list.data.total}
          page={page}
          rowsPerPage={size}
          onPageChange={(next) =>
            replaceListUrl({ q: applied, page: next, pageSize: size })
          }
          onRowsPerPageChange={(next) =>
            replaceListUrl({ q: applied, page: 0, pageSize: next })
          }
          pending={list.isFetching}
          error={listError}
          onRetry={retryList}
        /> : <RequestState pending={list.isFetching} error={listError} onRetry={retryList} />}
        <Divider />
        <Box component="section">
          <Typography component="h2" variant="h6" sx={{ mb: 2 }}>
            新規追加
          </Typography>
          <EditForm<MenuMasterDraft>
            label="メニューマスターを追加"
            defaultValues={blank}
            classifyError={classify}
            onDirtyChange={setNewDirty}
            onSave={async (values) => {
              const owner = { ...editor.current };
              const saved = await create.mutateAsync(values);
              if (
                owns(owner) && editor.current.id !== saved.id &&
                (!editor.current.dirty ||
                  window.confirm(
                    "未保存の編集を破棄して、追加したメニューを開きますか？",
                  ))
              ) {
                choose(saved);
              }
              if (editor.current.active) await refresh();
              return blank;
            }}
          >
            <Fields />
          </EditForm>
        </Box>
        {selected && (
          <Box component="section">
            <Divider sx={{ mb: 2 }} />
            <Typography component="h2" variant="h6" sx={{ mb: 2 }}>
              編集: {selected.name}
            </Typography>
            <EditForm<MenuMasterDraft>
              key={selected.id}
              label={`${selected.name}を編集`}
              defaultValues={draft(selected)}
              classifyError={classify}
              onDirtyChange={changeDirty}
              onSave={async (values) => {
                const owner = { ...editor.current };
                const saved = parseMenuMaster(
                  (
                    await apiClient.put<ItemResponse>(
                      `/menu-masters/${selected.id}`,
                      updateMenuMasterPayload(selected, values),
                    )
                  ).data.item,
                );
                if (owns(owner)) setSelected(saved);
                if (editor.current.active) await refresh();
                return draft(saved);
              }}
              onReload={async () => {
                const owner = { ...editor.current };
                const current = parseMenuMaster(
                  (
                    await apiClient.get<ItemResponse>(
                      `/menu-masters/${selected.id}`,
                    )
                  ).data.item,
                );
                if (owns(owner)) setSelected(current);
                return draft(current);
              }}
            >
              <Fields />
            </EditForm>
          </Box>
        )}
      </Stack>
    </Box>
  );
}

export default function MenuMastersPage() {
  const sessionKey = useSyncExternalStore(
    subscribeBrowserSession,
    browserSessionKey,
    () => "server",
  );
  const authenticated = useSyncExternalStore(
    subscribeBrowserSession,
    hasActiveSessionAuthHeader,
    () => false,
  );
  return (
    <SawaProvider sessionKey={`hospital-menu-masters:${sessionKey}`}>
      <Screen authenticated={authenticated} />
    </SawaProvider>
  );
}
