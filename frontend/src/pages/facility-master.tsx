import { useEffect, useMemo, useState } from "react";
import TopNav from "../components/TopNav";
import { apiClient } from "../services/apiClient";

type ValidationResult = {
  errors: string[];
  warnings: string[];
};

type FacilityEntry = Record<string, unknown>;

type FacilityMaster = {
  schema_version?: string;
  updated_at?: string;
  facilities?: FacilityEntry[];
};

type AreaEntry = {
  id: string;
  name: string;
};

type FaxColumn = {
  index: string;
  sourceIndex: string;
  role: string;
  headerSuperGroup: string;
  header: string;
  headerGroup: string;
  name: string;
  format: string;
  dietType: string;
  areaId: string;
  bagType: string;
  deliveryHeader: string;
  deliveryName: string;
  deliverySource: string;
  deliveryEnabled: boolean | null;
};

const DAILY_LABEL_DIET_OPTIONS = [
  { value: "regular", label: "常食" },
  { value: "soft", label: "軟菜" },
  { value: "mixer", label: "ミキサー" },
  { value: "regular_bag", label: "常食(袋分け)" },
  { value: "daycare", label: "通所" },
  { value: "staff", label: "職員" },
  { value: "diabetes", label: "糖尿" },
  { value: "no_meat", label: "肉禁" },
  { value: "no_fish", label: "魚禁" },
  { value: "no_fried", label: "揚げ物禁" },
  { value: "forbidden_other", label: "その他禁食" },
  { value: "sesame_allergy", label: "ごま禁" },
];

const ROLE_LABELS: Record<string, string> = {
  date: "日付",
  daypart: "朝昼夕",
  menu_name: "メニュー名",
  note: "備考",
  quantity: "数量",
  quantity_change: "変更数量",
};

const AREA_OPTIONS = [
  { value: "X", label: "共通" },
  { value: "2F", label: "2F" },
  { value: "3F", label: "3F" },
  { value: "2f", label: "2F" },
  { value: "3f", label: "3F" },
];

const EXTRA_DIET_LABELS: Record<string, string> = {
  unknown: "不明",
  change_1: "変更1",
  change_2: "変更2",
};

const dietLabel = (value: string) =>
  DAILY_LABEL_DIET_OPTIONS.find((option) => option.value === value)?.label || EXTRA_DIET_LABELS[value] || value || "未設定";

const areaLabel = (value: string) => {
  const normalized = value.trim();
  return AREA_OPTIONS.find((option) => option.value.toLowerCase() === normalized.toLowerCase())?.label || normalized || "共通";
};

const DEFAULT_DAILY_LABEL_DIETS_BY_FACILITY_ID: Record<string, string[]> = {
  FAC00001: ["no_meat", "no_fish"],
  FAC00002: ["no_meat", "no_fish"],
  FAC00003: ["soft", "mixer", "no_fish"],
  FAC00004: ["daycare", "staff", "no_meat", "no_fish", "no_fried", "forbidden_other"],
  FAC00006: ["soft", "mixer", "no_meat", "no_fish"],
  FAC00007: ["no_meat", "no_fish"],
  FAC00008: ["soft", "mixer"],
  FAC00009: ["soft", "mixer"],
  FAC00010: ["soft", "mixer"],
  FAC00011: ["no_meat", "no_fish"],
  FAC00012: ["no_meat", "no_fish"],
  FAC00013: ["diabetes", "no_fish"],
  FAC00014: ["staff", "no_meat", "no_fish", "sesame_allergy"],
  FAC00015: ["no_meat", "no_fish"],
  FAC00016: ["diabetes", "no_fish"],
  FAC636208: ["mixer", "no_meat", "no_fish"],
};

const prettyJson = (value: unknown) => JSON.stringify(value ?? {}, null, 2);

const cloneFacility = (facility: FacilityEntry | null) =>
  facility ? (JSON.parse(JSON.stringify(facility)) as FacilityEntry) : null;

const parseJson = (text: string) => {
  try {
    return { value: JSON.parse(text), error: "" };
  } catch (err) {
    const message = err instanceof Error ? err.message : "Invalid JSON";
    return { value: null, error: message };
  }
};

const generateFacilityId = (existing: Set<string>) => {
  let candidate = "";
  for (let i = 0; i < 20; i += 1) {
    const suffix = Math.floor(Math.random() * 1_000_000)
      .toString()
      .padStart(6, "0");
    candidate = `FAC${suffix}`;
    if (!existing.has(candidate)) {
      return candidate;
    }
  }
  return `FAC${Date.now().toString().slice(-6)}`;
};

const readString = (value: unknown) => (typeof value === "string" ? value : "");

const readStringList = (value: unknown) => {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => (typeof item === "string" ? item.trim() : ""))
    .filter((item) => item);
};

const readEditableStringList = (value: unknown) => {
  if (!Array.isArray(value)) return [];
  return value.map((item) => (typeof item === "string" ? item : "")).filter((item) => item || item === "");
};

const readDailyLabelDietTypes = (facility?: FacilityEntry | null) => {
  if (!facility || typeof facility !== "object") return [];
  const raw = (facility as Record<string, unknown>).daily_label_comparable_diet_types;
  if (Array.isArray(raw)) {
    return readStringList(raw);
  }
  const facilityId = readString((facility as Record<string, unknown>).facility_id);
  return DEFAULT_DAILY_LABEL_DIETS_BY_FACILITY_ID[facilityId] || [];
};

const formatDeliveryNamePreview = (value: string) => {
  const text = value.trim();
  const prefix = "医療法人　松岡会　";
  if (text.startsWith(prefix)) {
    const suffix = text.slice(prefix.length).trim();
    if (suffix) return `医療法人　松岡会\n${suffix}`;
  }
  return text;
};

const readAreas = (value: unknown) => {
  if (!Array.isArray(value)) return [];
  return value
    .map((area) => {
      if (typeof area === "string") {
        const name = area.trim();
        return name ? { id: name, name } : null;
      }
      if (area && typeof area === "object") {
        const record = area as Record<string, unknown>;
        const name = readString(record.name || record.label);
        const id = readString(record.area_id || record.id) || name;
        if (!name) return null;
        return { id, name };
      }
      return null;
    })
    .filter((item): item is { id: string; name: string } => Boolean(item));
};

const readEditableAreas = (value: unknown) => {
  if (!Array.isArray(value)) return [];
  return value
    .map((area) => {
      if (typeof area === "string") {
        return { id: area, name: area };
      }
      if (area && typeof area === "object") {
        const record = area as Record<string, unknown>;
        return {
          id: readString(record.area_id || record.id),
          name: readString(record.name || record.label),
        };
      }
      return null;
    })
    .filter((item): item is AreaEntry => Boolean(item));
};

const toStringValue = (value: unknown) => {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
};

const compactStringList = (items: string[]) => items.map((item) => item.trim()).filter((item) => item);

const normalizeArea = (area: AreaEntry) => {
  const id = area.id.trim();
  const name = area.name.trim();
  if (!id && !name) return null;
  return { area_id: id || name, name: name || id };
};

const readFaxOverride = (facility?: FacilityEntry) => {
  if (!facility || typeof facility !== "object") return { columns: [] as FaxColumn[] };
  const override = (facility as Record<string, unknown>).fax_template_override;
  if (!override || typeof override !== "object") return { columns: [] as FaxColumn[] };
  const overrideRecord = override as Record<string, unknown>;
  const columns = Array.isArray(overrideRecord.columns) ? overrideRecord.columns : [];
  return {
    columns: columns
      .map((column) => {
        if (!column || typeof column !== "object") return null;
        const col = column as Record<string, unknown>;
        return {
          index: toStringValue(col.index),
          sourceIndex: toStringValue(col.source_index),
          role: readString(col.role),
          headerSuperGroup: readString(col.header_super_group),
          header: readString(col.header),
          headerGroup: readString(col.header_group),
          name: readString(col.name),
          format: readString(col.format),
          dietType: readString(col.diet_type),
          areaId: readString(col.area_id),
          bagType: readString(col.bag_type),
          deliveryHeader: readString(col.delivery_header),
          deliveryName: readString(col.delivery_name),
          deliverySource: readString(col.delivery_source),
          deliveryEnabled: typeof col.delivery_enabled === "boolean" ? col.delivery_enabled : null,
        };
      })
      .filter(
        (col): col is FaxColumn =>
          Boolean(
            col &&
              (col.index ||
                col.sourceIndex ||
                col.role ||
                col.headerSuperGroup ||
                col.header ||
                col.headerGroup ||
                col.name ||
                col.format ||
                col.dietType ||
                col.areaId ||
                col.bagType ||
                col.deliveryHeader ||
                col.deliveryName ||
                col.deliverySource ||
                col.deliveryEnabled !== null)
          )
      ),
  };
};

