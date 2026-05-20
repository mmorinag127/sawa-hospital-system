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
          <p className="eyebrow">週別出力</p>
          <h1>週別重量表</h1>
          <p className="subtle">指定日の週に含まれる注文から、週別の重量表Excelを出力します。</p>
        </div>
        <TopNav />
      </header>

      <section className="panel">
        <header className="panel-header">
          <h2>この画面で見ること</h2>
        </header>
        <div className="guide-grid">
          <article className="guide-card">
            <p className="guide-title">週単位で出力</p>
            <p className="guide-text">選択した日付を含む週の重量表Excelを作成します。</p>
          </article>
          <article className="guide-card">
            <p className="guide-title">日別出力と連動</p>
            <p className="guide-text">日別出力画面のフィルタから同じ日付とステータスを引き継げます。</p>
          </article>
          <article className="guide-card">
            <p className="guide-title">空週も出力</p>
            <p className="guide-text">対象データがない週でも、確認用の空フォーマットを出力します。</p>
          </article>
        </div>
      </section>

      <section className="panel">
        <header className="panel-header">
          <div>
            <h2>出力条件</h2>
            <p className="subtle">対象週: {weekLabel || "-"}</p>
          </div>
          <Link href="/daily-delivery-notes" className="btn ghost">
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
        .page {
          min-height: 100vh;
          padding: 48px 6vw 80px;
        }
        :global(body) {
          background: radial-gradient(circle at top left, #f8f4ea, #f4f7f6 40%, #eef1f0 100%);
          color: #1f2a2a;
          font-family: "Manrope", "Noto Sans JP", sans-serif;
        }
        :global(*) {
          box-sizing: border-box;
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
        }
        h1,
        h2 {
          margin: 0;
        }
        h1 {
          font-size: clamp(26px, 4vw, 36px);
          margin-bottom: 12px;
        }
        h2 {
          font-size: 18px;
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
          gap: 16px;
          align-items: center;
          margin-bottom: 16px;
        }
        .guide-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
          gap: 14px;
        }
        .guide-card {
          border-radius: 16px;
          border: 1px solid rgba(25, 32, 30, 0.1);
          background: #fcfbf7;
          padding: 16px;
        }
        .guide-title {
          margin: 0 0 8px;
          font-weight: 800;
        }
        .guide-text {
          margin: 0;
          color: #51615c;
          line-height: 1.6;
        }
        .filters {
          display: grid;
          gap: 16px;
          grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
          align-items: center;
        }
        .field {
          display: flex;
          flex-direction: column;
          gap: 6px;
          font-size: 13px;
        }
        .field-label {
          color: #5f7b74;
          font-size: 12px;
          font-weight: 800;
        }
        .input {
          border: 1px solid rgba(25, 32, 30, 0.14);
          border-radius: 10px;
          font-size: 14px;
          min-height: 40px;
          padding: 8px 10px;
        }
        .btn {
          min-height: 40px;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          border-radius: 999px;
          border: none;
          padding: 0 14px;
          font-size: 14px;
          font-weight: 700;
          text-decoration: none;
          cursor: pointer;
        }
        .btn.primary {
          background: #1f2a2a;
          color: #f7f2e7;
        }
        .btn.ghost {
          background: #e6ebe9;
          color: #1f2a2a;
        }
        .btn:disabled {
          opacity: 0.55;
          cursor: not-allowed;
        }
        .helper-text {
          margin-top: 12px;
        }
        .message {
          margin: 0 0 20px;
          padding: 12px 14px;
          border-radius: 12px;
          background: #eff7f4;
          border: 1px solid rgba(31, 42, 42, 0.1);
          color: #1f2a2a;
        }
        @media (max-width: 720px) {
          .page {
            padding: 28px 16px 48px;
          }
          .hero {
            display: grid;
          }
          .panel-header {
            align-items: flex-start;
            flex-direction: column;
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
