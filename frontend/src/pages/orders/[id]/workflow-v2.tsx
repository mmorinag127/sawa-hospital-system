import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/router";
import Link from "next/link";

import { apiClient } from "../../../services/apiClient";
import TopNav from "../../../components/TopNav";
import {
  deriveWeekValueFromCalendarRange,
  formatWeekLabel,
  isConcreteWeekValue,
  normalizeConcreteWeekValue,
  normalizeWeekValue,
} from "../../../features/orders/orderDetailUtils";

type WorkflowV2 = {
  order_id: string;
  state: string;
  headline?: string | null;
  primary_action?: string | null;
  selected_ocr_result_id?: string | null;
  saved_sheet_id?: string | null;
  confirmed_snapshot_id?: string | null;
  facility_id?: string | null;
  week_start?: string | null;
  week_end?: string | null;
  template_id?: string | null;
  bagging_result_id?: string | null;
  output_bundle_id?: string | null;
  ocr_job?: {
    ocr_job_id?: string | null;
    status?: string | null;
    created_at?: string | null;
    updated_at?: string | null;
    elapsed_seconds?: number | null;
    processing_stage?: string | null;
    result_state?: string | null;
  } | null;
};

type OcrResult = {
  ocr_result_id: string;
  status?: string | null;
  source?: string | null;
  selected?: boolean;
  artifact_digest?: string | null;
  artifact_manifest?: Record<string, unknown> | null;
  overlay_url?: string | null;
  overlay_status?: string | null;
  overlay_message?: string | null;
  created_at?: string | null;
};

type InspectionPayload = {
  workflow?: WorkflowV2;
  ocr_results?: OcrResult[];
  saved_sheet?: {
    saved_sheet_id: string;
    source_ocr_result_id?: string | null;
    sheet?: Record<string, unknown>;
  } | null;
  artifact_lineage?: Record<string, unknown>;
  bagging_result?: Record<string, unknown> | null;
  output_bundle?: Record<string, unknown> | null;
};

type SheetPayload = {
  fields: string[];
  header: string[];
  rows: string[][];
  row_ids?: string[];
  [key: string]: unknown;
};

type FacilityOption = {
  id: string;
  name: string;
};

type WeekOption = {
  week_id: string;
  label: string;
  date_from?: string | null;
  date_to?: string | null;
  selected?: boolean;
};

const emptyContext = {
  facility_id: "",
  week_start: "",
  week_end: "",
};

const defaultSheet = {
  rows: [
    {
      date: "",
      daypart: "",
      menu_name: "",
    },
  ],
};

const formatJson = (value: unknown) => JSON.stringify(value ?? null, null, 2);

const formatApiError = (err: any, fallback: string) => {
  const detail = err?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    const message = (detail as { message?: unknown }).message;
    if (typeof message === "string" && message.trim()) return message;
    return JSON.stringify(detail);
  }
  return String(err?.message || fallback);
};

const normalizeSheetPayload = (value: unknown): SheetPayload | null => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const raw = value as Record<string, unknown>;
  const rows = Array.isArray(raw.rows)
    ? raw.rows
        .filter((row): row is unknown[] => Array.isArray(row))
        .map((row) => row.map((cell) => String(cell ?? "")))
    : [];
  const width = Math.max(
    Array.isArray(raw.fields) ? raw.fields.length : 0,
    Array.isArray(raw.header) ? raw.header.length : 0,
    ...rows.map((row) => row.length),
    1,
  );
  const fields = Array.from({ length: width }, (_, idx) => {
    const valueAtIndex = Array.isArray(raw.fields) ? raw.fields[idx] : undefined;
    return String(valueAtIndex ?? `col${idx + 1}`);
  });
  const header = Array.from({ length: width }, (_, idx) => {
    const valueAtIndex = Array.isArray(raw.header) ? raw.header[idx] : undefined;
    return String(valueAtIndex ?? fields[idx] ?? `col${idx + 1}`);
  });
  return {
    ...raw,
    fields,
    header,
    rows: rows.map((row) => Array.from({ length: width }, (_, idx) => String(row[idx] ?? ""))),
  };
};

const stateLabel = (state?: string | null) => {
  const normalized = String(state || "").trim();
  const labels: Record<string, string> = {
    uploaded: "Step1: PDF/施設/週次確認",
    context_confirmed: "Step1完了: OCR実行待ち",
    ocr_running: "Step1: OCR実行中",
    ocr_selected: "Step2完了: 正解OCR選択済み",
    sheet_saved: "Step3完了: シート保存済み",
    bagging_ready: "Step4: 袋分け確認",
    bagging_confirmed: "Step4完了: 出力確認待ち",
    output_review: "Step5: 出力確認",
    confirmed: "確定済み",
  };
  return labels[normalized] || normalized || "未開始";
};

const stepIndexForState = (state?: string | null) => {
  const normalized = String(state || "").trim();
  if (["uploaded", "context_confirmed", "ocr_running", "ocr_failed"].includes(normalized)) return 1;
  if (["ocr_completed", "ocr_selected"].includes(normalized)) return 2;
  if (["sheet_saved"].includes(normalized)) return 3;
  if (["bagging_ready", "bagging_confirmed"].includes(normalized)) return 4;
  if (["output_review", "confirmed"].includes(normalized)) return 5;
  return 1;
};

const stepLabels = [
  { step: 1, label: "PDF/施設/週次" },
  { step: 2, label: "OCR選択" },
  { step: 3, label: "シート編集" },
  { step: 4, label: "袋分け" },
  { step: 5, label: "出力確認" },
];

