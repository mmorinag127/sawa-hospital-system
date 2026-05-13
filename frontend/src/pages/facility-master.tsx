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

type FacilityEditDraft = {
  facility_id: string;
  facility_name: string;
  aliases_text: string;
  areas_text: string;
  address: string;
  phone: string;
  order_form_pattern_id: string;
  fax_template_id: string;
};

const prettyJson = (value: unknown) => JSON.stringify(value ?? {}, null, 2);

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

const facilityEntryToDraft = (facility?: FacilityEntry | null): FacilityEditDraft => {
  const record = facility && typeof facility === "object" ? (facility as Record<string, unknown>) : {};
  return {
    facility_id: readString(record.facility_id),
    facility_name: readString(record.facility_name),
    aliases_text: readStringList(record.aliases).join("\n"),
    areas_text: readAreas(record.areas)
      .map((area) => area.name)
      .join("\n"),
    address: readString(record.address),
    phone: readString(record.phone),
    order_form_pattern_id: readString(record.order_form_pattern_id),
    fax_template_id: readString(record.fax_template_id),
  };
};

const facilityDraftToEntry = (draft: FacilityEditDraft, base: FacilityEntry | null): FacilityEntry => {
  const next: FacilityEntry = { ...(base || {}) };
  next.facility_id = draft.facility_id.trim();
  next.facility_name = draft.facility_name.trim();
  next.aliases = draft.aliases_text
    .split(/\r?\n|,/)
    .map((item) => item.trim())
    .filter(Boolean);
  next.areas = draft.areas_text
    .split(/\r?\n|,/)
    .map((name) => name.trim())
    .filter(Boolean)
    .map((name) => ({ id: name, name }));
  if (draft.address.trim()) {
    next.address = draft.address.trim();
  } else {
    delete next.address;
  }
  if (draft.phone.trim()) {
    next.phone = draft.phone.trim();
  } else {
    delete next.phone;
  }
  if (draft.order_form_pattern_id.trim()) {
    next.order_form_pattern_id = draft.order_form_pattern_id.trim();
  } else {
    delete next.order_form_pattern_id;
  }
  if (draft.fax_template_id.trim()) {
    next.fax_template_id = draft.fax_template_id.trim();
    next.fax_template_ids = [draft.fax_template_id.trim()];
  }
  return next;
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
          dietType: readString(col.diet_type),
          areaId: readString(col.area_id),
        };
      })
      .filter(
        (col): col is { name: string; source: string; dietType: string; areaId: string } =>
          Boolean(col && (col.name || col.source))
      ),
  };
};

