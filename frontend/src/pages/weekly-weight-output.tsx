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
      <header className="hero">
        <div>
          <p className="eyebrow">Weekly Outputs</p>
          <h1>週別重量表</h1>
          <p className="subtle">指定日の週に含まれる注文から、週別の重量表Excelだけを出力します。</p>
        </div>
        <TopNav />
      </header>

      <section className="panel">
        <header className="panel-header">
          <div>
            <h2>出力条件</h2>
            <p className="subtle">対象週: {weekLabel || "-"}</p>
          </div>
          <Link href="/daily-delivery-notes" className="ghost-link">
            日別出力へ
          </Link>
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
        body {
          background: radial-gradient(circle at top left, #f8f4ea, #f4f7f6 40%, #eef1f0 100%);
          color: #1f2a2a;
          font-family: "Manrope", "Noto Sans JP", sans-serif;
        }
        * {
          box-sizing: border-box;
        }
        a {
          color: inherit;
          text-decoration: none;
        }
        .page {
          min-height: 100vh;
          padding: 48px 6vw 80px;
        }
        .panel {
          background: #ffffff;
          border-radius: 18px;
          padding: 20px;
          border: 1px solid rgba(25, 32, 30, 0.08);
          box-shadow: 0 12px 26px rgba(27, 35, 33, 0.06);
          margin-bottom: 20px;
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
          margin: 0 0 8px;
          font-weight: 700;
        }
        h1 {
          font-size: clamp(26px, 4vw, 36px);
          margin: 0 0 12px;
        }
        h2 {
          font-size: 18px;
          margin: 0;
        }
        .subtle {
          color: #51615c;
          margin: 0;
        }
        .panel-header {
          display: flex;
          justify-content: space-between;
          gap: 12px;
          align-items: flex-start;
          margin-bottom: 16px;
        }
        .ghost-link {
          color: #5f7b74;
          font-size: 13px;
          white-space: nowrap;
          font-weight: 700;
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
          color: #5f7b74;
          font-size: 13px;
          font-weight: 700;
        }
        .input {
          min-height: 40px;
          border: 1px solid rgba(31, 42, 42, 0.2);
          border-radius: 12px;
          padding: 8px 10px;
          background: #fbfaf7;
          font-size: 14px;
        }
        .btn {
          min-height: 40px;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          border-radius: 999px;
          border: 1px solid rgba(31, 42, 42, 0.12);
          padding: 8px 14px;
          font-size: 14px;
          font-weight: 700;
          text-decoration: none;
          cursor: pointer;
        }
        .btn.primary {
          background: #1f2a2a;
          color: #f7f2e7;
          border-color: #1f2a2a;
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
          border-radius: 14px;
          background: #eef3f1;
          border: 1px solid rgba(31, 42, 42, 0.12);
          color: #1f2a2a;
          font-weight: 700;
        }
        @media (max-width: 720px) {
          .page {
            padding: 32px 18px 56px;
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