const formatFacilityLabel = (facility: FacilityOption) => {
  if (!facility.name) return facility.id;
  return `${facility.name} (${facility.id})`;
};

const weekValueFromRange = (weekStart?: string | null, weekEnd?: string | null) =>
  normalizeConcreteWeekValue(deriveWeekValueFromCalendarRange(weekStart || "", weekEnd || ""));

const weekRangeFromValue = (value?: string | null) => {
  const normalized = normalizeConcreteWeekValue(value);
  const match = normalized.match(/^\d{4}-\d{2}@(\d{4}-\d{2}-\d{2})~(\d{4}-\d{2}-\d{2})$/);
  if (!match) return { week_start: "", week_end: "" };
  return { week_start: match[1], week_end: match[2] };
};

const formatElapsedSeconds = (value?: number | null) => {
  if (typeof value !== "number" || Number.isNaN(value)) return "未計測";
  if (value < 60) return `${value.toFixed(1)}秒`;
  const minutes = Math.floor(value / 60);
  const seconds = Math.round(value - minutes * 60);
  return `${minutes}分${String(seconds).padStart(2, "0")}秒`;
};

const formatDateTime = (value?: string | null) => {
  if (!value) return "-";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString("ja-JP", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
};

export default function OrderWorkflowV2Page() {
  const router = useRouter();
  const orderId = typeof router.query.id === "string" ? router.query.id : "";
  const [workflow, setWorkflow] = useState<WorkflowV2 | null>(null);
  const [ocrResults, setOcrResults] = useState<OcrResult[]>([]);
  const [inspection, setInspection] = useState<InspectionPayload | null>(null);
  const [contextForm, setContextForm] = useState(emptyContext);
  const [weekDraft, setWeekDraft] = useState<string>("");
  const [weekOptions, setWeekOptions] = useState<WeekOption[]>([]);
  const [weekOptionsLoading, setWeekOptionsLoading] = useState<boolean>(false);
  const [weekOptionsError, setWeekOptionsError] = useState<string>("");
  const [facilityOptions, setFacilityOptions] = useState<FacilityOption[]>([]);
  const [facilityOptionsLoading, setFacilityOptionsLoading] = useState(false);
  const [facilityOptionsError, setFacilityOptionsError] = useState("");
  const [customWeekRangeStart, setCustomWeekRangeStart] = useState<string>("");
  const [customWeekRangeEnd, setCustomWeekRangeEnd] = useState<string>("");
  const [sheetJson, setSheetJson] = useState(formatJson(defaultSheet));
  const [sheetPayload, setSheetPayload] = useState<SheetPayload | null>(null);
  const [visibleStep, setVisibleStep] = useState(1);
  const [pdfUrl, setPdfUrl] = useState<string>("");
  const [pdfError, setPdfError] = useState<string>("");
  const [busy, setBusy] = useState<string>("");
  const [message, setMessage] = useState<string>("");
  const [error, setError] = useState<string>("");

  const selectedOcr = useMemo(
    () => ocrResults.find((item) => item.selected || item.ocr_result_id === workflow?.selected_ocr_result_id) || null,
    [ocrResults, workflow?.selected_ocr_result_id],
  );

  const selectedWeekValue = useMemo(
    () => normalizeConcreteWeekValue(weekDraft) || weekValueFromRange(contextForm.week_start, contextForm.week_end),
    [contextForm.week_end, contextForm.week_start, weekDraft],
  );

  const contextReady = Boolean(contextForm.facility_id.trim() && contextForm.week_start && contextForm.week_end);

  const applyWeekValue = (value: string) => {
    const range = weekRangeFromValue(value);
    setWeekDraft(value);
    setContextForm((current) => ({
      ...current,
      week_start: range.week_start,
      week_end: range.week_end,
    }));
  };

  const refreshAll = async () => {
    if (!orderId) return;
    const [workflowRes, ocrRes, inspectionRes] = await Promise.all([
      apiClient.get<WorkflowV2>(`/orders/${orderId}/workflow-v2`),
      apiClient.get<{ results: OcrResult[] }>(`/orders/${orderId}/workflow-v2/ocr-results`),
      apiClient.get<InspectionPayload>(`/orders/${orderId}/workflow-v2/inspection`),
    ]);
    setWorkflow(workflowRes.data);
    setOcrResults(Array.isArray(ocrRes.data.results) ? ocrRes.data.results : []);
    setInspection(inspectionRes.data);
    const savedSheet = inspectionRes.data.saved_sheet?.sheet;
    if (savedSheet) {
      const normalizedSavedSheet = normalizeSheetPayload(savedSheet);
      setSheetPayload(normalizedSavedSheet);
      setSheetJson(formatJson(normalizedSavedSheet || savedSheet));
    }
    setContextForm({
      facility_id: workflowRes.data.facility_id || "",
      week_start: workflowRes.data.week_start || "",
      week_end: workflowRes.data.week_end || "",
    });
    const workflowWeekValue = weekValueFromRange(workflowRes.data.week_start, workflowRes.data.week_end);
    setWeekDraft(workflowWeekValue);
  };

  useEffect(() => {
    if (!router.isReady || !orderId) return;
    refreshAll().catch((err) => {
      setError(formatApiError(err, "workflow-v2 の取得に失敗しました"));
    });
  }, [router.isReady, orderId]);

  useEffect(() => {
    let active = true;
    const loadFacilities = async () => {
      setFacilityOptionsLoading(true);
      setFacilityOptionsError("");
      try {
        const res = await apiClient.get("/facilities");
        if (!active) return;
        const raw = Array.isArray(res.data?.facilities) ? res.data.facilities : [];
        const normalized = raw
          .map((item: any) => ({
            id: String(item?.id || ""),
            name: String(item?.name || ""),
          }))
          .filter((item: FacilityOption) => item.id);
        normalized.sort((a: FacilityOption, b: FacilityOption) => (
          a.name.localeCompare(b.name, "ja") || a.id.localeCompare(b.id, "ja")
        ));
        setFacilityOptions(normalized);
      } catch {
        if (!active) return;
        setFacilityOptions([]);
        setFacilityOptionsError("施設一覧の取得に失敗しました。");
      } finally {
        if (active) setFacilityOptionsLoading(false);
      }
    };
    loadFacilities();
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!router.isReady || !orderId) return;
    let active = true;
    const loadWeekOptions = async () => {
      setWeekOptionsLoading(true);
      setWeekOptionsError("");
      try {
        const res = await apiClient.get(`/orders/${orderId}/week-options`);
        if (!active) return;
        const options = Array.isArray(res.data?.options)
          ? res.data.options
              .map((item: any) => ({
                week_id: normalizeWeekValue(item?.week_id),
                label: String(item?.label || item?.week_id || ""),
                date_from: typeof item?.date_from === "string" ? item.date_from : null,
                date_to: typeof item?.date_to === "string" ? item.date_to : null,
                selected: Boolean(item?.selected),
              }))
              .filter((item: WeekOption) => item.week_id)
          : [];
        setWeekOptions(options);
        setWeekDraft((current) => {
          const currentNormalized = normalizeWeekValue(current);
          if (currentNormalized) return currentNormalized;
          const workflowWeekValue = weekValueFromRange(workflow?.week_start, workflow?.week_end);
          if (workflowWeekValue) return workflowWeekValue;
          return options.find((item: WeekOption) => item.selected)?.week_id || "";
        });
      } catch (err: any) {
        if (!active) return;
        if (err?.response?.status !== 404) {
          setWeekOptionsError("週候補の取得に失敗しました。必要なら例外範囲を設定してください。");
        }
        setWeekOptions([]);
      } finally {
        if (active) setWeekOptionsLoading(false);
      }
    };
    loadWeekOptions();
    return () => {
      active = false;
    };
  }, [router.isReady, orderId, workflow?.week_end, workflow?.week_start]);

  useEffect(() => {
    if (workflow?.state) {
      setVisibleStep(stepIndexForState(workflow.state));
    }
  }, [workflow?.state]);

  useEffect(() => {
    if (!router.isReady || !orderId) return;
    let active = true;
    let objectUrl = "";
    setPdfUrl("");
    setPdfError("");
    apiClient
      .get<Blob>(`/orders/${orderId}/document`, { responseType: "blob" })
      .then((res) => {
        if (!active) return;
        objectUrl = URL.createObjectURL(res.data);
        setPdfUrl(objectUrl);
      })
      .catch((err) => {
        if (!active) return;
        const status = err?.response?.status;
        setPdfError(status === 404 ? "原本FAX PDFを現在取得できません。" : "PDFの取得に失敗しました。");
      });
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [router.isReady, orderId]);

  const runAction = async (
    label: string,
    action: () => Promise<void>,
    options: { successMessage?: string; nextStep?: number } = {},
  ) => {
    setBusy(label);
    setError("");
    setMessage("");
    try {
      await action();
      await refreshAll();
      if (options.nextStep) {
        setVisibleStep(options.nextStep);
      }
      setMessage(options.successMessage || `${label} が完了しました`);
    } catch (err: any) {
      setError(formatApiError(err, `${label} に失敗しました`));
    } finally {
      setBusy("");
    }
  };

  const confirmContext = () =>
    runAction("Step1 context confirm", async () => {
      await apiClient.post(`/orders/${orderId}/workflow-v2/context`, {
        facility_id: contextForm.facility_id,
        week_start: contextForm.week_start,
        week_end: contextForm.week_end,
      });
    });

  const applyCustomWeekRange = () => {
    const weekValue = deriveWeekValueFromCalendarRange(customWeekRangeStart, customWeekRangeEnd);
    if (!weekValue) {
      setError("例外範囲の日付が不正です。");
      return;
    }
    applyWeekValue(weekValue);
    setError("");
    setMessage("例外範囲を設定しました。");
  };

  const runOcr = () =>
    runAction(
      "Step1 OCR run",
      async () => {
        await apiClient.post(`/orders/${orderId}/workflow-v2/ocr-runs`, {
          stale_action: "retry",
        });
      },
      {
        successMessage: "Step1 OCR run が開始しました",
        nextStep: 2,
      },
    );

  const selectOcr = (ocrResultId: string) =>
    runAction("Step2 OCR select", async () => {
      await apiClient.post(`/orders/${orderId}/workflow-v2/ocr-results/${ocrResultId}/select`);
    });

  const deleteOcr = (ocrResultId: string) =>
    runAction("OCR result delete", async () => {
      await apiClient.delete(`/orders/${orderId}/workflow-v2/ocr-results/${ocrResultId}`);
    });

  const generateSheetFromSelectedOcr = () =>
    runAction("Step3 sheet source", async () => {
      const response = await apiClient.get<{ sheet?: Record<string, unknown> }>(`/orders/${orderId}/workflow-v2/sheet-source`);
      const normalized = normalizeSheetPayload(response.data.sheet);
      if (!normalized) {
        throw new Error("選択OCRからシートを生成できませんでした");
      }
      setSheetPayload(normalized);
      setSheetJson(formatJson(normalized));
    });

  const updateSheetCell = (rowIndex: number, colIndex: number, value: string) => {
    setSheetPayload((current) => {
      if (!current) return current;
      const rows = current.rows.map((row, idx) => (
        idx === rowIndex ? row.map((cell, cellIdx) => (cellIdx === colIndex ? value : cell)) : row
      ));
      const nextSheet = { ...current, rows };
      setSheetJson(formatJson(nextSheet));
      return nextSheet;
    });
  };

  const saveSheet = () =>
    runAction("Step3 sheet save", async () => {
      const parsed = sheetPayload || normalizeSheetPayload(JSON.parse(sheetJson));
      if (!parsed) {
        throw new Error("保存できるシートがありません");
      }
      await apiClient.put(`/orders/${orderId}/workflow-v2/sheet`, {
        sheet: parsed,
        edited_by: "operator",
      });
    });

  const runBagging = () =>
    runAction("Step4 bagging", async () => {
      await apiClient.post(`/orders/${orderId}/workflow-v2/bagging`);
    });

  const confirmBagging = () =>
    runAction("Step4 bagging confirm", async () => {
      await apiClient.post(`/orders/${orderId}/workflow-v2/bagging/confirm`);
    });

  const prepareOutputReview = () =>
    runAction("Step5 output review", async () => {
      await apiClient.post(`/orders/${orderId}/workflow-v2/outputs/review`);
    });

  const finalConfirm = () =>
    runAction("Step5 final confirm", async () => {
      await apiClient.post(`/orders/${orderId}/workflow-v2/confirm`, {
        confirmed_by: "operator",
      });
    });

  return (
    <main className="page workflow-v2-page">
      <header className="hero">
        <div>
          <p className="eyebrow">Workflow V2</p>
          <h1>注文処理 v2</h1>
          <p className="subtle">DB workflow state と artifact lineage だけで step を進めます。</p>
        </div>
        <TopNav />
      </header>

      <section className="panel state-panel">
        <div>
          <p className="eyebrow">Current State</p>
          <h2>{stateLabel(workflow?.state)}</h2>
          <p className="subtle">{workflow?.headline || "workflow-v2 を読み込み中です。"}</p>
        </div>
        <div className="state-actions">
          <button className="btn ghost" type="button" onClick={() => void refreshAll()} disabled={Boolean(busy)}>
            再読込
          </button>
          {orderId ? (
            <>
              <Link className="ghost-link" href={`/orders/${orderId}/inspection-v2`}>
                確認専用ページ
              </Link>
              <Link className="ghost-link" href="/orders">
                注文一覧へ戻る
              </Link>
            </>
          ) : null}
        </div>
      </section>

      {message ? <div className="notice success">{message}</div> : null}
      {error ? <div className="notice error">{error}</div> : null}

      <section className="panel order-info-panel">
        <div className="panel-header">
          <div>
            <p className="step-tag">Order Info</p>
            <h2>注文情報</h2>
            <p className="subtle">前工程と照合するための基本情報です。</p>
          </div>
        </div>
        <div className="summary-grid summary-grid--compact">
          <div className="summary-primary-card">
            <span className="field-label">注文ID</span>
            <p className="summary-value">{orderId || "-"}</p>
          </div>
          <div className="summary-primary-card">
            <span className="field-label">処理状態</span>
            <p className="summary-value">{stateLabel(workflow?.state)}</p>
          </div>
          <div className="summary-primary-card">
            <span className="field-label">施設</span>
            <p className="summary-value">{workflow?.facility_id || "未設定"}</p>
          </div>
          <div className="summary-primary-card">
            <span className="field-label">週次</span>
            <p className="summary-value">
              {formatWeekLabel(weekValueFromRange(workflow?.week_start, workflow?.week_end)) || "未設定"}
            </p>
          </div>
          <div className="summary-primary-card">
            <span className="field-label">テンプレート</span>
            <p className="summary-value">{workflow?.template_id || "施設設定から自動解決"}</p>
          </div>
          <div className="summary-primary-card">
            <span className="field-label">OCR結果</span>
            <p className="summary-value">{ocrResults.length}件</p>
          </div>
          <div className="summary-primary-card">
            <span className="field-label">OCRジョブ</span>
            <p className="summary-value">{workflow?.ocr_job?.ocr_job_id || "-"}</p>
          </div>
          <div className="summary-primary-card">
            <span className="field-label">OCR処理時間</span>
            <p className="summary-value">{formatElapsedSeconds(workflow?.ocr_job?.elapsed_seconds)}</p>
          </div>
          <div className="summary-primary-card">
            <span className="field-label">OCR開始/更新</span>
            <p className="summary-value">
              {formatDateTime(workflow?.ocr_job?.created_at)} / {formatDateTime(workflow?.ocr_job?.updated_at)}
            </p>
          </div>
        </div>
      </section>

      <nav className="step-nav" aria-label="workflow steps">
        {stepLabels.map((item) => (
          <button
            key={item.step}
            className={`step-nav-btn ${visibleStep === item.step ? "active" : ""}`}
            type="button"
            onClick={() => setVisibleStep(item.step)}
          >
            <span>Step{item.step}</span>
            <strong>{item.label}</strong>
          </button>
        ))}
      </nav>

      <section className="step-page">
        {visibleStep === 1 ? (
        <section className="panel">
          <p className="step-tag">Step1</p>
          <header className="panel-header">
            <div>
              <h2>注文書 (FAX PDF)</h2>
              <p className="subtle">原本PDFを確認し、施設と週設定を完了してください。</p>
            </div>
            {pdfUrl ? (
              <a className="ghost-link" href={pdfUrl} target="_blank" rel="noreferrer">
                原本を開く
              </a>
            ) : (
              <span className="subtle">{pdfError || "PDFを読み込み中..."}</span>
            )}
          </header>
          <div className="step1-facility-block">
            <div className="summary-grid summary-grid--compact">
              <div className="summary-primary-card">
                <span className="field-label">注文ID</span>
                <p className="summary-value">{orderId || "-"}</p>
              </div>
              <div className="summary-primary-card">
                <span className="field-label">現在の施設</span>
                <p className="summary-value">{workflow?.facility_id || "未設定"}</p>
              </div>
              <div className="summary-primary-card">
                <span className="field-label">現在の週</span>
                <p className="summary-value">
                  {formatWeekLabel(weekValueFromRange(workflow?.week_start, workflow?.week_end)) || "未設定"}
                </p>
              </div>
              <div className="summary-primary-card">
                <span className="field-label">施設テンプレート</span>
                <p className="summary-value">{workflow?.template_id || "施設設定から自動解決"}</p>
              </div>
            </div>
            <div className="summary-actions">
              <label className="field">
                <span className="field-label">施設 (Step1 必須)</span>
                <select
                  className="input"
                  value={contextForm.facility_id}
                  onChange={(event) => setContextForm((current) => ({ ...current, facility_id: event.target.value }))}
                  disabled={facilityOptionsLoading || Boolean(busy)}
                >
                  <option value="">施設を選択</option>
                  {contextForm.facility_id && !facilityOptions.some((option) => option.id === contextForm.facility_id) ? (
                    <option value={contextForm.facility_id}>{contextForm.facility_id} (未登録)</option>
                  ) : null}
                  {facilityOptions.map((option) => (
                    <option key={option.id} value={option.id}>
                      {formatFacilityLabel(option)}
                    </option>
                  ))}
                </select>
                {facilityOptionsError ? (
                  <span className="subtle">{facilityOptionsError}</span>
                ) : facilityOptionsLoading ? (
                  <span className="subtle">施設一覧を取得中...</span>
                ) : null}
              </label>
              <label className="field">
                <span className="field-label">週 (Step1 必須)</span>
                <select
                  className="input"
                  value={weekDraft}
                  onChange={(event) => applyWeekValue(event.target.value)}
                  disabled={weekOptionsLoading || Boolean(busy)}
                >
                  <option value="">週を選択</option>
                  {isConcreteWeekValue(weekDraft) && !weekOptions.some((option) => option.week_id === weekDraft) ? (
                    <option value={weekDraft}>{formatWeekLabel(weekDraft) || weekDraft} (現在値)</option>
                  ) : null}
                  {weekOptions.map((option) => (
                    <option key={option.week_id} value={option.week_id}>
                      {option.label}
                    </option>
                  ))}
                </select>
                {weekOptionsError ? (
                  <span className="subtle">{weekOptionsError}</span>
                ) : weekOptionsLoading ? (
                  <span className="subtle">週候補を取得中...</span>
                ) : (
                  <span className="subtle">日曜から土曜の固定週を表示します。</span>
                )}
                <div className="step1-week-range">
                  <span className="subtle">例外時だけ範囲指定します。</span>
                  <div className="summary-actions">
                    <input
                      className="input"
                      type="date"
                      value={customWeekRangeStart}
                      onChange={(event) => setCustomWeekRangeStart(event.target.value)}
                      disabled={Boolean(busy)}
                    />
                    <input
                      className="input"
                      type="date"
                      value={customWeekRangeEnd}
                      onChange={(event) => setCustomWeekRangeEnd(event.target.value)}
                      disabled={Boolean(busy)}
                    />
                    <button type="button" className="btn secondary" onClick={applyCustomWeekRange} disabled={Boolean(busy)}>
                      例外範囲を設定
                    </button>
                  </div>
                  {selectedWeekValue ? (
                    <span className="subtle">設定予定: {formatWeekLabel(selectedWeekValue) || selectedWeekValue}</span>
                  ) : null}
                </div>
              </label>
              <button className="btn primary" type="button" onClick={confirmContext} disabled={Boolean(busy || !contextReady)}>
                {contextReady ? "設定を保存" : "施設と週を選択"}
              </button>
              <button
                className="btn"
                type="button"
                onClick={runOcr}
                disabled={Boolean(busy || !workflow?.facility_id || !workflow?.week_start || !workflow?.week_end)}
              >
                OCRを実行
              </button>
            </div>
            {!workflow?.facility_id || !workflow?.week_start || !workflow?.week_end ? (
              <div className="warning-banner">
                {!workflow?.facility_id ? <p>この注文は施設が未設定です。OCR実行前に施設設定が必要です。</p> : null}
                {!workflow?.week_start || !workflow?.week_end ? <p>この注文は週が未設定です。OCR実行前に週設定が必要です。</p> : null}
              </div>
            ) : null}
          </div>
          <div className="pdf-preview-panel">
            <div className="pdf-preview-header">
              <div>
                <h3>原本PDF</h3>
                <p className="subtle">施設と週次の確認用です。</p>
              </div>
            </div>
            {pdfUrl ? (
              <iframe title="workflow-v2-original-pdf" src={pdfUrl} className="pdf-frame pdf-frame-wide" />
            ) : (
              <div className="pdf-placeholder">{pdfError || "PDFを読み込み中..."}</div>
            )}
          </div>
        </section>
        ) : null}

        {visibleStep === 2 ? (
        <section className="panel">
          <p className="step-tag">Step2</p>
          <h2>正解 OCR を一つ選ぶ</h2>
          <p className="subtle">選択変更または削除時は、派生 sheet / bagging / output / confirmed snapshot を無効化します。</p>
          <div className="ocr-result-list">
            {ocrResults.length ? (
              ocrResults.map((item) => (
                <div key={item.ocr_result_id} className={`ocr-card ${item === selectedOcr ? "selected" : ""}`}>
                  <div className="ocr-card-body">
                    <div>
                      <strong>{item.ocr_result_id}</strong>
                      <p className="subtle">
                        {item.status || "unknown"} / {item.source || "-"} / {formatDateTime(item.created_at)}
                      </p>
                      <p className="digest">{item.artifact_digest || ""}</p>
                    </div>
                    <div className="ocr-overlay-preview-card">
                      <div className="preview-header">
                        <div>
                          <span className="field-label">OCR Overlay</span>
                          <p className="subtle">このOCR結果に紐づくoverlay成果物です。</p>
                        </div>
                        {item.overlay_url ? (
                          <a className="ghost-link" href={item.overlay_url} target="_blank" rel="noreferrer">
                            別タブで開く
                          </a>
                        ) : null}
                      </div>
                      {item.overlay_url ? (
                        <img className="ocr-overlay-preview-image" src={item.overlay_url} alt={`${item.ocr_result_id} overlay`} />
                      ) : (
                        <div className="preview-placeholder">{item.overlay_message || "overlay成果物がありません。"}</div>
                      )}
                    </div>
                  </div>
                  <div className="row-actions">
                    <button className="btn" type="button" onClick={() => selectOcr(item.ocr_result_id)} disabled={Boolean(busy)}>
                      正解にする
                    </button>
                    <button className="btn danger" type="button" onClick={() => deleteOcr(item.ocr_result_id)} disabled={Boolean(busy)}>
                      完全削除
                    </button>
                  </div>
                </div>
              ))
            ) : (
              <p className="subtle">OCR result はまだありません。Step1 から OCR を実行してください。</p>
            )}
          </div>
        </section>
        ) : null}

        {visibleStep === 3 ? (
        <section className="panel">
          <p className="step-tag">Step3</p>
          <h2>選択 OCR からシート作成 / 編集 / 保存</h2>
          <div className="row-actions">
            <button
              className="btn"
              type="button"
              onClick={generateSheetFromSelectedOcr}
              disabled={Boolean(busy || !workflow?.selected_ocr_result_id)}
            >
              選択OCRからシート生成
            </button>
            <button className="btn primary" type="button" onClick={saveSheet} disabled={Boolean(busy || !workflow?.selected_ocr_result_id || !sheetPayload)}>
              シートを保存
            </button>
          </div>
          {sheetPayload ? (
            <div className="sheet-table-wrap">
              <table className="sheet-table">
                <thead>
                  <tr>
                    <th>#</th>
                    {sheetPayload.header.map((label, colIdx) => (
                      <th key={`${sheetPayload.fields[colIdx] || "col"}-${colIdx}`}>{label || sheetPayload.fields[colIdx]}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {sheetPayload.rows.map((row, rowIdx) => (
                    <tr key={sheetPayload.row_ids?.[rowIdx] || `row-${rowIdx}`}>
                      <th>{rowIdx + 1}</th>
                      {sheetPayload.fields.map((field, colIdx) => (
                        <td key={`${field}-${colIdx}`}>
                          <input
                            value={row[colIdx] || ""}
                            onChange={(event) => updateSheetCell(rowIdx, colIdx, event.target.value)}
                          />
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="subtle">Step2で正解OCRを選択してから、選択OCRだけを使ってシートを生成してください。</p>
          )}
          <details className="json-details">
            <summary>保存予定JSONを確認</summary>
            <textarea
              value={sheetJson}
              onChange={(event) => {
                const nextJson = event.target.value;
                setSheetJson(nextJson);
                try {
                  setSheetPayload(normalizeSheetPayload(JSON.parse(nextJson)));
                } catch {
                  setSheetPayload(null);
                }
              }}
              spellCheck={false}
            />
          </details>
        </section>
        ) : null}

        {visibleStep === 4 ? (
        <section className="panel">
          <p className="step-tag">Step4</p>
          <h2>保存済みシートから袋分け</h2>
          <p className="subtle">この step は saved_sheet_id だけを入力にします。</p>
          <div className="row-actions">
            <button className="btn primary" type="button" onClick={runBagging} disabled={Boolean(busy || !workflow?.saved_sheet_id)}>
              袋分けを計算
            </button>
            <button className="btn" type="button" onClick={confirmBagging} disabled={Boolean(busy || !workflow?.bagging_result_id)}>
              袋分けを確認
            </button>
          </div>
          <pre>{formatJson(inspection?.bagging_result || null)}</pre>
        </section>
        ) : null}

        {visibleStep === 5 ? (
        <section className="panel">
          <p className="step-tag">Step5</p>
          <h2>出力確認 / 確定</h2>
          <div className="row-actions">
            <button className="btn" type="button" onClick={prepareOutputReview} disabled={Boolean(busy || !workflow?.bagging_result_id)}>
              出力確認を作成
            </button>
            <button className="btn primary" type="button" onClick={finalConfirm} disabled={Boolean(busy || !workflow?.output_bundle_id)}>
              確定
            </button>
          </div>
          <pre>{formatJson(inspection?.output_bundle || null)}</pre>
        </section>
        ) : null}
      </section>

      <section className="panel">
        <p className="step-tag">Read Only Inspection</p>
        <h2>状態と lineage</h2>
        <pre>{formatJson(inspection || workflow)}</pre>
      </section>

      <style jsx>{`
        @import url("https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;700;800&family=Noto+Sans+JP:wght@400;600;700;800&display=swap");

        :global(body) {
          background: radial-gradient(circle at top left, #f8f4ea, #f4f7f6 40%, #eef1f0 100%);
          color: #1f2a2a;
          font-family: "Manrope", "Noto Sans JP", sans-serif;
        }

        :global(*) {
          box-sizing: border-box;
        }

        .workflow-v2-page {
          background:
            radial-gradient(circle at top left, rgba(62, 110, 89, 0.14), transparent 30%),
            linear-gradient(180deg, #faf8f1 0%, #f1efe6 100%);
          min-height: 100vh;
        }
        .hero {
          display: flex;
          justify-content: space-between;
          align-items: flex-start;
          gap: 24px;
          padding: 32px 36px 18px;
        }
        .eyebrow {
          color: #6f6a5a;
          font-size: 12px;
          font-weight: 700;
          letter-spacing: 0.08em;
          margin: 0 0 8px;
          text-transform: uppercase;
        }
        h1,
        h2 {
          color: #1c2822;
          margin: 0;
        }
        .subtle {
          color: #687269;
          margin: 8px 0 0;
        }
        .panel {
          background: #ffffff;
          border: 1px solid rgba(25, 32, 30, 0.08);
          border-radius: 18px;
          box-shadow: 0 12px 26px rgba(27, 35, 33, 0.06);
          margin: 16px 36px;
          padding: 22px;
        }
        .panel-header {
          align-items: center;
          display: flex;
          justify-content: space-between;
          gap: 16px;
          margin-bottom: 12px;
        }
        .panel-header > div {
          display: flex;
          flex-direction: column;
          gap: 4px;
        }
        .state-panel {
          align-items: center;
          display: flex;
          justify-content: space-between;
        }
        .state-actions,
        .row-actions {
          display: flex;
          flex-wrap: wrap;
          gap: 10px;
        }
        .step-page {
          display: block;
        }
        .step-nav {
          display: grid;
          gap: 10px;
          grid-template-columns: repeat(5, minmax(0, 1fr));
          margin: 16px 36px;
        }
        .step-nav-btn {
          background: rgba(255, 255, 255, 0.74);
          border: 1px solid rgba(54, 82, 68, 0.14);
          border-radius: 16px;
          color: #526258;
          cursor: pointer;
          display: grid;
          gap: 4px;
          min-height: 68px;
          padding: 12px 14px;
          text-align: left;
        }
        .step-nav-btn span {
          color: #a15f2d;
          font-size: 11px;
          font-weight: 800;
          letter-spacing: 0.08em;
          text-transform: uppercase;
        }
        .step-nav-btn strong {
          color: #1c2822;
          font-size: 14px;
        }
        .step-nav-btn.active {
          background: #1c2822;
          border-color: #1c2822;
          box-shadow: 0 12px 28px rgba(28, 40, 34, 0.16);
        }
        .step-nav-btn.active span,
        .step-nav-btn.active strong {
          color: #fffdf7;
        }
        .step-tag {
          color: #a15f2d;
          font-size: 12px;
          font-weight: 800;
          margin: 0 0 8px;
        }
        .form-grid {
          display: grid;
          gap: 12px;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          margin: 18px 0;
        }
        .summary-grid {
          display: grid;
          gap: 12px;
          grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
          align-items: end;
        }
        .summary-grid--compact {
          grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
          align-items: stretch;
        }
        .summary-primary-card {
          background: #f8fbfa;
          border: 1px solid rgba(25, 32, 30, 0.08);
          border-radius: 12px;
          padding: 10px 12px;
        }
        .summary-value {
          font-weight: 700;
          margin: 4px 0 0;
        }
        .summary-actions {
          align-items: flex-end;
          display: flex;
          flex-wrap: wrap;
          gap: 12px;
          margin-top: 12px;
        }
        .summary-actions .field {
          flex: 1;
          min-width: 240px;
        }
        .step1-facility-block {
          background: #f8fbfa;
          border: 1px solid rgba(25, 32, 30, 0.1);
          border-radius: 12px;
          margin: 14px 0 16px;
          padding: 12px;
        }
        .step1-week-range {
          display: flex;
          flex-direction: column;
          gap: 8px;
          margin-top: 10px;
        }
        .field {
          display: flex;
          flex-direction: column;
          gap: 6px;
        }
        .field-label {
          color: #5f7b74;
          font-size: 12px;
          font-weight: 800;
          letter-spacing: 0.06em;
          text-transform: uppercase;
        }
        .warning-banner {
          background: #fff8e6;
          border: 1px solid #ebd6a7;
          border-radius: 12px;
          color: #775316;
          font-size: 13px;
          margin-top: 12px;
          padding: 10px 12px;
        }
        .warning-banner p {
          margin: 0;
        }
        .warning-banner p + p {
          margin-top: 6px;
        }
        .pdf-preview-panel {
          border: 1px solid #d7d1c0;
          border-radius: 16px;
          margin: 18px 0;
          overflow: hidden;
          background: #fffdf7;
        }
        .pdf-preview-header {
          align-items: center;
          border-bottom: 1px solid #e5dece;
          display: flex;
          justify-content: space-between;
          gap: 16px;
          padding: 14px 16px;
        }
        .pdf-preview-header h3 {
          color: #1c2822;
          margin: 0;
        }
        .pdf-frame {
          background: #fff;
          border: 1px solid #ddd;
          border-radius: 12px;
          display: block;
          height: 520px;
          width: 100%;
        }
        .pdf-frame-wide {
          min-height: 640px;
        }
        .pdf-placeholder {
          align-items: center;
          color: #687269;
          background: #f9f7f2;
          display: flex;
          font-weight: 800;
          height: 420px;
          justify-content: center;
        }
        label {
          color: #455248;
          display: grid;
          font-size: 13px;
          font-weight: 700;
          gap: 6px;
        }
        input,
        select,
        textarea {
          background: #fffdf7;
          border: 1px solid #d7d1c0;
          border-radius: 12px;
          color: #1c2822;
          font: inherit;
          padding: 10px 12px;
        }
        textarea {
          font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
          min-height: 260px;
          width: 100%;
        }
        .sheet-table-wrap {
          border: 1px solid #d7d1c0;
          border-radius: 14px;
          margin-top: 16px;
          max-height: 520px;
          overflow: auto;
        }
        .sheet-table {
          border-collapse: separate;
          border-spacing: 0;
          min-width: 100%;
          width: max-content;
        }
        .sheet-table th,
        .sheet-table td {
          border-bottom: 1px solid #e5dece;
          border-right: 1px solid #e5dece;
          padding: 0;
        }
        .sheet-table th {
          background: #f4eddd;
          color: #405045;
          font-size: 12px;
          min-width: 72px;
          padding: 8px 10px;
          position: sticky;
          top: 0;
          z-index: 1;
        }
        .sheet-table th:first-child {
          left: 0;
          min-width: 42px;
          position: sticky;
          z-index: 2;
        }
        .sheet-table tbody th {
          top: auto;
        }
        .sheet-table td input {
          background: #fffdf7;
          border: 0;
          border-radius: 0;
          min-width: 92px;
          padding: 8px 10px;
        }
        .json-details {
          margin-top: 16px;
        }
        .json-details summary {
          color: #687269;
          cursor: pointer;
          font-weight: 800;
          margin-bottom: 10px;
        }
        .btn,
        .ghost-link {
          align-items: center;
          background: #f0eadc;
          border: 0;
          border-radius: 999px;
          color: #1c2822;
          cursor: pointer;
          display: inline-flex;
          font-weight: 800;
          justify-content: center;
          min-height: 40px;
          padding: 0 16px;
          text-decoration: none;
        }
        .btn.primary {
          background: #1c2822;
          color: #fffdf7;
        }
        .btn.ghost {
          background: #ebe5d5;
        }
        .btn.danger {
          background: #f5d5cb;
          color: #8a2c18;
        }
        .btn:disabled {
          cursor: not-allowed;
          opacity: 0.48;
        }
        .notice {
          border-radius: 14px;
          font-weight: 700;
          margin: 12px 36px;
          padding: 12px 16px;
        }
        .notice.success {
          background: #e7f4e8;
          color: #24552b;
        }
        .notice.error {
          background: #f8dfd8;
          color: #8a2c18;
        }
        .ocr-result-list {
          display: grid;
          gap: 12px;
          margin-top: 16px;
        }
        .ocr-card {
          align-items: flex-start;
          background: #fffdf7;
          border: 1px solid #ddd5c2;
          border-radius: 14px;
          display: flex;
          gap: 16px;
          justify-content: space-between;
          padding: 14px;
        }
        .ocr-card.selected {
          border-color: #2f7d52;
          box-shadow: inset 4px 0 0 #2f7d52;
        }
        .ocr-card-body {
          display: grid;
          gap: 12px;
          min-width: 0;
          flex: 1;
        }
        .ocr-overlay-preview-card {
          background: #ffffff;
          border: 1px solid rgba(25, 32, 30, 0.1);
          border-radius: 14px;
          overflow: hidden;
        }
        .preview-header {
          align-items: center;
          border-bottom: 1px solid #e5dece;
          display: flex;
          justify-content: space-between;
          gap: 12px;
          padding: 10px 12px;
        }
        .ocr-overlay-preview-image {
          background: #ffffff;
          display: block;
          max-height: 520px;
          object-fit: contain;
          width: 100%;
        }
        .digest {
          color: #8a826e;
          font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
          font-size: 11px;
          margin: 6px 0 0;
          word-break: break-all;
        }
        pre {
          background: #162019;
          border-radius: 14px;
          color: #e9f1e7;
          font-size: 12px;
          max-height: 360px;
          overflow: auto;
          padding: 14px;
        }
        @media (max-width: 980px) {
          .hero,
          .state-panel {
            display: block;
          }
          .step-nav,
          .form-grid {
            grid-template-columns: 1fr;
          }
          .panel,
          .notice {
            margin-left: 16px;
            margin-right: 16px;
          }
        }
      `}</style>
    </main>
  );
}
