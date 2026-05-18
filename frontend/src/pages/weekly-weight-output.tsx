import { useRouter } from "next/router";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import TopNav from "../components/TopNav";
import { apiClient } from "../services/apiClient";

const todayIso = () => new Date().toISOString().slice(0, 10);

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

export default function WeeklyWeightOutputPage() {
  const router = useRouter();
  const [date, setDate] = useState(todayIso());
  const [status, setStatus] = useState("");
  const [message, setMessage] = useState("");
  const [downloading, setDownloading] = useState(false);

  useEffect(() => {
    if (!router.isReady) return;
    const queryDate = queryValue(router.query.date);
    const queryStatus = queryValue(router.query.status);
    if (queryDate) setDate(queryDate);
    if (queryStatus) setStatus(queryStatus);
  }, [router.isReady, router.query.date, router.query.status]);

  const weekLabel = useMemo(() => {
    const parsed = new Date(`${date}T00:00:00`);
    if (Number.isNaN(parsed.getTime())) return "";
    const day = parsed.getDay();
    const offset = day === 0 ? -6 : 1 - day;
    const monday = new Date(parsed);
    monday.setDate(parsed.getDate() + offset);
    const sunday = new Date(monday);
    sunday.setDate(monday.getDate() + 6);
    return `${monday.toISOString().slice(0, 10)} から ${sunday.toISOString().slice(0, 10)}`;
  }, [date]);

  const downloadWeeklyWeight = async () => {
    if (!date) {
      setMessage("日付を指定してください。");
      return;
    }
    setDownloading(true);
    setMessage("週別重量表Excelを作成中です。");
    try {
      const res = await apiClient.get("/outputs/weekly-weight", {
        params: { date, status: status || undefined },
        responseType: "blob",
        timeout: 0,
      });
      const contentDisposition = res.headers?.["content-disposition"] || res.headers?.["Content-Disposition"];
      const filename = extractFilename(contentDisposition) || `weekly_weight_${date}.xlsx`;
      const blob = res.data instanceof Blob ? res.data : new Blob([res.data]);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      setMessage("週別重量表Excelをダウンロードしました。");
    } catch (err: any) {
      const detail = await extractErrorDetail(err);
      setMessage(detail ? `週別重量表Excelの作成に失敗しました: ${detail}` : "週別重量表Excelの作成に失敗しました。");
    } finally {
      setDownloading(false);
    }
  };

  return (
    <main className="page">
      <TopNav />

      <section className="panel hero">
        <div>
          <p className="eyebrow">週別出力</p>
          <h1>週別重量表</h1>
          <p className="subtle">指定日の週に含まれる注文から、週別の重量表Excelだけを出力します。</p>
        </div>
        <Link href="/daily-delivery-notes" className="btn ghost">
          日別出力へ
        </Link>
      </section>

      <section className="panel">
        <header className="panel-header">
          <div>
            <h2>出力条件</h2>
            <p className="subtle">対象週: {weekLabel || "-"}</p>
          </div>
        </header>
        <div className="filters">
          <label className="field">
            <span className="field-label">週に含まれる日付</span>
            <input className="input" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          </label>
          <label className="field">
            <span className="field-label">ステータス</span>
            <select className="input" value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">全て</option>
              <option value="未着">未着</option>
              <option value="要確認">要確認</option>
              <option value="確定">確定</option>
              <option value="エラー">エラー</option>
            </select>
          </label>
          <button className="btn primary" type="button" onClick={downloadWeeklyWeight} disabled={downloading}>
            {downloading ? "作成中..." : "週別重量表Excel"}
          </button>
        </div>
        <p className="subtle helper-text">
          日別のラベルExcel・納品書Excel・一括Excelには重量表を同梱しません。重量表が必要な場合はこのページから出力します。
        </p>
      </section>

      {message ? <p className="message">{message}</p> : null}

      <style jsx>{`
        .page {
          min-height: 100vh;
          background: #f5f7fb;
          color: #1f2937;
          padding: 24px;
        }
        .panel {
          background: #ffffff;
          border: 1px solid #d8dee9;
          border-radius: 8px;
          padding: 18px;
          margin-top: 16px;
        }
        .hero {
          display: flex;
          justify-content: space-between;
          gap: 16px;
          align-items: flex-start;
        }
        .eyebrow {
          margin: 0 0 6px;
          color: #4b5563;
          font-size: 13px;
          font-weight: 700;
        }
        h1,
        h2 {
          margin: 0;
        }
        .subtle {
          color: #667085;
          margin: 8px 0 0;
        }
        .panel-header {
          display: flex;
          justify-content: space-between;
          gap: 16px;
          align-items: flex-start;
          margin-bottom: 14px;
        }
        .filters {
          display: flex;
          flex-wrap: wrap;
          gap: 12px;
          align-items: flex-end;
        }
        .field {
          display: grid;
          gap: 6px;
        }
        .field-label {
          color: #344054;
          font-size: 13px;
          font-weight: 700;
        }
        .input {
          min-height: 40px;
          border: 1px solid #cbd5e1;
          border-radius: 6px;
          padding: 8px 10px;
          background: #ffffff;
          font-size: 14px;
        }
        .btn {
          min-height: 40px;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          border-radius: 6px;
          border: 1px solid #cbd5e1;
          padding: 8px 14px;
          font-size: 14px;
          font-weight: 700;
          text-decoration: none;
          cursor: pointer;
        }
        .btn.primary {
          background: #2563eb;
          color: #ffffff;
          border-color: #2563eb;
        }
        .btn.ghost {
          background: #ffffff;
          color: #1f2937;
        }
        .btn:disabled {
          opacity: 0.55;
          cursor: not-allowed;
        }
        .helper-text {
          margin-top: 12px;
        }
        .message {
          margin-top: 16px;
          padding: 12px 14px;
          border-radius: 8px;
          background: #eff6ff;
          border: 1px solid #bfdbfe;
          color: #1e40af;
        }
        @media (max-width: 720px) {
          .page {
            padding: 14px;
          }
          .hero {
            display: grid;
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
