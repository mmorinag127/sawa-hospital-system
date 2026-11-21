import { useEffect, useState } from "react";
import { apiClient } from "../../services/apiClient";

function PreviewFile({ path, kind, page, hidden, setPageCount }: {
  path: string; kind: "pdf" | "overlay"; page?: number; hidden: boolean; setPageCount?: (count: number) => void;
}) {
  const [url, setUrl] = useState("");
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new window.AbortController();
    let objectUrl: string | undefined;
    apiClient.get<Blob>(path, { params: page ? { page } : undefined, responseType: "blob",
      signal: controller.signal, timeout: 65000 })
      .then(({ data, headers }) => {
        if (controller.signal.aborted) return;
        if (data.size === 0 || data.type !== "image/png") throw new Error("Unexpected preview format");
        if (setPageCount) {
          const count = Number(headers["x-fax-page-count"]);
          if (!Number.isSafeInteger(count) || count < (page ?? 1)) throw new Error("Unexpected page count");
          setPageCount(count);
        }
        objectUrl = URL.createObjectURL(data);
        setUrl(objectUrl);
      })
      .catch(() => { if (!controller.signal.aborted) setFailed(true); });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [attempt, page, path, setPageCount]);
  return <div className="fax-preview-file" hidden={hidden}>
    {url ? <img src={url} alt={kind === "pdf" ? `FAX原本PDF ${page}ページ` : "FAXのOCR重ね表示"} />
      : failed ? <div>
        <p role="alert">{kind === "pdf" ? "原本PDFを表示できません。" : "この確認案のOCR重ね表示を取得できません。"}</p>
        <button type="button" onClick={() => { setFailed(false); setAttempt(n => n + 1); }}>再読込</button>
      </div> : <p role="status">読み込み中…</p>}
  </div>;
}

export default function FaxSourcePreview({ path, hasEvidence }: {
  path: string;
  hasEvidence: boolean;
}) {
  const [mode, setMode] = useState<"pdf" | "overlay">("pdf");
  const [overlayOpened, setOverlayOpened] = useState(false);
  const [page, setPage] = useState(1);
  const [pageCount, setPageCount] = useState(1);
  return <section className="fax-preview" aria-label="FAX原本とOCR">
    <div className="fax-preview-modes" role="group" aria-label="FAXの表示切替">
      <button type="button" aria-pressed={mode === "pdf"} onClick={() => setMode("pdf")}>原本PDF</button>
      <button type="button" disabled={!hasEvidence} aria-pressed={mode === "overlay"}
        onClick={() => { setOverlayOpened(true); setMode("overlay"); }}>OCR重ね表示</button>
      {mode === "pdf" && pageCount > 1 && <div className="fax-preview-pagination">
        <button type="button" aria-label="前のページ" disabled={page === 1} onClick={() => setPage(n => n - 1)}>‹</button>
        <span>{page} / {pageCount}</span>
        <button type="button" aria-label="次のページ" disabled={page === pageCount} onClick={() => setPage(n => n + 1)}>›</button>
      </div>}
    </div>
    <PreviewFile key={page} path={path} kind="pdf" page={page} setPageCount={setPageCount} hidden={mode !== "pdf"} />
    {overlayOpened && hasEvidence && <PreviewFile path={path} kind="overlay" hidden={mode !== "overlay"} />}
  </section>;
}