export default function FacilityMasterPage() {
  const [master, setMaster] = useState<FacilityMaster | null>(null);
  const [masterText, setMasterText] = useState("");
  const [selectedIndex, setSelectedIndex] = useState<number>(-1);
  const [facilityText, setFacilityText] = useState("");
  const [facilityDraft, setFacilityDraft] = useState<FacilityEditDraft>(() => facilityEntryToDraft(null));
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
        setFacilityText(prettyJson(nextFacilities[0]));
        setFacilityDraft(facilityEntryToDraft(nextFacilities[0]));
      } else {
        setSelectedIndex(-1);
        setFacilityText("{}");
        setFacilityDraft(facilityEntryToDraft(null));
      }
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
    setSelectedIndex(index);
    setFacilityText(prettyJson(target));
    setFacilityDraft(facilityEntryToDraft(target));
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
    setFacilityText(prettyJson(nextFacility));
    setFacilityDraft(facilityEntryToDraft(nextFacility));
    setMasterText(prettyJson(nextMaster));
    return nextMaster;
  };

  const updateFacilityDraft = (field: keyof FacilityEditDraft, value: string) => {
    setFacilityDraft((current) => ({ ...current, [field]: value }));
  };

  const applyFacilityForm = () => {
    if (!master) return null;
    if (!facilityDraft.facility_id.trim()) {
      setMessage("施設IDを入力してください。");
      return null;
    }
    if (!facilityDraft.facility_name.trim()) {
      setMessage("施設名を入力してください。");
      return null;
    }
    const duplicate = facilities.some((facility, index) => {
      if (index === selectedIndex) return false;
      return String(facility.facility_id || "").trim() === facilityDraft.facility_id.trim();
    });
    if (duplicate) {
      setMessage(`施設ID ${facilityDraft.facility_id.trim()} は既に使われています。`);
      return null;
    }
    const base = selectedIndex >= 0 && selectedIndex < facilities.length ? facilities[selectedIndex] : null;
    const nextFacility = facilityDraftToEntry(facilityDraft, base);
    return updateFacilityState(nextFacility) || master;
  };

  const applyFacilityEditor = () => {
    if (!master) return null;
    if (selectedIndex < 0 || facilities.length === 0) {
      return master;
    }
    const parsed = parseJson(facilityText);
    if (parsed.error) {
      setMessage(`施設JSONエラー: ${parsed.error}`);
      return null;
    }
    if (!parsed.value || typeof parsed.value !== "object") {
      setMessage("施設JSONはオブジェクトで入力してください。");
      return null;
    }
    const nextFacility = { ...(parsed.value as FacilityEntry) };
    delete (nextFacility as Record<string, unknown>).main_ocr_facility_prompt;
    delete (nextFacility as Record<string, unknown>).ocr_prompt;
    delete (nextFacility as Record<string, unknown>).facility_prompt;
    return updateFacilityState(nextFacility) || master;
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
    };
    const nextFacilities = [...facilities, nextFacility];
    const nextMaster = { ...master, facilities: nextFacilities };
    setMaster(nextMaster);
    setSelectedIndex(nextFacilities.length - 1);
    setFacilityText(prettyJson(nextFacility));
    setFacilityDraft(facilityEntryToDraft(nextFacility));
    setMasterText(prettyJson(nextMaster));
    setMessage("新規施設を追加しました。");
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
      setFacilityText(prettyJson(nextFacilities[0]));
      setFacilityDraft(facilityEntryToDraft(nextFacilities[0]));
    } else {
      setSelectedIndex(-1);
      setFacilityText("{}");
      setFacilityDraft(facilityEntryToDraft(null));
    }
    setMessage("Master JSON を反映しました。");
  };

  const saveMaster = async () => {
    if (!master) return;
    const nextMaster = applyFacilityForm();
    if (!nextMaster) return;
    try {
      const res = await apiClient.put("/facility-master", nextMaster);
      const updatedMaster = res.data.facility_master as FacilityMaster;
      setMaster(updatedMaster);
      setMasterText(prettyJson(updatedMaster));
      setValidation(res.data.validation || null);
      setPath(res.data.path || path);
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
      aliases: readStringList(record.aliases),
      areas: readAreas(record.areas),
      invoice: readInvoiceTemplate(selectedFacility),
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
          <button className="btn" onClick={addFacility}>
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
              <div className="form-grid">
                <label className="field">
                  <span className="field-label">施設ID</span>
                  <input
                    className="input"
                    value={facilityDraft.facility_id}
                    onChange={(e) => updateFacilityDraft("facility_id", e.target.value)}
                    placeholder="FAC00017"
                  />
                </label>
                <label className="field">
                  <span className="field-label">施設名</span>
                  <input
                    className="input"
                    value={facilityDraft.facility_name}
                    onChange={(e) => updateFacilityDraft("facility_name", e.target.value)}
                    placeholder="ケアホーム長生苑"
                  />
                </label>
                <label className="field">
                  <span className="field-label">注文書テンプレート</span>
                  <input
                    className="input"
                    value={facilityDraft.fax_template_id}
                    onChange={(e) => updateFacilityDraft("fax_template_id", e.target.value)}
                    placeholder="fax_layout_regular_forbidden_v1"
                  />
                </label>
                <label className="field">
                  <span className="field-label">注文書パターン</span>
                  <input
                    className="input"
                    value={facilityDraft.order_form_pattern_id}
                    onChange={(e) => updateFacilityDraft("order_form_pattern_id", e.target.value)}
                    placeholder="PATTERN_A"
                  />
                </label>
                <label className="field">
                  <span className="field-label">住所</span>
                  <input
                    className="input"
                    value={facilityDraft.address}
                    onChange={(e) => updateFacilityDraft("address", e.target.value)}
                  />
                </label>
                <label className="field">
                  <span className="field-label">電話番号</span>
                  <input
                    className="input"
                    value={facilityDraft.phone}
                    onChange={(e) => updateFacilityDraft("phone", e.target.value)}
                  />
                </label>
                <label className="field form-wide">
                  <span className="field-label">別名 (改行またはカンマ区切り)</span>
                  <textarea
                    className="textarea compact-textarea"
                    value={facilityDraft.aliases_text}
                    onChange={(e) => updateFacilityDraft("aliases_text", e.target.value)}
                    rows={3}
                  />
                </label>
                <label className="field form-wide">
                  <span className="field-label">施設区分/エリア (改行またはカンマ区切り)</span>
                  <textarea
                    className="textarea compact-textarea"
                    value={facilityDraft.areas_text}
                    onChange={(e) => updateFacilityDraft("areas_text", e.target.value)}
                    rows={3}
                  />
                </label>
              </div>
              <div className="actions">
                <button className="btn ghost" onClick={applyFacilityForm}>
                  フォーム内容を反映
                </button>
                <button className="btn primary" onClick={saveMaster}>
                  施設一覧を保存
                </button>
              </div>
              <div className="detail-grid">
                <div className="detail-card">
                  <p className="detail-label">施設ID</p>
                  <p className="detail-value">{facilityInfo?.id || "-"}</p>
                </div>
                <div className="detail-card">
                  <p className="detail-label">施設名</p>
                  <p className="detail-value">{facilityInfo?.name || "-"}</p>
                </div>
                <div className="detail-card">
                  <p className="detail-label">住所</p>
                  <p className="detail-value">{facilityInfo?.address || "-"}</p>
                </div>
                <div className="detail-card">
                  <p className="detail-label">電話番号</p>
                  <p className="detail-value">{facilityInfo?.phone || "-"}</p>
                </div>
                <div className="detail-card">
                  <p className="detail-label">注文書パターン</p>
                  <p className="detail-value">{facilityInfo?.orderFormPatternId || "未設定"}</p>
                </div>
              </div>
              <div className="detail-card detail-wide">
                <p className="detail-label">別名</p>
                {facilityInfo?.aliases?.length ? (
                  <div className="chip-row">
                    {facilityInfo.aliases.map((alias) => (
                      <span key={alias} className="chip">
                        {alias}
                      </span>
                    ))}
                  </div>
                ) : (
                  <p className="subtle">未設定</p>
                )}
              </div>
              <div className="detail-card detail-wide">
                <p className="detail-label">施設区分</p>
                {facilityInfo?.areas?.length ? (
                  <div className="chip-row">
                    {facilityInfo.areas.map((area) => (
                      <span key={`${area.id}-${area.name}`} className="chip">
                        {area.name}
                      </span>
                    ))}
                  </div>
                ) : (
                  <p className="subtle">未設定</p>
                )}
              </div>
              <div className="detail-card detail-wide">
                <p className="detail-label">納品書テンプレート</p>
                {facilityInfo?.invoice ? (
                  <>
                    {facilityInfo.invoice.templateUri ? (
                      <p className="detail-meta">template_uri: {facilityInfo.invoice.templateUri}</p>
                    ) : (
                      <p className="detail-meta">template_uri: 未設定</p>
                    )}
                    {facilityInfo.invoice.sheetName ? (
                      <p className="detail-meta">sheet_name: {facilityInfo.invoice.sheetName}</p>
                    ) : (
                      <p className="detail-meta">sheet_name: 未設定</p>
                    )}
                    <p className="detail-meta">
                      include_menu_name: {facilityInfo.invoice.includeMenuName ? "true" : "false"}
                    </p>
                    {facilityInfo.invoice.columns.length ? (
                      <div className="table-wrap">
                        <table className="invoice-table">
                          <thead>
                            <tr>
                              <th>列名</th>
                              <th>ソース</th>
                              <th>区分</th>
                              <th>エリア</th>
                            </tr>
                          </thead>
                          <tbody>
                            {facilityInfo.invoice.columns.map((col, idx) => (
                              <tr key={`${col.name}-${col.source}-${idx}`}>
                                <td>{col.name || "-"}</td>
                                <td>{col.source || "-"}</td>
                                <td>{col.dietType || "-"}</td>
                                <td>{col.areaId || "-"}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ) : (
                      <p className="subtle">納品書カラムが未設定です。</p>
                    )}
                  </>
                ) : (
                  <p className="subtle">納品書テンプレートが未設定です。</p>
                )}
              </div>
              <details className="json-panel">
                <summary>施設JSON (上級)</summary>
                <label className="field">
                  <span className="field-label">施設JSON</span>
                  <textarea
                    className="textarea json-textarea"
                    value={facilityText}
                    onChange={(e) => setFacilityText(e.target.value)}
                    rows={14}
                    wrap="soft"
                  />
                </label>
              </details>
              <div className="actions">
                <button className="btn ghost" onClick={applyFacilityEditor}>
                  施設編集を反映
                </button>
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

        .form-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
          gap: 12px;
          padding: 14px;
          border-radius: 16px;
          border: 1px solid rgba(31, 42, 42, 0.12);
          background: #fbfaf7;
        }

        .form-wide {
          grid-column: 1 / -1;
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

        .textarea {
          min-height: 120px;
          resize: vertical;
        }

        .compact-textarea {
          min-height: 72px;
          font-family: "Manrope", "Noto Sans JP", sans-serif;
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
          font-size: 12px;
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

        .btn.ghost {
          background: #eef3f1;
          color: #1f2a2a;
        }

        .btn.primary {
          background: #1f2a2a;
          color: #f7f2e7;
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
