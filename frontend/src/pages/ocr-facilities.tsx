import { useEffect, useMemo, useState } from "react";
import TopNav from "../components/TopNav";
import { apiClient } from "../services/apiClient";

type FacilityEntry = {
  id: string;
  data: Record<string, unknown>;
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

export default function OcrFacilitiesPage() {
  const [facilities, setFacilities] = useState<FacilityEntry[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [facilityId, setFacilityId] = useState("");
  const [facilityText, setFacilityText] = useState("{}");
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);

  const selectedFacility = useMemo(
    () => facilities.find((item) => item.id === selectedId) || null,
    [facilities, selectedId]
  );

  const loadFacilities = async () => {
    setLoading(true);
    try {
      const res = await apiClient.get("/ocr/facilities");
      const items = Array.isArray(res.data.facilities) ? res.data.facilities : [];
      const normalized = items.map((entry: any) => ({
        id: String(entry.id || ""),
        data: entry.data || {},
      }));
      setFacilities(normalized);
      if (normalized.length > 0) {
        setSelectedId(normalized[0].id);
        setFacilityId(normalized[0].id);
        setFacilityText(prettyJson(normalized[0].data));
      } else {
        setSelectedId("");
        setFacilityId("");
        setFacilityText("{}");
      }
      setMessage("");
    } catch (err) {
      setMessage("Failed to load OCR facilities.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadFacilities();
  }, []);

  const selectFacility = (id: string) => {
    const entry = facilities.find((item) => item.id === id);
    if (!entry) return;
    setSelectedId(id);
    setFacilityId(id);
    setFacilityText(prettyJson(entry.data));
  };

  const createNewFacility = () => {
    const existing = new Set(facilities.map((item) => item.id));
    const id = generateFacilityId(existing);
    setSelectedId("");
    setFacilityId(id);
    setFacilityText(
      prettyJson({
        facility_id: id,
        facility_name: "",
        template_id: "",
        main_ocr_facility_prompt: "",
      })
    );
    setMessage("New OCR facility draft created.");
  };

  const saveFacility = async () => {
    if (!facilityId) {
      setMessage("Facility ID is required.");
      return;
    }
    const parsed = parseJson(facilityText);
    if (parsed.error) {
      setMessage(`JSON error: ${parsed.error}`);
      return;
    }
    if (!parsed.value || typeof parsed.value !== "object") {
      setMessage("Facility must be a JSON object.");
      return;
    }
    setLoading(true);
    try {
      await apiClient.put(`/ocr/facilities/${facilityId}`, {
        facility: parsed.value,
      });
      await loadFacilities();
      setSelectedId(facilityId);
      setMessage("Facility saved.");
    } catch (err) {
      setMessage("Failed to save facility.");
    } finally {
      setLoading(false);
    }
  };

  const deleteFacility = async () => {
    if (!selectedId) {
      setMessage("Select a facility to delete.");
      return;
    }
    setLoading(true);
    try {
      await apiClient.delete(`/ocr/facilities/${selectedId}`);
      await loadFacilities();
      setMessage("Facility deleted.");
    } catch (err) {
      setMessage("Failed to delete facility.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="page">
      <header className="hero">
        <div>
          <p className="eyebrow">OCR Facilities</p>
          <h1>OCR Facility Registry</h1>
          <p className="subtle">Map facility IDs to preferred templates and prompts.</p>
        </div>
        <TopNav />
      </header>

      <section className="panel">
        <header className="panel-header">
          <h2>Facilities</h2>
          <div className="actions">
            <button className="btn ghost" onClick={loadFacilities} disabled={loading}>
              Refresh
            </button>
            <button className="btn" onClick={createNewFacility} disabled={loading}>
              New Facility
            </button>
          </div>
        </header>
        <div className="facility-grid">
          <div className="list">
            {facilities.length === 0 && <p className="subtle">No OCR facilities found.</p>}
            {facilities.map((entry) => (
              <button
                key={entry.id}
                className={`list-item ${entry.id === selectedId ? "active" : ""}`}
                onClick={() => selectFacility(entry.id)}
              >
                <div>
                  <p className="list-title">{entry.id}</p>
                  <p className="list-meta">{String(entry.data.template_id || "-")}</p>
                </div>
                <span className="ghost-link">Edit</span>
              </button>
            ))}
          </div>
          <div className="editor">
            <label className="field">
              <span className="field-label">Facility ID</span>
              <input
                className="input"
                value={facilityId}
                onChange={(e) => setFacilityId(e.target.value)}
                placeholder="FAC000001"
              />
            </label>
            <label className="field">
              <span className="field-label">Facility JSON</span>
              <textarea
                className="textarea"
                value={facilityText}
                onChange={(e) => setFacilityText(e.target.value)}
                rows={18}
              />
            </label>
            <div className="actions">
              <button className="btn ghost" onClick={deleteFacility} disabled={loading}>
                Delete
              </button>
              <button className="btn primary" onClick={saveFacility} disabled={loading}>
                Save
              </button>
            </div>
          </div>
        </div>
      </section>

      {selectedFacility?.data?.main_ocr_facility_prompt && (
        <section className="panel">
          <header className="panel-header">
            <h2>Facility Prompt</h2>
          </header>
          <pre className="code-block">
            {String(selectedFacility.data.main_ocr_facility_prompt)}
          </pre>
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
          box-shadow: 0 20px 50px rgba(23, 30, 28, 0.08);
          margin-bottom: 24px;
        }

        .panel-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 16px;
        }

        .facility-grid {
          display: grid;
          grid-template-columns: minmax(220px, 1fr) minmax(320px, 2fr);
          gap: 20px;
        }

        .list {
          display: grid;
          gap: 12px;
        }

        .list-item {
          background: #f7f7f4;
          border: 1px solid rgba(24, 32, 30, 0.08);
          border-radius: 14px;
          padding: 14px 16px;
          display: flex;
          justify-content: space-between;
          align-items: center;
          text-align: left;
          cursor: pointer;
        }

        .list-item.active {
          border-color: rgba(55, 94, 86, 0.45);
          background: rgba(225, 236, 231, 0.65);
        }

        .list-title {
          margin: 0;
          font-weight: 600;
        }

        .list-meta {
          margin: 4px 0 0;
          font-size: 12px;
          color: #6a7c76;
        }

        .ghost-link {
          color: #1f2a2a;
          font-weight: 600;
          opacity: 0.6;
        }

        .editor {
          display: grid;
          gap: 14px;
        }

        .field {
          display: grid;
          gap: 8px;
        }

        .field-label {
          font-size: 12px;
          text-transform: uppercase;
          letter-spacing: 0.12em;
          color: #5f7b74;
        }

        .textarea,
        .input {
          width: 100%;
          border-radius: 12px;
          border: 1px solid rgba(28, 33, 31, 0.14);
          padding: 12px 14px;
          font-family: "JetBrains Mono", "SFMono-Regular", ui-monospace, monospace;
          background: #fbfaf7;
          color: #1f2a2a;
        }

        .actions {
          display: flex;
          justify-content: flex-end;
          gap: 12px;
        }

        .btn {
          border: none;
          border-radius: 999px;
          padding: 10px 18px;
          background: #dfe6e1;
          color: #1f2a2a;
          font-weight: 600;
          cursor: pointer;
        }

        .btn.primary {
          background: #1f2a2a;
          color: #f7f2e7;
        }

        .btn.ghost {
          background: transparent;
          border: 1px solid rgba(24, 32, 30, 0.2);
        }

        .code-block {
          background: #111413;
          color: #f4f0e6;
          padding: 12px;
          border-radius: 12px;
          font-size: 12px;
          overflow-x: auto;
        }

        .message {
          margin-top: 16px;
          color: #1f2a2a;
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
