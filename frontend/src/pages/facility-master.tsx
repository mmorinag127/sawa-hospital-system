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

type InvoiceColumn = {
  name: string;
  source: string;
  header: string;
  columnIndex: string;
  dietType: string;
  areaId: string;
};

const DAILY_LABEL_DIET_OPTIONS = [
  { value: "soft", label: "軟菜" },
  { value: "mixer", label: "ミキサー" },
  { value: "daycare", label: "通所" },
  { value: "staff", label: "職員" },
  { value: "diabetes", label: "糖尿" },
  { value: "no_meat", label: "肉禁" },
  { value: "no_fish", label: "魚禁" },
  { value: "no_fried", label: "揚げ物禁" },
  { value: "forbidden_other", label: "その他禁食" },
  { value: "sesame_allergy", label: "ごま禁" },
];

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

const normalizeInvoiceColumn = (column: InvoiceColumn) => {
  const next: Record<string, unknown> = {};
  if (column.name.trim()) next.name = column.name.trim();
  if (column.source.trim()) next.source = column.source.trim();
  if (column.header.trim()) next.header = column.header.trim();
  if (column.columnIndex.trim()) {
    const parsed = Number(column.columnIndex);
    next.column_index = Number.isFinite(parsed) ? parsed : column.columnIndex.trim();
  }
  if (column.dietType.trim()) next.diet_type = column.dietType.trim();
  if (column.areaId.trim()) next.area_id = column.areaId.trim();
  return next;
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
        if (record.invoice_template && typeof record.invoice_template === "object") {
          const invoice = record.invoice_template as Record<string, unknown>;
          const columns = readInvoiceTemplate(record)?.columns || [];
          record.invoice_template = {
            ...invoice,
            columns: columns.map(normalizeInvoiceColumn).filter((column) => Object.keys(column).length > 0),
          };
        }
        return record;
      })
    : master.facilities;
  return { ...master, facilities };
};


