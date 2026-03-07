import { useEffect, useMemo, useState } from "react";
import TopNav from "../components/TopNav";
import { apiClient } from "../services/apiClient";

type FacilityOption = {
  id: string;
  name: string;
};

type OrderFormPattern = {
  pattern_id: string;
  label?: string;
  description?: string;
};

const DEFAULT_ORDER_FORM_PATTERNS: OrderFormPattern[] = [
  { pattern_id: "PATTERN_A", label: "標準A" },
  { pattern_id: "PATTERN_B", label: "標準B" },
  { pattern_id: "PATTERN_C", label: "標準C" },
  { pattern_id: "PATTERN_D", label: "標準D" },
  { pattern_id: "PATTERN_E", label: "標準E" },
];

const extractFilename = (value?: string | null) => {
  if (!value) return "";
  const match = value.match(/filename\*=UTF-8''([^;]+)|filename=\"?([^\";]+)\"?/i);
  const rawName = match?.[1] || match?.[2] || "";
  if (!rawName) return "";
  try {
    return decodeURIComponent(rawName);
  } catch {
    return rawName;
  }
};

const currentMonth = () => {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
};

export default function OrderFormsPage() {
  const [facilities, setFacilities] = useState<FacilityOption[]>([]);
  const [patterns, setPatterns] = useState<OrderFormPattern[]>(DEFAULT_ORDER_FORM_PATTERNS);
  const [facilityId, setFacilityId] = useState("");
  const [monthId, setMonthId] = useState(currentMonth());
  const [patternId, setPatternId] = useState("");
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const [facRes, patternRes] = await Promise.all([
          apiClient.get("/facilities"),
          apiClient.get("/order-forms/patterns").catch(() => null),
        ]);
        if (cancelled) return;
        const facItems = Array.isArray(facRes.data?.facilities) ? facRes.data.facilities : [];
        const normalizedFacilities = facItems
          .map((item: any) => ({
            id: String(item?.id || "").trim(),
            name: String(item?.name || "").trim(),
          }))
          .filter((item: FacilityOption) => Boolean(item.id));
        setFacilities(normalizedFacilities);
        if (normalizedFacilities.length > 0) {
          setFacilityId(normalizedFacilities[0].id);
        }

        const rawPatterns = patternRes?.data?.patterns;
        if (Array.isArray(rawPatterns)) {
          const normalizedPatterns = rawPatterns
            .map((item: any) => ({
              pattern_id: String(item?.pattern_id || "").trim(),
              label:
                typeof item?.label === "string" && item.label.trim() ? item.label.trim() : undefined,
              description:
                typeof item?.description === "string" && item.description.trim()
                  ? item.description.trim()
                  : undefined,
            }))
            .filter((item: OrderFormPattern) => Boolean(item.pattern_id));
          setPatterns(normalizedPatterns.length ? normalizedPatterns : DEFAULT_ORDER_FORM_PATTERNS);
        } else {
          setPatterns(DEFAULT_ORDER_FORM_PATTERNS);
        }
      } catch {
        if (!cancelled) {
          setMessage("初期データの取得に失敗しました。");
        }
      }
    };
    load();
    return () => {
      cancelled = true;
    };
  }, []);

  const selectedPattern = useMemo(
    () => patterns.find((item) => item.pattern_id === patternId) || null,
    [patterns, patternId]
  );

  const generate = async () => {
    if (!facilityId) {
      setMessage("施設を選択してください。");
      return;
    }
    if (!monthId) {
      setMessage("対象月を選択してください。");
      return;
    }
    setLoading(true);
    setMessage("注文書を生成中です...");
    try {
      const params: Record<string, string> = {
        facility_id: facilityId,
        month_id: monthId,
      };
      if (patternId.trim()) {
        params.pattern_id = patternId.trim();
      }
      const res = await apiClient.post("/order-forms/generate", null, {
        params,
        responseType: "blob",
      });
      const contentDisposition = res.headers?.["content-disposition"] || res.headers?.["Content-Disposition"];
      const filename = extractFilename(contentDisposition) || "注文書_自動生成.xlsx";
      const blob = res.data instanceof Blob ? res.data : new Blob([res.data]);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 10000);
      setMessage("注文書を生成し、Excelをダウンロードしました。");
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setMessage(detail ? `生成に失敗しました: ${detail}` : "生成に失敗しました。");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="page">
      <header className="hero">
        <div>
          <p className="eyebrow">Order Form Builder</p>
          <h1>注文書生成</h1>
          <p className="subtle">施設・対象月・パターンを指定して注文書を自動生成します。</p>
        </div>
        <TopNav />
      </header>

      <section className="panel">
        <header className="panel-header">
          <h2>生成設定</h2>
        </header>
        <div className="filters">
          <label className="field">
            <span className="field-label">施設</span>
            <select className="input" value={facilityId} onChange={(e) => setFacilityId(e.target.value)}>
              {facilities.map((facility) => (
                <option key={facility.id} value={facility.id}>
                  {facility.name ? `${facility.name} (${facility.id})` : facility.id}
                </option>
              ))}
            </select>
          </label>

          <label className="field">
            <span className="field-label">対象月</span>
            <input
              className="input"
              type="month"
              value={monthId}
              onChange={(e) => setMonthId(e.target.value)}
            />
          </label>

          <label className="field">
            <span className="field-label">注文書パターン</span>
            <select className="input" value={patternId} onChange={(e) => setPatternId(e.target.value)}>
              <option value="">施設既定を使う</option>
              {patterns.map((pattern) => (
                <option key={pattern.pattern_id} value={pattern.pattern_id}>
                  {pattern.label ? `${pattern.label} (${pattern.pattern_id})` : pattern.pattern_id}
                </option>
              ))}
            </select>
          </label>
        </div>

        {selectedPattern?.description ? <p className="subtle">説明: {selectedPattern.description}</p> : null}

        <div className="actions">
          <button className="btn primary" onClick={generate} disabled={loading}>
            {loading ? "生成中..." : "注文書を生成してダウンロード"}
          </button>
        </div>
      </section>

      {message ? <p className="message">{message}</p> : null}

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

        .filters {
          display: grid;
          gap: 16px;
          grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
          margin-bottom: 12px;
        }

        .field {
          display: flex;
          flex-direction: column;
          gap: 6px;
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

        .actions {
          margin-top: 16px;
        }

        .btn {
          border: none;
          border-radius: 999px;
          padding: 10px 18px;
          background: #e6ebe9;
          color: #1f2a2a;
          font-weight: 600;
          cursor: pointer;
        }

        .btn.primary {
          background: #1f2a2a;
          color: #f7f2e7;
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
          max-width: 900px;
        }
      `}</style>
      <style jsx global>{`
        @import url("https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;700&family=Noto+Sans+JP:wght@400;600&display=swap");
      `}</style>
    </main>
  );
}