const normalizeFaxColumn = (column: FaxColumn) => {
  const next: Record<string, unknown> = {};
  if (column.index.trim()) {
    const parsed = Number(column.index);
    next.index = Number.isFinite(parsed) ? parsed : column.index.trim();
  }
  if (column.sourceIndex.trim()) {
    const parsed = Number(column.sourceIndex);
    next.source_index = Number.isFinite(parsed) ? parsed : column.sourceIndex.trim();
  }
  if (column.role.trim()) next.role = column.role.trim();
  if (column.headerSuperGroup.trim()) next.header_super_group = column.headerSuperGroup.trim();
  if (column.header.trim()) next.header = column.header.trim();
  if (column.headerGroup.trim()) next.header_group = column.headerGroup.trim();
  if (column.name.trim()) next.name = column.name.trim();
  if (column.format.trim()) next.format = column.format.trim();
  if (column.dietType.trim()) next.diet_type = column.dietType.trim();
  if (column.areaId.trim()) next.area_id = column.areaId.trim();
  if (column.bagType.trim()) next.bag_type = column.bagType.trim();
  if (column.deliveryHeader.trim()) next.delivery_header = column.deliveryHeader.trim();
  if (column.deliveryName.trim()) next.delivery_name = column.deliveryName.trim();
  if (column.deliverySource.trim()) next.delivery_source = column.deliverySource.trim();
  if (column.deliveryEnabled !== null) next.delivery_enabled = column.deliveryEnabled;
  return next;
};

const normalizeAreaToken = (value: string) => {
  const raw = value.trim().toLowerCase();
  if (!raw || raw === "x" || raw === "common" || raw === "共通") return "x";
  if (raw === "2" || raw === "2f" || raw === "2階") return "2f";
  if (raw === "3" || raw === "3f" || raw === "3階") return "3f";
  return raw.replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "") || "x";
};

const isQuantityColumn = (column: FaxColumn) => {
  const role = column.role.trim();
  return role === "quantity" || role === "quantity_change";
};

const orderHeaderLabel = (column: FaxColumn) => {
  const role = column.role.trim();
  if (role === "date") return "日付";
  if (role === "daypart") return "区分";
  if (role === "menu_name") return "メニュー名";
  if (role === "note") return "備考";
  return column.header || column.name || "数量";
};

const deliveryHeaderLabel = (column: FaxColumn) =>
  column.deliveryName || column.deliveryHeader || orderHeaderLabel(column);

const buildHeaderBands = (columns: FaxColumn[], readLabel: (column: FaxColumn) => string) => {
  const bands: { label: string; span: number }[] = [];
  columns.forEach((column) => {
    const label = readLabel(column).trim();
    const last = bands[bands.length - 1];
    if (last && last.label === label) {
      last.span += 1;
    } else {
      bands.push({ label, span: 1 });
    }
  });
  return bands;
};

const deriveFaxRowFields = (columns: FaxColumn[]) => {
  const fields: string[] = [];
  columns.forEach((column) => {
    const role = column.role.trim();
    const name = column.name.trim();
    if (role === "date") fields.push("date_mmdd");
    else if (role === "daypart") fields.push("daypart");
    else if (role === "menu_name") fields.push("menu");
    else if (role === "note") fields.push("remarks");
    else if (role === "quantity" || role === "quantity_change") {
      const diet = column.dietType.trim();
      if (diet) fields.push(`qty.${diet}_${normalizeAreaToken(column.areaId)}`);
    } else if (name) {
      fields.push(name);
    }
  });
  return Array.from(new Set(fields));
};

const sanitizeMasterForSave = (master: FacilityMaster): FacilityMaster => {
  const facilities = Array.isArray(master.facilities)
    ? master.facilities.map((facility) => {
        const record = { ...facility };
        if (Array.isArray(record.aliases)) {
          record.aliases = compactStringList(readEditableStringList(record.aliases));
        }
        if (Array.isArray(record.fax_template_ids)) {
          record.fax_template_ids = compactStringList(readEditableStringList(record.fax_template_ids));
        }
        if (Array.isArray(record.areas)) {
          record.areas = readEditableAreas(record.areas).map(normalizeArea).filter(Boolean);
        }
        if (record.fax_template_override && typeof record.fax_template_override === "object") {
          const override = record.fax_template_override as Record<string, unknown>;
          const columns = readFaxOverride(record).columns;
          const normalizedColumns = columns
            .map(normalizeFaxColumn)
            .filter((column) => Object.keys(column).length > 0);
          record.fax_template_override = {
            ...override,
            columns: normalizedColumns,
            main_ocr_row_fields: deriveFaxRowFields(columns),
          };
        }
        return record;
      })
    : master.facilities;
  return { ...master, facilities };
};