const readInvoiceTemplate = (facility?: FacilityEntry) => {
  if (!facility || typeof facility !== "object") return null;
  const invoice = (facility as Record<string, unknown>).invoice_template;
  if (!invoice || typeof invoice !== "object") return null;
  const invoiceRecord = invoice as Record<string, unknown>;
  const columns = Array.isArray(invoiceRecord.columns) ? invoiceRecord.columns : [];
  return {
    templateUri: readString(invoiceRecord.template_uri),
    sheetName: readString(invoiceRecord.sheet_name),
    includeMenuName: Boolean(invoiceRecord.include_menu_name),
    columns: columns
      .map((column) => {
        if (!column || typeof column !== "object") return null;
        const col = column as Record<string, unknown>;
        return {
          name: readString(col.name),
          source: readString(col.source),
          header: readString(col.header),
          columnIndex: toStringValue(col.column_index),
          dietType: readString(col.diet_type),
          areaId: readString(col.area_id),
        };
      })
      .filter(
        (col): col is InvoiceColumn =>
          Boolean(col && (col.name || col.source || col.header || col.columnIndex || col.dietType || col.areaId))
      ),
  };
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

  const facilities = useMemo(() => {
    if (!master?.facilities || !Array.isArray(master.facilities)) return [];
    return master.facilities;
  }, [master]);

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

  const updateInvoiceTemplate = (patch: Record<string, unknown>) => {
    if (!selectedFacility || typeof selectedFacility !== "object") return;
    const current = ((selectedFacility as Record<string, unknown>).invoice_template || {}) as Record<string, unknown>;
    updateSelectedField("invoice_template", { ...current, ...patch });
  };

  const updateInvoiceColumn = (index: number, patch: Partial<InvoiceColumn>) => {
    if (!selectedFacility || typeof selectedFacility !== "object") return;
    const invoice = readInvoiceTemplate(selectedFacility);
    const columns = invoice?.columns || [];
    const nextColumns = columns.map((column, itemIndex) =>
      itemIndex === index ? { ...column, ...patch } : column
    );
    updateInvoiceTemplate({ columns: nextColumns.map(normalizeInvoiceColumn) });
  };

  const addInvoiceColumn = () => {
    if (!selectedFacility || typeof selectedFacility !== "object") return;
    const invoice = readInvoiceTemplate(selectedFacility);
    const columns = invoice?.columns || [];
    updateInvoiceTemplate({
      columns: [
        ...columns.map(normalizeInvoiceColumn),
        { name: "", source: "quantity", header: "", column_index: "", diet_type: "", area_id: "" },
      ],
    });
  };

  const removeInvoiceColumn = (index: number) => {
    if (!selectedFacility || typeof selectedFacility !== "object") return;
    const invoice = readInvoiceTemplate(selectedFacility);
    const columns = invoice?.columns || [];
    updateInvoiceTemplate({
      columns: columns
        .filter((_, itemIndex) => itemIndex !== index)
        .map(normalizeInvoiceColumn),
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
      invoice_template: {
        template_uri: "",
        sheet_name: "",
        include_menu_name: false,
        columns: [],
      },
    };
    const nextFacilities = [...facilities, nextFacility];
    const nextMaster = { ...master, facilities: nextFacilities };
    setMaster(nextMaster);
    setSelectedIndex(nextFacilities.length - 1);
    setEditingIndex(nextFacilities.length - 1);
    setEditBaseline(null);
    setMasterText(prettyJson(nextMaster));
    setMessage("新規施設を追加しました。編集内容を保存してください。");
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
    if (!master) return;
    const payload = sanitizeMasterForSave(master);
    try {
      const res = await apiClient.put("/facility-master", payload);
      const updatedMaster = res.data.facility_master as FacilityMaster;
      setMaster(updatedMaster);
      setMasterText(prettyJson(updatedMaster));
      setValidation(res.data.validation || null);
      setPath(res.data.path || path);
      setEditingIndex(-1);
      setEditBaseline(null);
      setMessage("Facility master を保存しました。");
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      if (detail?.errors) {
        setValidation({ errors: detail.errors, warnings: [] });
        setMessage("Facility master の検証に失敗しました。");
        return;
      }
      setMessage("Facility master の保存に失敗しました。");
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
      address: readString(record.address),
      phone: readString(record.phone),
      orderFormPatternId: readString(record.order_form_pattern_id),
      faxTemplateId: readString(record.fax_template_id),
      faxTemplateIds: readEditableStringList(record.fax_template_ids),
      aliases: readEditableStringList(record.aliases),
      areas: readEditableAreas(record.areas),
      invoice: readInvoiceTemplate(selectedFacility),
      dailyLabelDietTypes: readDailyLabelDietTypes(selectedFacility),
    };
  }, [selectedFacility]);

  return (
    <main className="page">
      <header className="hero">
        <div>
          <p className="eyebrow">Facilities</p>
          <h1>施設一覧</h1>
          <p className="subtle">施設情報と納品書テンプレート設定を更新します。</p>
        </div>
        <TopNav />
      </header>

      <section className="panel">
        <header className="panel-header">
          <h2>概要</h2>
          <span className="badge">{facilities.length}</span>
        </header>
        <div className="summary">
          <div>
            <p className="summary-label">schema_version</p>
            <p className="summary-value">{master?.schema_version || "-"}</p>
          </div>
          <div>
            <p className="summary-label">updated_at</p>
            <p className="summary-value">{master?.updated_at || "-"}</p>
          </div>
          <div>
            <p className="summary-label">path</p>
            <p className="summary-value">{path || "-"}</p>
          </div>
        </div>
      </section>

      <section className="panel">
        <header className="panel-header">
          <h2>施設一覧</h2>
          <button className="btn" onClick={addFacility} disabled={isEditing}>
            新規追加
          </button>
        </header>
        {facilities.length === 0 ? (
          <p className="subtle">まだ施設がありません。</p>
        ) : (
          <div className="facility-grid">
            <div className="list">
              {facilities.map((facility, index) => {
                const id = String(facility.facility_id || "unknown");
                const name = String(facility.facility_name || "未設定");
                const isActive = index === selectedIndex;
                return (
                  <button
                    key={`${id}-${index}`}
                    className={`list-item ${isActive ? "active" : ""}`}
                    onClick={() => selectFacility(index)}
                  >
                    <div>
                      <p className="list-title">{name}</p>
                      <p className="list-meta">{id}</p>
                    </div>
                    <span className="ghost-link">編集</span>
                  </button>
                );
              })}
            </div>
            <div className="editor">
              <div className="form-section">
                <div className="section-title-row">
                  <div>
                    <h3>基本情報</h3>
                    <p className="mode-text">{isEditing ? "編集中" : "閲覧中"}</p>
                  </div>
                  {isEditing ? (
                    <div className="actions compact-actions">
                      <button className="btn ghost compact" onClick={cancelEdit}>
                        キャンセル
                      </button>
                      <button className="btn primary compact" onClick={saveMaster}>
                        保存
                      </button>
                    </div>
                  ) : (
                    <button className="btn compact" onClick={beginEdit} disabled={!selectedFacility}>
                      編集
                    </button>
                  )}
                </div>
                <div className="form-grid">
                  <label className="field">
                    <span className="field-label">施設ID</span>
                    <input
                      className="input"
                      value={facilityInfo?.id || ""}
                      disabled={!isEditing}
                      onChange={(e) => updateSelectedField("facility_id", e.target.value)}
                    />
                  </label>
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
                  <label className="field">
                    <span className="field-label">注文書パターンID</span>
                    <input
                      className="input"
                      value={facilityInfo?.orderFormPatternId || ""}
                      disabled={!isEditing}
                      onChange={(e) => updateSelectedField("order_form_pattern_id", e.target.value)}
                    />
                  </label>
                  <label className="field">
                    <span className="field-label">FAXテンプレートID</span>
                    <input
                      className="input"
                      value={facilityInfo?.faxTemplateId || ""}
                      disabled={!isEditing}
                      onChange={(e) => updateSelectedField("fax_template_id", e.target.value)}
                    />
                  </label>
                </div>
              </div>

              <div className="form-section">
                <div className="section-title-row">
                  <h3>別名</h3>
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
                  <p className="subtle">未設定です。追加ボタンから登録してください。</p>
                )}
              </div>

              <div className="form-section">
                <div className="section-title-row">
                  <h3>FAXテンプレート候補</h3>
                  <button
                    className="btn compact"
                    onClick={() => addStringListItem("fax_template_ids")}
                    disabled={!isEditing}
                  >
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
                  <p className="subtle">未設定です。単一テンプレートの場合は基本情報のFAXテンプレートIDを使用します。</p>
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
                          <span className="field-label">区分ID</span>
                          <input
                            className="input"
                            value={area.id}
                            disabled={!isEditing}
                            onChange={(e) => updateArea(index, { id: e.target.value })}
                          />
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
                <h3>ラベル比較・表示対象区分</h3>
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
                  未選択の場合はシステム既定値を使用します。施設固有に確定した区分だけ選択してください。
                </p>
              </div>

              <div className="form-section">
                <div className="section-title-row">
                  <h3>納品書テンプレート</h3>
                  <button className="btn compact" onClick={addInvoiceColumn} disabled={!isEditing}>
                    列を追加
                  </button>
                </div>
                <div className="form-grid">
                  <label className="field">
                    <span className="field-label">テンプレートURI</span>
                    <input
                      className="input"
                      value={facilityInfo?.invoice?.templateUri || ""}
                      disabled={!isEditing}
                      onChange={(e) => updateInvoiceTemplate({ template_uri: e.target.value })}
                    />
                  </label>
                  <label className="field">
                    <span className="field-label">シート名</span>
                    <input
                      className="input"
                      value={facilityInfo?.invoice?.sheetName || ""}
                      disabled={!isEditing}
                      onChange={(e) => updateInvoiceTemplate({ sheet_name: e.target.value })}
                    />
                  </label>
                  <label className="check-field">
                    <input
                      type="checkbox"
                      checked={Boolean(facilityInfo?.invoice?.includeMenuName)}
                      disabled={!isEditing}
                      onChange={(e) => updateInvoiceTemplate({ include_menu_name: e.target.checked })}
                    />
                    <span>献立名を含める</span>
                  </label>
                </div>
                {facilityInfo?.invoice?.columns.length ? (
                  <div className="table-wrap">
                    <table className="invoice-table">
                      <thead>
                        <tr>
                          <th>列名</th>
                          <th>ソース</th>
                          <th>ヘッダー</th>
                          <th>列番号</th>
                          <th>食種</th>
                          <th>施設区分</th>
                          <th>操作</th>
                        </tr>
                      </thead>
                      <tbody>
                        {facilityInfo.invoice.columns.map((col, idx) => (
                          <tr key={`${col.name}-${col.source}-${idx}`}>
                            <td>
                              <input
                                className="table-input"
                                value={col.name}
                                disabled={!isEditing}
                                onChange={(e) => updateInvoiceColumn(idx, { name: e.target.value })}
                              />
                            </td>
                            <td>
                              <input
                                className="table-input"
                                value={col.source}
                                disabled={!isEditing}
                                onChange={(e) => updateInvoiceColumn(idx, { source: e.target.value })}
                              />
                            </td>
                            <td>
                              <input
                                className="table-input"
                                value={col.header}
                                disabled={!isEditing}
                                onChange={(e) => updateInvoiceColumn(idx, { header: e.target.value })}
                              />
                            </td>
                            <td>
                              <input
                                className="table-input numeric"
                                value={col.columnIndex}
                                disabled={!isEditing}
                                onChange={(e) => updateInvoiceColumn(idx, { columnIndex: e.target.value })}
                              />
                            </td>
                            <td>
                              <input
                                className="table-input"
                                value={col.dietType}
                                disabled={!isEditing}
                                onChange={(e) => updateInvoiceColumn(idx, { dietType: e.target.value })}
                              />
                            </td>
                            <td>
                              <input
                                className="table-input"
                                value={col.areaId}
                                disabled={!isEditing}
                                onChange={(e) => updateInvoiceColumn(idx, { areaId: e.target.value })}
                              />
                            </td>
                            <td>
                              <button
                                className="btn danger compact"
                                onClick={() => removeInvoiceColumn(idx)}
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
                ) : (
                  <p className="subtle">納品書カラムが未設定です。列を追加してください。</p>
                )}
              </div>
            </div>
          </div>
        )}
      </section>

      <section className="panel">
        <details className="json-panel">
          <summary>Master JSON (上級)</summary>
          <div className="actions">
            <button className="btn ghost" onClick={applyMasterJson}>
              JSON を反映
            </button>
          </div>
          <textarea
            className="textarea json-textarea"
            value={masterText}
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
          background: #1f2a2a;
          color: #fff9ef;
          padding: 6px 12px;
          border-radius: 999px;
          font-size: 12px;
        }

        .summary {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
          gap: 16px;
        }

        .summary-label {
          font-size: 12px;
          color: #5f7b74;
          margin: 0 0 6px;
          text-transform: uppercase;
          letter-spacing: 0.08em;
        }

        .summary-value {
          margin: 0;
          font-weight: 600;
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

        .list-item {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          padding: 12px 14px;
          border-radius: 14px;
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
          border-radius: 10px;
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

        .detail-card {
          border-radius: 14px;
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

        .detail-meta {
          margin: 0 0 8px;
          font-size: 12px;
          color: #5f7b74;
          word-break: break-word;
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
          border-radius: 12px;
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
        .table-input:disabled {
          background: #f4f5f3;
          border-color: rgba(31, 42, 42, 0.08);
          cursor: default;
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

        .btn {
          border: none;
          border-radius: 999px;
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
          .facility-grid {
            grid-template-columns: 1fr;
          }
        }
      `}</style>
    </main>
  );
}