export default function FacilityMasterPage() {
  const [master, setMaster] = useState<FacilityMaster | null>(null);
  const [masterText, setMasterText] = useState("");
  const [selectedIndex, setSelectedIndex] = useState<number>(-1);
  const [editingIndex, setEditingIndex] = useState<number>(-1);
  const [editBaseline, setEditBaseline] = useState<FacilityEntry | null>(null);
  const [validation, setValidation] = useState<ValidationResult | null>(null);
  const [message, setMessage] = useState("");
  const [path, setPath] = useState("");
  const [saving, setSaving] = useState(false);
  const [searchText, setSearchText] = useState("");
  const [selectedFaxColumnIndex, setSelectedFaxColumnIndex] = useState<number>(-1);

  const facilities = useMemo(() => {
    if (!master?.facilities || !Array.isArray(master.facilities)) return [];
    return master.facilities;
  }, [master]);

  const filteredFacilities = useMemo(() => {
    const query = searchText.trim().toLowerCase();
    if (!query) return facilities.map((facility, index) => ({ facility, index }));
    return facilities
      .map((facility, index) => ({ facility, index }))
      .filter(({ facility }) => {
        const record = facility as Record<string, unknown>;
        const haystack = [
          record.facility_id,
          record.facility_name,
          record.delivery_note_facility_name,
          record.fax_template_id,
          ...(readStringList(record.aliases) || []),
        ]
          .join(" ")
          .toLowerCase();
        return haystack.includes(query);
      });
  }, [facilities, searchText]);

  const loadMaster = async () => {
    try {
      const res = await apiClient.get("/facility-master");
      const data = res.data;
      const nextMaster = data.facility_master as FacilityMaster;
      setMaster(nextMaster);
      setMasterText(prettyJson(nextMaster));
      setValidation(data.validation || null);
      setPath(data.path || "");
      const nextFacilities = Array.isArray(nextMaster.facilities) ? nextMaster.facilities : [];
      if (nextFacilities.length > 0) {
        setSelectedIndex(0);
      } else {
        setSelectedIndex(-1);
      }
      setEditingIndex(-1);
      setEditBaseline(null);
      setMessage("");
    } catch (err) {
      setMessage("Facility master の取得に失敗しました。");
    }
  };

  useEffect(() => {
    loadMaster();
  }, []);

  const selectFacility = (index: number) => {
    const target = facilities[index];
    if (!target) return;
    if (editingIndex >= 0 && index !== selectedIndex) {
      setMessage("編集中です。保存またはキャンセルしてから別の施設を選択してください。");
      return;
    }
    setSelectedIndex(index);
    setSelectedFaxColumnIndex(-1);
  };

  const updateFacilityState = (nextFacility: FacilityEntry) => {
    if (!master) return;
    const nextFacilities = [...facilities];
    if (selectedIndex >= 0 && selectedIndex < nextFacilities.length) {
      nextFacilities[selectedIndex] = nextFacility;
    } else {
      nextFacilities.push(nextFacility);
      setSelectedIndex(nextFacilities.length - 1);
    }
    const nextMaster = { ...master, facilities: nextFacilities };
    setMaster(nextMaster);
    setMasterText(prettyJson(nextMaster));
    return nextMaster;
  };

  const updateSelectedFacilityPatch = (patch: FacilityEntry) => {
    if (editingIndex !== selectedIndex) return;
    if (!selectedFacility || typeof selectedFacility !== "object") return;
    const nextFacility = { ...(selectedFacility as FacilityEntry), ...patch };
    updateFacilityState(nextFacility);
  };

  const beginEdit = () => {
    if (!selectedFacility || typeof selectedFacility !== "object") return;
    setEditingIndex(selectedIndex);
    setEditBaseline(cloneFacility(selectedFacility));
    setMessage("編集モードにしました。");
  };

  const cancelEdit = () => {
    if (editingIndex < 0) return;
    if (editBaseline && master) {
      const nextFacilities = [...facilities];
      if (editingIndex >= 0 && editingIndex < nextFacilities.length) {
        nextFacilities[editingIndex] = editBaseline;
        const nextMaster = { ...master, facilities: nextFacilities };
        setMaster(nextMaster);
        setMasterText(prettyJson(nextMaster));
        setSelectedIndex(editingIndex);
      }
    }
    setEditingIndex(-1);
    setEditBaseline(null);
    setMessage("編集をキャンセルしました。");
  };

  const toggleDailyLabelDietType = (dietType: string) => {
    const current = new Set(readDailyLabelDietTypes(selectedFacility));
    if (current.has(dietType)) {
      current.delete(dietType);
    } else {
      current.add(dietType);
    }
    const ordered = DAILY_LABEL_DIET_OPTIONS.map((option) => option.value).filter((value) => current.has(value));
    updateSelectedFacilityPatch({ daily_label_comparable_diet_types: ordered });
  };

  const updateSelectedField = (field: string, value: unknown) => {
    updateSelectedFacilityPatch({ [field]: value });
  };

  const updateStringListItem = (field: string, index: number, value: string) => {
    if (!selectedFacility || typeof selectedFacility !== "object") return;
    const current = readEditableStringList((selectedFacility as Record<string, unknown>)[field]);
    const next = [...current];
    next[index] = value;
    updateSelectedField(field, next);
  };

  const addStringListItem = (field: string) => {
    if (!selectedFacility || typeof selectedFacility !== "object") return;
    const current = readEditableStringList((selectedFacility as Record<string, unknown>)[field]);
    updateSelectedField(field, [...current, ""]);
  };

  const removeStringListItem = (field: string, index: number) => {
    if (!selectedFacility || typeof selectedFacility !== "object") return;
    const current = readEditableStringList((selectedFacility as Record<string, unknown>)[field]);
    updateSelectedField(
      field,
      current.filter((_, itemIndex) => itemIndex !== index)
    );
  };

  const updateArea = (index: number, patch: Partial<AreaEntry>) => {
    const current = readEditableAreas((selectedFacility as Record<string, unknown> | null)?.areas);
    const next = current.map((area, itemIndex) => (itemIndex === index ? { ...area, ...patch } : area));
    updateSelectedField("areas", next.map(normalizeArea).filter(Boolean));
  };

  const addArea = () => {
    const current = readEditableAreas((selectedFacility as Record<string, unknown> | null)?.areas);
    updateSelectedField("areas", [...current.map(normalizeArea).filter(Boolean), { area_id: "", name: "" }]);
  };

  const removeArea = (index: number) => {
    const current = readEditableAreas((selectedFacility as Record<string, unknown> | null)?.areas);
    updateSelectedField(
      "areas",
      current
        .filter((_, itemIndex) => itemIndex !== index)
        .map(normalizeArea)
        .filter(Boolean)
    );
  };

  const updateFaxOverride = (patch: Record<string, unknown>) => {
    if (!selectedFacility || typeof selectedFacility !== "object") return;
    const current = ((selectedFacility as Record<string, unknown>).fax_template_override || {}) as Record<
      string,
      unknown
    >;
    updateSelectedField("fax_template_override", { ...current, ...patch });
  };

  const updateFaxColumn = (index: number, patch: Partial<FaxColumn>) => {
    if (!selectedFacility || typeof selectedFacility !== "object") return;
    const columns = readFaxOverride(selectedFacility).columns;
    const nextColumns = columns.map((column, itemIndex) =>
      itemIndex === index ? { ...column, ...patch } : column
    );
    updateFaxOverride({
      columns: nextColumns.map(normalizeFaxColumn),
      main_ocr_row_fields: deriveFaxRowFields(nextColumns),
    });
  };

  const addFaxColumn = () => {
    if (!selectedFacility || typeof selectedFacility !== "object") return;
    const columns = readFaxOverride(selectedFacility).columns;
    const nextColumns = [
      ...columns,
      {
        index: "",
        sourceIndex: "",
        role: "quantity",
        headerSuperGroup: "",
        header: "",
        headerGroup: "",
        name: "",
        format: "",
        dietType: "",
        areaId: "X",
        bagType: "",
        deliveryHeader: "",
        deliveryName: "",
        deliverySource: "",
        deliveryEnabled: null,
      },
    ];
    updateFaxOverride({
      columns: nextColumns.map(normalizeFaxColumn),
      main_ocr_row_fields: deriveFaxRowFields(nextColumns),
    });
  };

  const removeFaxColumn = (index: number) => {
    if (!selectedFacility || typeof selectedFacility !== "object") return;
    const nextColumns = readFaxOverride(selectedFacility).columns.filter((_, itemIndex) => itemIndex !== index);
    updateFaxOverride({
      columns: nextColumns.map(normalizeFaxColumn),
      main_ocr_row_fields: deriveFaxRowFields(nextColumns),
    });
  };

  const addFacility = () => {
    if (!master) return;
    const existing = new Set(facilities.map((facility) => String(facility.facility_id || "")));
    const facilityId = generateFacilityId(existing);
    const nextFacility = {
      facility_id: facilityId,
      facility_name: "",
      aliases: [],
      areas: [],
      fax_template_ids: [],
      daily_label_comparable_diet_types: [],
    };
    const nextFacilities = [...facilities, nextFacility];
    const nextMaster = { ...master, facilities: nextFacilities };
    setMaster(nextMaster);
    setSelectedIndex(nextFacilities.length - 1);
    setEditingIndex(nextFacilities.length - 1);
    setEditBaseline(null);
    setSelectedFaxColumnIndex(-1);
    setMasterText(prettyJson(nextMaster));
    setMessage("新規施設を追加しました。編集内容を保存してください。");
  };

  const duplicateSelectedFacility = () => {
    if (!master || !selectedFacility || typeof selectedFacility !== "object" || isEditing) return;
    const existing = new Set(facilities.map((facility) => String(facility.facility_id || "")));
    const facilityId = generateFacilityId(existing);
    const sourceName = readString((selectedFacility as Record<string, unknown>).facility_name);
    const nextFacility = {
      ...(cloneFacility(selectedFacility) || {}),
      facility_id: facilityId,
      facility_name: sourceName ? `${sourceName} コピー` : "",
    };
    const nextFacilities = [...facilities, nextFacility];
    const nextMaster = { ...master, facilities: nextFacilities };
    setMaster(nextMaster);
    setMasterText(prettyJson(nextMaster));
    setSelectedIndex(nextFacilities.length - 1);
    setEditingIndex(nextFacilities.length - 1);
    setEditBaseline(null);
    setSearchText("");
    setSelectedFaxColumnIndex(-1);
    setMessage("選択中の施設を複製しました。施設名と表示名を確認して保存してください。");
  };

  const applyMasterJson = () => {
    const parsed = parseJson(masterText);
    if (parsed.error) {
      setMessage(`Master JSON エラー: ${parsed.error}`);
      return;
    }
    if (!parsed.value || typeof parsed.value !== "object") {
      setMessage("Master JSON はオブジェクトで入力してください。");
      return;
    }
    const nextMaster = parsed.value as FacilityMaster;
    setMaster(nextMaster);
    setValidation(null);
    const nextFacilities = Array.isArray(nextMaster.facilities) ? nextMaster.facilities : [];
    if (nextFacilities.length > 0) {
      setSelectedIndex(0);
    } else {
      setSelectedIndex(-1);
    }
    setEditingIndex(-1);
    setEditBaseline(null);
    setMessage("Master JSON を反映しました。");
  };

  const saveMaster = async () => {
    if (!master || saving) return;
    const payload = sanitizeMasterForSave(master);
    setSaving(true);
    setMessage("保存中です...");
    try {
      const res = await apiClient.put("/facility-master", payload);
      const updatedMaster = res.data.facility_master as FacilityMaster;
      setMaster(updatedMaster);
      setMasterText(prettyJson(updatedMaster));
      setValidation(res.data.validation || null);
      setPath(res.data.path || path);
      setEditingIndex(-1);
      setEditBaseline(null);
      setMessage("保存しました。反映先の環境で表示を確認してください。");
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      if (detail?.errors) {
        setValidation({ errors: detail.errors, warnings: [] });
        setMessage("Facility master の検証に失敗しました。");
        return;
      }
      const status = err?.response?.status;
      setMessage(status ? `保存に失敗しました。HTTP ${status}` : "保存に失敗しました。通信状態を確認してください。");
    } finally {
      setSaving(false);
    }
  };

  const selectedFacility = useMemo(
    () => (selectedIndex >= 0 && selectedIndex < facilities.length ? facilities[selectedIndex] : null),
    [facilities, selectedIndex]
  );
  const isEditing = editingIndex === selectedIndex && Boolean(selectedFacility);
  const facilityInfo = useMemo(() => {
    if (!selectedFacility || typeof selectedFacility !== "object") {
      return null;
    }
    const record = selectedFacility as Record<string, unknown>;
    return {
      id: readString(record.facility_id),
      name: readString(record.facility_name),
      deliveryNoteFacilityName: readString(record.delivery_note_facility_name),
      address: readString(record.address),
      phone: readString(record.phone),
      orderFormPatternId: readString(record.order_form_pattern_id),
      faxTemplateId: readString(record.fax_template_id),
      faxTemplateIds: readEditableStringList(record.fax_template_ids),
      faxOverride: readFaxOverride(selectedFacility),
      aliases: readEditableStringList(record.aliases),
      areas: readEditableAreas(record.areas),
      dailyLabelDietTypes: readDailyLabelDietTypes(selectedFacility),
    };
  }, [selectedFacility]);

  const faxColumns = facilityInfo?.faxOverride.columns || [];
  const quantityColumns = faxColumns.filter((column) => {
    return isQuantityColumn(column);
  });
  const visibleFaxColumns = faxColumns.filter((column) => {
    const role = column.role.trim();
    return role === "date" || role === "daypart" || role === "menu_name" || role === "note" || role === "quantity" || role === "quantity_change";
  });
  const deliveryColumns = quantityColumns.filter((column) => column.deliveryEnabled !== false);
  const orderSuperBands = buildHeaderBands(visibleFaxColumns, (column) => column.headerSuperGroup);
  const orderGroupBands = buildHeaderBands(visibleFaxColumns, (column) => column.headerGroup);
  const hasOrderSuperBand = orderSuperBands.some((band) => band.label);
  const hasOrderGroupBand = orderGroupBands.some((band) => band.label);
  const deliverySuperBands = buildHeaderBands(deliveryColumns, (column) => column.headerSuperGroup);
  const deliveryGroupBands = buildHeaderBands(deliveryColumns, (column) => column.headerGroup);
  const hasDeliverySuperBand = deliverySuperBands.some((band) => band.label);
  const hasDeliveryGroupBand = deliveryGroupBands.some((band) => band.label);
  const selectedFaxColumn =
    selectedFaxColumnIndex >= 0 && selectedFaxColumnIndex < faxColumns.length ? faxColumns[selectedFaxColumnIndex] : null;
  const selectedFaxColumnTitle = selectedFaxColumn
    ? selectedFaxColumn.header || selectedFaxColumn.deliveryName || selectedFaxColumn.name || `列 ${selectedFaxColumn.index || selectedFaxColumnIndex + 1}`
    : "";
  const labelPreviewItems = (facilityInfo?.dailyLabelDietTypes || []).map((dietType) => dietLabel(dietType));

  return (
    <main className="page">
      <header className="hero">
        <div>
          <p className="eyebrow">Facilities</p>
          <h1>施設一覧</h1>
          <p className="subtle">施設情報と発注書・納品書のヘッダー設定を更新します。</p>
        </div>
        <TopNav />
      </header>

      <section className="panel help-panel">
        <header className="panel-header">
          <div>
            <h2>編集の流れ</h2>
            <p className="panel-subtitle">施設を選び、編集を開始してから、発注書と納品書の見た目を直接確認しながら設定します。</p>
          </div>
          <a className="btn ghost" href="/manuals/current-stg-20260519/facility_management_detail_current_stg_20260615.md" target="_blank" rel="noreferrer">
            施設編集マニュアル
          </a>
        </header>
        <div className="guide-steps" aria-label="施設編集の手順">
          <div className="guide-step">
            <span className="step-number">1</span>
            <div>
              <p className="guide-title">施設を選ぶ</p>
              <p className="guide-text">左の一覧から修正する施設を選択します。新規施設は既存施設を複製すると設定漏れを減らせます。</p>
            </div>
          </div>
          <div className="guide-step">
            <span className="step-number">2</span>
            <div>
              <p className="guide-title">編集を開始</p>
              <p className="guide-text">閲覧中は入力できません。右上の「この施設の編集を開始」を押すと保存ボタンが有効になります。</p>
            </div>
          </div>
          <div className="guide-step">
            <span className="step-number">3</span>
            <div>
              <p className="guide-title">列をクリック</p>
              <p className="guide-text">発注書または納品書の数量列をクリックして、見出し・食種・階/通所などを編集します。</p>
            </div>
          </div>
          <div className="guide-step">
            <span className="step-number">4</span>
            <div>
              <p className="guide-title">保存して確認</p>
              <p className="guide-text">保存後、発注書取込・ラベル・納品書で想定どおりに表示されるか確認します。</p>
            </div>
          </div>
        </div>
      </section>

      <section className="panel">
        <header className="panel-header">
          <div>
            <h2>施設一覧</h2>
            <p className="panel-subtitle">施設を選んで確認し、必要な時だけ編集を開始します。</p>
          </div>
          <div className="actions">
            <button className="btn ghost" onClick={duplicateSelectedFacility} disabled={!master || !selectedFacility || isEditing}>
              選択施設を複製
            </button>
            <button className="btn" onClick={addFacility} disabled={!master || isEditing}>
              新規施設を追加
            </button>
          </div>
        </header>
        {facilities.length === 0 ? (
          <p className="subtle">まだ施設がありません。</p>
        ) : (
          <div className="facility-grid">
            <div className="list">
              <label className="field search-field">
                <span className="field-label">検索</span>
                <input
                  className="input"
                  value={searchText}
                  disabled={isEditing}
                  onChange={(e) => setSearchText(e.target.value)}
                  placeholder="施設名、納品書表示名で検索"
                />
              </label>
              <p className="list-count">{filteredFacilities.length} / {facilities.length} 件</p>
              {filteredFacilities.length === 0 && <p className="subtle">一致する施設がありません。</p>}
              {filteredFacilities.map(({ facility, index }) => {
                const id = String(facility.facility_id || "unknown");
                const name = String(facility.facility_name || "未設定");
                const deliveryName = String(facility.delivery_note_facility_name || "");
                const hasColumns = readFaxOverride(facility).columns.length > 0;
                const isActive = index === selectedIndex;
                return (
                  <button
                    key={`${id}-${index}`}
                    className={`list-item ${isActive ? "active" : ""}`}
                    onClick={() => selectFacility(index)}
                  >
                    <div>
                      <p className="list-title">{name}</p>
                      <p className="list-meta">{hasColumns ? "発注書設定あり" : "発注書設定なし"}</p>
                      {deliveryName ? <p className="list-meta">納品書: {deliveryName}</p> : null}
                    </div>
                    <span className="ghost-link">{isActive ? "選択中" : "選択"}</span>
                  </button>
                );
              })}
            </div>
            <div className="editor">
              <div className="form-section">
                <div className="section-title-row">
                  <div>
                    <h3>施設概要</h3>
                    <p className="mode-text">
                      {isEditing
                        ? "編集中です。変更後は保存するかキャンセルしてください。"
                        : "閲覧中です。入力や削除は編集開始後にできます。"}
                    </p>
                  </div>
                  {isEditing ? (
                    <div className="actions compact-actions">
                      <button className="btn ghost compact" onClick={cancelEdit} disabled={saving}>
                        キャンセル
                      </button>
                      <button className="btn primary compact" onClick={saveMaster} disabled={saving}>
                        {saving ? "保存中..." : "変更を保存"}
                      </button>
                    </div>
                  ) : (
                    <button className="btn primary edit-start-button" onClick={beginEdit} disabled={!selectedFacility}>
                      この施設の編集を開始
                    </button>
                  )}
                </div>
                <div className="detail-grid overview-grid">
                  <div className="detail-card">
                    <p className="detail-label">施設名</p>
                    <p className="detail-value">{facilityInfo?.name || "-"}</p>
                  </div>
                  <div className="detail-card">
                    <p className="detail-label">納品書表示</p>
                    <p className="detail-value multiline">
                      {formatDeliveryNamePreview(facilityInfo?.deliveryNoteFacilityName || facilityInfo?.name || "-")}
                    </p>
                  </div>
                  <div className="detail-card">
                    <p className="detail-label">発注書設定</p>
                    <p className="detail-value">{visibleFaxColumns.length ? "設定済み" : "未設定"}</p>
                  </div>
                </div>
              </div>

              <div className="form-section">
                <div className="section-title-row">
                  <div>
                    <h3>基本情報</h3>
                    <p className="mode-text">施設一覧や候補選択で使う名前と連絡先です。</p>
                  </div>
                </div>
                <div className="form-grid">
                  <label className="field">
                    <span className="field-label">施設名</span>
                    <input
                      className="input"
                      value={facilityInfo?.name || ""}
                      disabled={!isEditing}
                      onChange={(e) => updateSelectedField("facility_name", e.target.value)}
                    />
                  </label>
                  <label className="field">
                    <span className="field-label">住所</span>
                    <input
                      className="input"
                      value={facilityInfo?.address || ""}
                      disabled={!isEditing}
                      onChange={(e) => updateSelectedField("address", e.target.value)}
                    />
                  </label>
                  <label className="field">
                    <span className="field-label">電話番号</span>
                    <input
                      className="input"
                      value={facilityInfo?.phone || ""}
                      disabled={!isEditing}
                      onChange={(e) => updateSelectedField("phone", e.target.value)}
                    />
                  </label>
                </div>
              </div>

              <div className="form-section">
                <div className="section-title-row">
                  <div>
                    <h3>発注書ヘッダー設定</h3>
                    <p className="mode-text">発注書の上部からメニュー行までを見ながら、数量列の意味を設定します。薄緑の数量列をクリックしてください。</p>
                  </div>
                </div>
                <div className="inline-guide">
                  <span>発注書の見出し: 原稿に書かれている列名</span>
                  <span>拡大ヘッダー: 禁食など複数列をまとめる上段名</span>
                  <span>食種/施設区分: ラベル・納品書の集計単位</span>
                </div>
                {visibleFaxColumns.length ? (
                  <>
                    <div className="document-fragment order-fragment" aria-label="発注書ヘッダー">
                      <div className="document-title-row">
                        <div>
                          <p className="document-kicker">発注書</p>
                          <p className="document-title">{facilityInfo?.name || "施設発注書"}</p>
                        </div>
                        <div className="document-meta">
                          <span>対象週</span>
                          <strong>2026/6/21 - 6/27</strong>
                        </div>
                      </div>
                      <table className="document-table">
                        <thead>
                          {hasOrderSuperBand && (
                            <tr>
                              {orderSuperBands.map((band, idx) => (
                                <th key={`order-super-${band.label}-${idx}`} colSpan={band.span} className="document-band super-band">
                                  {band.label || ""}
                                </th>
                              ))}
                            </tr>
                          )}
                          {hasOrderGroupBand && (
                            <tr>
                              {orderGroupBands.map((band, idx) => (
                                <th key={`order-group-${band.label}-${idx}`} colSpan={band.span} className="document-band">
                                  {band.label || ""}
                                </th>
                              ))}
                            </tr>
                          )}
                          <tr>
                            {visibleFaxColumns.map((col, idx) => {
                              const role = col.role.trim();
                              const isQuantity = isQuantityColumn(col);
                              const sourceIndex = faxColumns.findIndex((candidate) => candidate === col);
                              return (
                                <th key={`${col.index}-${col.role}-${idx}`} className={isQuantity ? "quantity-preview-col editable-column" : ""}>
                                  {isQuantity ? (
                                    <button
                                      className={`cell-edit-button ${sourceIndex === selectedFaxColumnIndex ? "active" : ""}`}
                                      type="button"
                                      onClick={() => setSelectedFaxColumnIndex(sourceIndex)}
                                    >
                                      <span>{orderHeaderLabel(col)}</span>
                                      <small>{dietLabel(col.dietType)} / {areaLabel(col.areaId)}</small>
                                    </button>
                                  ) : (
                                    <>
                                      <span>{orderHeaderLabel(col)}</span>
                                    </>
                                  )}
                                </th>
                              );
                            })}
                          </tr>
                        </thead>
                      </table>
                    </div>
                    <div className="column-editor">
                      {selectedFaxColumn ? (
                        <>
                          <div className="selected-column-summary">
                            <p className="detail-label">選択中の列</p>
                            <p className="quantity-title">{selectedFaxColumnTitle}</p>
                            <p className="detail-meta">
                              この列は発注書OCRの読み取り先です。食種と施設区分を変えると、ラベル・納品書・袋分けの表示単位にも反映されます。
                            </p>
                          </div>
                          <label className="field">
                            <span className="field-label">発注書の見出し</span>
                            <input
                              className="input"
                              value={selectedFaxColumn.header || ""}
                              disabled={!isEditing}
                              onChange={(e) => updateFaxColumn(selectedFaxColumnIndex, { header: e.target.value })}
                              placeholder="発注書に書かれている見出し"
                            />
                          </label>
                          <label className="field">
                            <span className="field-label">拡大ヘッダー</span>
                            <input
                              className="input"
                              value={selectedFaxColumn.headerSuperGroup || ""}
                              disabled={!isEditing}
                              onChange={(e) => updateFaxColumn(selectedFaxColumnIndex, { headerSuperGroup: e.target.value })}
                              placeholder="例: 禁食、魚"
                            />
                          </label>
                          <label className="field">
                            <span className="field-label">上段ヘッダー</span>
                            <input
                              className="input"
                              value={selectedFaxColumn.headerGroup || ""}
                              disabled={!isEditing}
                              onChange={(e) => updateFaxColumn(selectedFaxColumnIndex, { headerGroup: e.target.value })}
                              placeholder="例: 常食、軟菜、ミキサー"
                            />
                          </label>
                          <label className="field">
                            <span className="field-label">食種</span>
                            <select
                              className="input"
                              value={selectedFaxColumn.dietType || ""}
                              disabled={!isEditing}
                              onChange={(e) => updateFaxColumn(selectedFaxColumnIndex, { dietType: e.target.value })}
                            >
                              <option value="">未設定</option>
                              {selectedFaxColumn.dietType && EXTRA_DIET_LABELS[selectedFaxColumn.dietType] ? (
                                <option value={selectedFaxColumn.dietType}>{EXTRA_DIET_LABELS[selectedFaxColumn.dietType]}</option>
                              ) : null}
                              {DAILY_LABEL_DIET_OPTIONS.map((option) => (
                                <option key={option.value} value={option.value}>
                                  {option.label}
                                </option>
                              ))}
                            </select>
                          </label>
                          <label className="field">
                            <span className="field-label">施設区分</span>
                            <input
                              className="input"
                              value={areaLabel(selectedFaxColumn.areaId)}
                              disabled={!isEditing}
                              onChange={(e) => updateFaxColumn(selectedFaxColumnIndex, { areaId: e.target.value })}
                              placeholder="例: 共通、2F、3F、通所"
                            />
                          </label>
                          <div className="impact-box">
                            <p className="detail-label">反映先</p>
                            <div className="impact-grid">
                              <span>発注書OCR</span>
                              <strong>{orderHeaderLabel(selectedFaxColumn)}</strong>
                              <span>ラベル</span>
                              <strong>{dietLabel(selectedFaxColumn.dietType)} / {areaLabel(selectedFaxColumn.areaId)}</strong>
                              <span>納品書</span>
                              <strong>{deliveryHeaderLabel(selectedFaxColumn)}</strong>
                            </div>
                          </div>
                        </>
                      ) : (
                        <p className="subtle">発注書ヘッダーの数量列を選択すると、ここで内容を編集できます。</p>
                      )}
                    </div>
                  </>
                ) : (
                  <div className="empty-guide">
                    <p className="subtle">発注書の列設定が未設定です。既存施設を複製するか、上級設定から列を追加してください。</p>
                    <button className="btn compact" onClick={addFaxColumn} disabled={!isEditing}>
                      数量列を追加
                    </button>
                  </div>
                )}
              </div>

              <div className="form-section">
                <div className="section-title-row">
                  <div>
                    <h3>納品書ヘッダー設定</h3>
                    <p className="mode-text">納品書の上部からメニュー行までを見ながら、施設名と列名を確認します。発注書と同じ列をクリックして編集します。</p>
                  </div>
                </div>
                <div className="document-fragment delivery-fragment" aria-label="納品書ヘッダー">
                  <div className="document-title-row">
                    <div>
                      <p className="document-kicker">納品書</p>
                      <p className="document-title multiline">
                        {formatDeliveryNamePreview(facilityInfo?.deliveryNoteFacilityName || facilityInfo?.name || "施設名")}
                      </p>
                    </div>
                    <div className="document-meta">
                      <span>株式会社アドオンミール</span>
                      <strong>2026/6/21</strong>
                    </div>
                  </div>
                  <table className="document-table delivery-header-table">
                    <thead>
                      {hasDeliverySuperBand && (
                        <tr>
                          <th className="document-band super-band" colSpan={3} />
                          {deliverySuperBands.map((band, idx) => (
                            <th key={`delivery-super-${band.label}-${idx}`} colSpan={band.span} className="document-band super-band">
                              {band.label || ""}
                            </th>
                          ))}
                          <th className="document-band super-band" />
                        </tr>
                      )}
                      {hasDeliveryGroupBand && (
                        <tr>
                          <th className="document-band" colSpan={3} />
                          {deliveryGroupBands.map((band, idx) => (
                            <th key={`delivery-group-${band.label}-${idx}`} colSpan={band.span} className="document-band">
                              {band.label || ""}
                            </th>
                          ))}
                          <th className="document-band" />
                        </tr>
                      )}
                      <tr>
                        <th className="document-band">日付</th>
                        <th className="document-band">区分</th>
                        <th className="document-band menu-column">メニュー名</th>
                        {deliveryColumns.map((col) => {
                          const sourceIndex = faxColumns.findIndex((candidate) => candidate === col);
                          return (
                            <th key={`${col.index}-${sourceIndex}`} className="editable-column">
                              <button
                                className={`cell-edit-button ${sourceIndex === selectedFaxColumnIndex ? "active" : ""}`}
                                type="button"
                                onClick={() => setSelectedFaxColumnIndex(sourceIndex)}
                              >
                                <span>{deliveryHeaderLabel(col)}</span>
                                <small>{dietLabel(col.dietType)} / {areaLabel(col.areaId)}</small>
                              </button>
                            </th>
                          );
                        })}
                        <th className="document-band">備考</th>
                      </tr>
                    </thead>
                  </table>
                </div>
                <div className="delivery-editor-grid">
                  <label className="field">
                    <span className="field-label">納品書の施設名</span>
                    <input
                      className="input"
                      value={facilityInfo?.deliveryNoteFacilityName || ""}
                      disabled={!isEditing}
                      onChange={(e) => updateSelectedField("delivery_note_facility_name", e.target.value)}
                      placeholder="未設定の場合は施設名を使用"
                    />
                  </label>
                  {selectedFaxColumn ? (
                    <>
                      <label className="field">
                        <span className="field-label">納品書での列名</span>
                        <input
                          className="input"
                          value={selectedFaxColumn.deliveryName || ""}
                          disabled={!isEditing}
                          onChange={(e) => updateFaxColumn(selectedFaxColumnIndex, { deliveryName: e.target.value })}
                          placeholder={selectedFaxColumn.header || selectedFaxColumn.name || "例: 常食2F"}
                        />
                      </label>
                      <label className="field">
                        <span className="field-label">納品書で表示</span>
                        <select
                          className="input"
                          value={selectedFaxColumn.deliveryEnabled === false ? "false" : "true"}
                          disabled={!isEditing}
                          onChange={(e) => updateFaxColumn(selectedFaxColumnIndex, { deliveryEnabled: e.target.value === "true" })}
                        >
                          <option value="true">表示する</option>
                          <option value="false">表示しない</option>
                        </select>
                      </label>
                    </>
                  ) : (
                    <p className="subtle">納品書ヘッダーの数量列を選択すると、列名と表示有無を編集できます。</p>
                  )}
                </div>
                {visibleFaxColumns.length ? (
                  <>
                    <details className="advanced-details">
                      <summary>上級設定を開く</summary>
                      <div className="legacy-settings">
                        <label className="field">
                          <span className="field-label">内部施設ID</span>
                          <input
                            className="input"
                            value={facilityInfo?.id || ""}
                            disabled={!isEditing}
                            onChange={(e) => updateSelectedField("facility_id", e.target.value)}
                          />
                        </label>
                        <label className="field">
                          <span className="field-label">旧 注文書パターンID</span>
                          <input
                            className="input"
                            value={facilityInfo?.orderFormPatternId || ""}
                            disabled={!isEditing}
                            onChange={(e) => updateSelectedField("order_form_pattern_id", e.target.value)}
                          />
                        </label>
                        <label className="field">
                          <span className="field-label">旧 FAXテンプレートID</span>
                          <input
                            className="input"
                            value={facilityInfo?.faxTemplateId || ""}
                            disabled={!isEditing}
                            onChange={(e) => updateSelectedField("fax_template_id", e.target.value)}
                          />
                        </label>
                      </div>
                      <details className="nested-details">
                        <summary>読み取り名・旧テンプレート候補</summary>
                        <div className="legacy-list-block">
                          <div className="section-title-row">
                            <h4>読み取り名</h4>
                            <button className="btn compact" onClick={() => addStringListItem("aliases")} disabled={!isEditing}>
                              追加
                            </button>
                          </div>
                          {facilityInfo?.aliases.length ? (
                            <div className="row-list">
                              {facilityInfo.aliases.map((alias, index) => (
                                <div className="row-editor" key={`${alias}-${index}`}>
                                  <input
                                    className="input"
                                    value={alias}
                                    disabled={!isEditing}
                                    onChange={(e) => updateStringListItem("aliases", index, e.target.value)}
                                  />
                                  <button
                                    className="btn danger compact"
                                    onClick={() => removeStringListItem("aliases", index)}
                                    disabled={!isEditing}
                                  >
                                    削除
                                  </button>
                                </div>
                              ))}
                            </div>
                          ) : (
                            <p className="subtle">未設定です。</p>
                          )}
                          <div className="section-title-row">
                            <h4>旧FAXテンプレート候補</h4>
                            <button className="btn compact" onClick={() => addStringListItem("fax_template_ids")} disabled={!isEditing}>
                              追加
                            </button>
                          </div>
                          {facilityInfo?.faxTemplateIds.length ? (
                            <div className="row-list">
                              {facilityInfo.faxTemplateIds.map((templateId, index) => (
                                <div className="row-editor" key={`${templateId}-${index}`}>
                                  <input
                                    className="input"
                                    value={templateId}
                                    disabled={!isEditing}
                                    onChange={(e) => updateStringListItem("fax_template_ids", index, e.target.value)}
                                  />
                                  <button
                                    className="btn danger compact"
                                    onClick={() => removeStringListItem("fax_template_ids", index)}
                                    disabled={!isEditing}
                                  >
                                    削除
                                  </button>
                                </div>
                              ))}
                            </div>
                          ) : (
                            <p className="subtle">未設定です。</p>
                          )}
                        </div>
                      </details>
                      <div className="section-title-row advanced-title-row">
                        <p className="mode-text">列番号・role・内部名など、システム向けの詳細設定です。</p>
                        <button className="btn compact" onClick={addFaxColumn} disabled={!isEditing}>
                          列を追加
                        </button>
                      </div>
                      <div className="table-wrap">
                        <table className="invoice-table">
                      <thead>
                        <tr>
                          <th>列番号</th>
                          <th>読取元</th>
                          <th>役割</th>
                          <th>見出し</th>
                          <th>内部名</th>
                          <th>形式</th>
                          <th>食種</th>
                          <th>施設区分</th>
                          <th>袋種</th>
                          <th>操作</th>
                        </tr>
                      </thead>
                      <tbody>
                        {facilityInfo.faxOverride.columns.map((col, idx) => (
                          <tr key={`${col.index}-${col.role}-${idx}`}>
                            <td>
                              <input
                                className="table-input numeric"
                                value={col.index}
                                disabled={!isEditing}
                                onChange={(e) => updateFaxColumn(idx, { index: e.target.value })}
                              />
                            </td>
                            <td>
                              <input
                                className="table-input numeric"
                                value={col.sourceIndex}
                                disabled={!isEditing}
                                onChange={(e) => updateFaxColumn(idx, { sourceIndex: e.target.value })}
                              />
                            </td>
                            <td>
                              <input
                                className="table-input"
                                value={col.role}
                                disabled={!isEditing}
                                onChange={(e) => updateFaxColumn(idx, { role: e.target.value })}
                              />
                            </td>
                            <td>
                              <input
                                className="table-input"
                                value={col.header}
                                disabled={!isEditing}
                                onChange={(e) => updateFaxColumn(idx, { header: e.target.value })}
                              />
                            </td>
                            <td>
                              <input
                                className="table-input"
                                value={col.name}
                                disabled={!isEditing}
                                onChange={(e) => updateFaxColumn(idx, { name: e.target.value })}
                              />
                            </td>
                            <td>
                              <input
                                className="table-input"
                                value={col.format}
                                disabled={!isEditing}
                                onChange={(e) => updateFaxColumn(idx, { format: e.target.value })}
                              />
                            </td>
                            <td>
                              <input
                                className="table-input"
                                value={col.dietType}
                                disabled={!isEditing}
                                onChange={(e) => updateFaxColumn(idx, { dietType: e.target.value })}
                              />
                            </td>
                            <td>
                              <input
                                className="table-input"
                                value={col.areaId}
                                disabled={!isEditing}
                                onChange={(e) => updateFaxColumn(idx, { areaId: e.target.value })}
                              />
                            </td>
                            <td>
                              <input
                                className="table-input"
                                value={col.bagType}
                                disabled={!isEditing}
                                onChange={(e) => updateFaxColumn(idx, { bagType: e.target.value })}
                              />
                            </td>
                            <td>
                              <button
                                className="btn danger compact"
                                onClick={() => removeFaxColumn(idx)}
                                disabled={!isEditing}
                              >
                                削除
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                      </div>
                    </details>
                  </>
                ) : (
                  null
                )}
              </div>

              <div className="form-section">
                <div className="section-title-row">
                  <h3>施設区分</h3>
                  <button className="btn compact" onClick={addArea} disabled={!isEditing}>
                    追加
                  </button>
                </div>
                {facilityInfo?.areas.length ? (
                  <div className="area-list">
                    {facilityInfo.areas.map((area, index) => (
                      <div className="area-row" key={`${area.id}-${area.name}-${index}`}>
                        <label className="field">
                          <span className="field-label">区分キー</span>
                          <input
                            className="input"
                            value={area.id}
                            disabled={!isEditing}
                            onChange={(e) => updateArea(index, { id: e.target.value })}
                          />
                          <span className="field-help">通常は区分名と同じ値で問題ありません。例: 2F、3F、通所</span>
                        </label>
                        <label className="field">
                          <span className="field-label">区分名</span>
                          <input
                            className="input"
                            value={area.name}
                            disabled={!isEditing}
                            onChange={(e) => updateArea(index, { name: e.target.value })}
                          />
                        </label>
                        <button className="btn danger compact" onClick={() => removeArea(index)} disabled={!isEditing}>
                          削除
                        </button>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="subtle">未設定です。通所・職員など施設内区分がある場合に追加してください。</p>
                )}
              </div>

              <div className="form-section">
                <div className="section-title-row">
                  <div>
                    <h3>ラベルの表示設定</h3>
                    <p className="mode-text">ラベルで常食とは別に確認したい食種を選びます。選んだものが下のプレビューに出ます。</p>
                  </div>
                </div>
                <div className="label-config-layout">
                  <div>
                    <div className="toggle-grid">
                      {DAILY_LABEL_DIET_OPTIONS.map((option) => {
                        const checked = Boolean(facilityInfo?.dailyLabelDietTypes.includes(option.value));
                        return (
                          <label key={option.value} className={`toggle-item ${checked ? "checked" : ""}`}>
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={() => toggleDailyLabelDietType(option.value)}
                              disabled={!isEditing}
                            />
                            <span>{option.label}</span>
                          </label>
                        );
                      })}
                    </div>
                    <p className="detail-meta">
                      施設で実際に分けて確認する食種だけ選んでください。未選択の場合はシステム既定値を使います。
                    </p>
                  </div>
                  <div className="label-preview">
                    <p className="detail-label">ラベル表示プレビュー</p>
                    <div className="label-preview-body">
                      <p className="label-menu-name">白身魚フライ 添)キャベツ</p>
                      <div className="label-chip-row">
                        <span className="label-chip">常食</span>
                        {labelPreviewItems.length ? (
                          labelPreviewItems.map((item) => (
                            <span className="label-chip muted" key={item}>
                              {item}
                            </span>
                          ))
                        ) : (
                          <span className="label-chip muted">既定設定</span>
                        )}
                      </div>
                      <p className="label-meta-line">温菜 / 1人前 / 2026年6月21日</p>
                    </div>
                  </div>
                </div>
              </div>

              {isEditing && (
                <div className="actions save-actions">
                  <button className="btn ghost" onClick={cancelEdit} disabled={saving}>
                    キャンセル
                  </button>
                  <button className="btn primary" onClick={saveMaster} disabled={saving}>
                    {saving ? "保存中..." : "保存する"}
                  </button>
                </div>
              )}
            </div>
          </div>
        )}
      </section>

      <section className="panel">
        <details className="json-panel">
          <summary>システム担当者向け: Master JSON</summary>
          <div className="actions">
            <button className="btn ghost" onClick={applyMasterJson} disabled={!isEditing}>
              JSON を反映
            </button>
          </div>
          <textarea
            className="textarea json-textarea"
            value={masterText}
            disabled={!isEditing}
            onChange={(e) => setMasterText(e.target.value)}
            rows={16}
            wrap="soft"
          />
        </details>
      </section>

      {validation && (
        <section className="panel">
          <header className="panel-header">
            <h2>検証結果</h2>
          </header>
          <details className="validation-details">
            <summary>
              Errors: {validation.errors.length} / Warnings: {validation.warnings.length}
            </summary>
            {validation.errors.length > 0 && (
              <>
                <p className="subtle">Errors</p>
                <pre className="code-block">{validation.errors.join("\n")}</pre>
              </>
            )}
            {validation.warnings.length > 0 && (
              <>
                <p className="subtle">Warnings</p>
                <pre className="code-block">{validation.warnings.join("\n")}</pre>
              </>
            )}
          </details>
        </section>
      )}

      {message && <p className="message">{message}</p>}

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

        .nav {
          display: flex;
          gap: 12px;
          flex-wrap: wrap;
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

        .panel {
          background: #ffffff;
          border-radius: 8px;
          padding: 20px;
          border: 1px solid rgba(25, 32, 30, 0.08);
          box-shadow: 0 12px 26px rgba(27, 35, 33, 0.06);
          margin-bottom: 20px;
        }

        .help-panel {
          background: #fbfaf7;
        }

        .panel-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          gap: 16px;
          margin-bottom: 16px;
        }

        .panel-subtitle {
          margin: 6px 0 0;
          color: #5f7b74;
          font-size: 13px;
          line-height: 1.5;
        }

        h2 {
          font-size: 18px;
          margin: 0;
        }

        .badge {
          background: #1f2a2a;
          color: #fff9ef;
          padding: 6px 12px;
          border-radius: 999px;
          font-size: 12px;
        }

        .guide-steps {
          display: grid;
          grid-template-columns: repeat(4, minmax(0, 1fr));
          gap: 10px;
        }

        .guide-step {
          display: grid;
          grid-template-columns: auto 1fr;
          gap: 10px;
          min-height: 112px;
          padding: 12px;
          border: 1px solid rgba(31, 42, 42, 0.12);
          border-radius: 8px;
          background: #ffffff;
        }

        .step-number {
          display: grid;
          place-items: center;
          width: 28px;
          height: 28px;
          border-radius: 50%;
          background: #1f2a2a;
          color: #fffdf8;
          font-size: 13px;
          font-weight: 900;
        }

        .guide-title {
          margin: 0;
          font-size: 13px;
          font-weight: 900;
        }

        .guide-text {
          margin: 4px 0 0;
          color: #51615c;
          font-size: 12px;
          line-height: 1.55;
        }

        .facility-grid {
          display: grid;
          grid-template-columns: minmax(200px, 1fr) minmax(320px, 2fr);
          gap: 18px;
        }

        .list {
          display: flex;
          flex-direction: column;
          gap: 10px;
        }

        .search-field {
          background: #ffffff;
          padding-bottom: 4px;
        }

        .list-count {
          margin: 0;
          color: #5f7b74;
          font-size: 12px;
          font-weight: 700;
        }

        .list-item {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          padding: 12px 14px;
          border-radius: 8px;
          border: 1px solid rgba(31, 42, 42, 0.1);
          background: #f7f5f1;
          text-align: left;
          cursor: pointer;
        }

        .list-item.active {
          border-color: #1f2a2a;
          background: #eef3f1;
        }

        .list-title {
          margin: 0 0 4px;
          font-weight: 600;
        }

        .list-meta {
          margin: 0;
          font-size: 12px;
          color: #5a6c66;
        }

        .ghost-link {
          font-size: 12px;
          color: #1f2a2a;
        }

        .editor {
          display: flex;
          flex-direction: column;
          gap: 14px;
        }

        .form-section {
          border-radius: 8px;
          border: 1px solid rgba(31, 42, 42, 0.12);
          background: #ffffff;
          padding: 14px;
        }

        .form-section h3 {
          font-size: 15px;
          margin: 0 0 12px;
        }

        .section-title-row {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          margin-bottom: 12px;
        }

        .mode-text {
          margin: 4px 0 0;
          color: #5f7b74;
          font-size: 12px;
          font-weight: 600;
          max-width: 520px;
          line-height: 1.6;
        }

        .section-help {
          margin: 0 0 12px;
          color: #40514c;
          font-size: 13px;
          line-height: 1.6;
        }

        .inline-guide {
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
          margin: 0 0 12px;
        }

        .inline-guide span {
          padding: 6px 10px;
          border-radius: 8px;
          background: #eef3f1;
          color: #40514c;
          font-size: 12px;
          font-weight: 800;
        }

        .compact-actions {
          align-items: center;
        }

        .section-title-row h3 {
          margin: 0;
        }

        .form-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
          gap: 12px;
        }

        .row-list,
        .area-list {
          display: flex;
          flex-direction: column;
          gap: 10px;
        }

        .row-editor,
        .area-row {
          display: grid;
          grid-template-columns: 1fr auto;
          gap: 10px;
          align-items: end;
        }

        .area-row {
          grid-template-columns: minmax(120px, 0.7fr) minmax(160px, 1fr) auto;
        }

        .detail-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
          gap: 12px;
        }

        .overview-grid {
          grid-template-columns: repeat(3, minmax(0, 1fr));
        }

        .detail-card {
          border-radius: 8px;
          border: 1px solid rgba(31, 42, 42, 0.12);
          background: #f7f5f1;
          padding: 12px;
        }

        .detail-wide {
          width: 100%;
        }

        .detail-label {
          font-size: 12px;
          letter-spacing: 0.08em;
          text-transform: uppercase;
          color: #5f7b74;
          margin: 0 0 6px;
        }

        .detail-value {
          margin: 0;
          font-weight: 600;
          word-break: break-word;
        }

        .detail-value.multiline {
          white-space: pre-line;
          line-height: 1.6;
        }

        .delivery-preview {
          min-height: 78px;
        }

        .detail-meta {
          margin: 0 0 8px;
          font-size: 12px;
          color: #5f7b74;
          word-break: break-word;
        }

        .field-help {
          display: block;
          margin-top: 5px;
          color: #5f7b74;
          font-size: 11px;
          line-height: 1.5;
        }

        .chip-row {
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
        }

        .chip {
          padding: 4px 10px;
          border-radius: 999px;
          background: #eef3f1;
          font-size: 12px;
          color: #1f2a2a;
        }

        .toggle-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(132px, 1fr));
          gap: 8px;
          margin-bottom: 10px;
        }

        .order-sheet-preview {
          width: 100%;
          overflow-x: auto;
          border: 1px solid rgba(31, 42, 42, 0.14);
          border-radius: 8px;
          background: #fffdf8;
          margin-bottom: 14px;
        }

        .preview-table {
          width: 100%;
          min-width: 680px;
          border-collapse: collapse;
          font-size: 12px;
        }

        .preview-table th,
        .preview-table td {
          border: 1px solid rgba(31, 42, 42, 0.14);
          padding: 9px 10px;
          text-align: center;
          vertical-align: middle;
        }

        .preview-table th {
          background: #f2f5f2;
          font-weight: 800;
        }

        .preview-table th span,
        .preview-table th small {
          display: block;
          line-height: 1.45;
        }

        .preview-table th small {
          color: #5f7b74;
          font-size: 11px;
          font-weight: 700;
        }

        .quantity-preview-col {
          min-width: 98px;
        }

        .document-fragment {
          width: 100%;
          overflow-x: auto;
          border: 1px solid rgba(31, 42, 42, 0.18);
          border-radius: 8px;
          background: #fffdf8;
          margin-bottom: 12px;
        }

        .document-title-row {
          display: flex;
          justify-content: space-between;
          gap: 16px;
          align-items: flex-start;
          min-width: 760px;
          padding: 14px 16px;
          border-bottom: 1px solid rgba(31, 42, 42, 0.16);
          background: #ffffff;
        }

        .document-kicker {
          margin: 0 0 6px;
          font-size: 12px;
          font-weight: 800;
          letter-spacing: 0.08em;
          color: #5f7b74;
        }

        .document-title {
          margin: 0;
          font-size: 20px;
          line-height: 1.4;
          font-weight: 900;
        }

        .document-title.multiline {
          white-space: pre-line;
        }

        .document-meta {
          display: grid;
          gap: 4px;
          min-width: 160px;
          text-align: right;
          font-size: 12px;
          color: #40514c;
        }

        .document-meta strong {
          color: #1f2a2a;
          font-size: 14px;
        }

        .document-table {
          width: 100%;
          min-width: 900px;
          border-collapse: collapse;
          table-layout: auto;
          font-size: 12px;
        }

        .document-table th,
        .document-table td {
          border: 1px solid rgba(31, 42, 42, 0.24);
          padding: 9px 8px;
          text-align: center;
          vertical-align: middle;
          background: #fffefa;
          min-width: 84px;
          white-space: normal;
        }

        .document-table th {
          background: #f2f5f2;
          font-weight: 900;
        }

        .document-band {
          background: #e9efec !important;
        }

        .menu-column {
          min-width: 180px;
        }

        .editable-column {
          padding: 0 !important;
        }

        .cell-edit-button {
          display: grid;
          gap: 3px;
          width: 100%;
          min-height: 58px;
          padding: 8px;
          border: 2px solid transparent;
          background: transparent;
          color: #1f2a2a;
          font: inherit;
          font-weight: 900;
          cursor: pointer;
        }

        .cell-edit-button:hover,
        .cell-edit-button.active {
          border-color: #1f2a2a;
          background: #eef3f1;
        }

        .cell-edit-button small {
          color: #5f7b74;
          font-size: 11px;
          font-weight: 800;
        }

        .column-editor,
        .delivery-editor-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
          gap: 12px;
          align-items: end;
          border: 1px solid rgba(31, 42, 42, 0.12);
          border-radius: 8px;
          background: #f7f9f8;
          padding: 12px;
          margin-bottom: 12px;
        }

        .selected-column-summary,
        .impact-box {
          align-self: stretch;
        }

        .impact-box {
          border: 1px solid rgba(31, 42, 42, 0.12);
          border-radius: 8px;
          background: #ffffff;
          padding: 10px;
        }

        .impact-grid {
          display: grid;
          grid-template-columns: max-content minmax(0, 1fr);
          gap: 6px 10px;
          font-size: 12px;
          line-height: 1.45;
        }

        .impact-grid span {
          color: #5f7b74;
          font-weight: 800;
        }

        .impact-grid strong {
          min-width: 0;
          word-break: break-word;
        }

        .quantity-card-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
          gap: 12px;
          margin-bottom: 12px;
        }

        .quantity-card {
          display: grid;
          gap: 10px;
          border: 1px solid rgba(31, 42, 42, 0.14);
          border-radius: 8px;
          background: #fbfaf7;
          padding: 12px;
        }

        .quantity-title {
          margin: 0;
          font-size: 15px;
          font-weight: 800;
          word-break: break-word;
        }

        .advanced-details {
          border-top: 1px solid rgba(31, 42, 42, 0.1);
          padding-top: 12px;
          margin-top: 10px;
        }

        .advanced-details summary {
          cursor: pointer;
          color: #40514c;
          font-weight: 800;
        }

        .advanced-title-row {
          margin-top: 12px;
        }

        .legacy-settings {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
          gap: 12px;
          margin-top: 12px;
          padding: 12px;
          border: 1px solid rgba(31, 42, 42, 0.1);
          border-radius: 8px;
          background: #fbfaf7;
        }

        .nested-details {
          margin-top: 12px;
          border: 1px solid rgba(31, 42, 42, 0.1);
          border-radius: 8px;
          padding: 10px 12px;
          background: #ffffff;
        }

        .nested-details summary {
          cursor: pointer;
          font-weight: 800;
          color: #40514c;
        }

        .legacy-list-block {
          display: grid;
          gap: 12px;
          margin-top: 12px;
        }

        .legacy-list-block h4 {
          margin: 0;
          font-size: 14px;
        }

        .empty-guide {
          display: flex;
          justify-content: space-between;
          align-items: center;
          gap: 12px;
          border: 1px dashed rgba(31, 42, 42, 0.22);
          border-radius: 8px;
          padding: 14px;
          background: #fbfaf7;
        }

        .label-config-layout {
          display: grid;
          grid-template-columns: minmax(260px, 1.2fr) minmax(240px, 0.8fr);
          gap: 14px;
          align-items: start;
        }

        .label-preview {
          border: 1px solid rgba(31, 42, 42, 0.14);
          border-radius: 8px;
          background: #fffdf8;
          padding: 12px;
        }

        .label-preview-body {
          border: 2px solid #1f2a2a;
          border-radius: 6px;
          padding: 12px;
          background: #ffffff;
        }

        .label-menu-name {
          margin: 0 0 10px;
          font-size: 16px;
          font-weight: 900;
          line-height: 1.45;
        }

        .label-chip-row {
          display: flex;
          flex-wrap: wrap;
          gap: 6px;
          margin-bottom: 10px;
        }

        .label-chip {
          border: 1px solid #1f2a2a;
          border-radius: 6px;
          padding: 4px 8px;
          font-size: 12px;
          font-weight: 800;
          background: #eef3f1;
        }

        .label-chip.muted {
          background: #ffffff;
        }

        .label-meta-line {
          margin: 0;
          color: #40514c;
          font-size: 12px;
          font-weight: 700;
        }

        .toggle-item {
          display: flex;
          align-items: center;
          gap: 8px;
          min-height: 36px;
          padding: 8px 10px;
          border: 1px solid rgba(31, 42, 42, 0.14);
          border-radius: 8px;
          background: #fbfaf7;
          font-size: 13px;
          cursor: pointer;
        }

        .toggle-item.checked {
          border-color: #1f2a2a;
          background: #eef3f1;
          font-weight: 700;
        }

        .toggle-item input {
          width: 16px;
          height: 16px;
          accent-color: #1f2a2a;
        }

        .field {
          display: flex;
          flex-direction: column;
          gap: 6px;
        }

        .field-label {
          font-size: 12px;
          letter-spacing: 0.08em;
          text-transform: uppercase;
          color: #5f7b74;
        }

        .input,
        .textarea {
          border-radius: 8px;
          border: 1px solid rgba(31, 42, 42, 0.2);
          padding: 10px 12px;
          font-family: "JetBrains Mono", "Noto Sans JP", monospace;
          font-size: 13px;
          background: #fbfaf7;
        }

        .input {
          width: 100%;
        }

        .input:disabled,
        .table-input:disabled,
        .toggle-item input:disabled + span,
        .check-field input:disabled + span {
          color: #4c5955;
        }

        .input:disabled,
        .table-input:disabled,
        .textarea:disabled {
          background: #f4f5f3;
          border-color: rgba(31, 42, 42, 0.08);
          cursor: default;
        }

        .textarea:disabled {
          color: #4c5955;
        }

        .check-field {
          display: flex;
          align-items: center;
          gap: 8px;
          min-height: 42px;
          padding-top: 18px;
          font-size: 13px;
          font-weight: 600;
        }

        .check-field input {
          width: 16px;
          height: 16px;
          accent-color: #1f2a2a;
        }

        .textarea {
          min-height: 120px;
          resize: vertical;
        }

        .json-textarea {
          white-space: pre-wrap;
          word-break: break-word;
        }

        .json-panel summary {
          cursor: pointer;
          font-weight: 600;
          color: #1f2a2a;
        }

        .invoice-table {
          width: 100%;
          border-collapse: collapse;
          font-size: 12px;
        }

        .invoice-table th,
        .invoice-table td {
          border-bottom: 1px solid rgba(31, 42, 42, 0.1);
          padding: 6px;
          text-align: left;
          vertical-align: middle;
        }

        .table-wrap {
          width: 100%;
          overflow-x: auto;
        }

        .table-input {
          width: 100%;
          min-width: 92px;
          border: 1px solid rgba(31, 42, 42, 0.18);
          border-radius: 8px;
          padding: 7px 8px;
          font-family: "JetBrains Mono", "Noto Sans JP", monospace;
          font-size: 12px;
          background: #fbfaf7;
        }

        .table-input.numeric {
          min-width: 64px;
        }

        .actions {
          display: flex;
          gap: 12px;
          flex-wrap: wrap;
        }

        .save-actions {
          justify-content: flex-end;
          padding: 14px;
          border: 1px solid rgba(31, 42, 42, 0.1);
          border-radius: 8px;
          background: #f7f9f8;
        }

        .btn {
          border: none;
          border-radius: 8px;
          padding: 10px 18px;
          font-weight: 600;
          cursor: pointer;
          background: #1f2a2a;
          color: #f7f2e7;
        }

        .btn:disabled {
          opacity: 0.45;
          cursor: not-allowed;
        }

        .btn.ghost {
          background: #eef3f1;
          color: #1f2a2a;
        }

        .btn.primary {
          background: #1f2a2a;
          color: #f7f2e7;
        }

        .edit-start-button {
          min-width: 220px;
          min-height: 46px;
          box-shadow: 0 8px 18px rgba(31, 42, 42, 0.16);
        }

        .btn.compact {
          padding: 7px 12px;
          border-radius: 8px;
          font-size: 12px;
        }

        .btn.danger {
          background: #f3e8e8;
          color: #7a2d2d;
        }

        .message {
          margin-top: 16px;
          font-weight: 600;
          color: #1f2a2a;
        }

        .code-block {
          background: #f1f0ec;
          border-radius: 12px;
          padding: 12px;
          white-space: pre-wrap;
          word-break: break-word;
        }

        .json-panel {
          margin-top: 8px;
        }

        .json-panel summary {
          cursor: pointer;
          font-weight: 600;
          color: #1f2a2a;
          margin-bottom: 8px;
        }

        .json-textarea {
          white-space: pre-wrap;
          word-break: break-word;
        }

        .validation-details summary {
          cursor: pointer;
          font-weight: 600;
        }

        @media (max-width: 900px) {
          .guide-steps {
            grid-template-columns: repeat(2, minmax(0, 1fr));
          }

          .facility-grid {
            grid-template-columns: 1fr;
          }

          .overview-grid {
            grid-template-columns: 1fr;
          }

          .panel-header {
            align-items: flex-start;
            flex-direction: column;
          }

          .label-config-layout {
            grid-template-columns: 1fr;
          }
        }

        @media (max-width: 560px) {
          .guide-steps,
          .overview-grid {
            grid-template-columns: 1fr;
          }

          .area-row {
            grid-template-columns: 1fr;
          }
        }
      `}</style>
    </main>
  );
}
