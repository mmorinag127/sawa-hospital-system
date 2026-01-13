import { useEffect, useState } from "react";
import Link from "next/link";
import TopNav from "../../components/TopNav";
import { apiClient } from "../../services/apiClient";

type Facility = {
  id: string;
  name: string;
  areas: { id: string; name: string }[];
};

const parseJson = (text: string) => {
  try {
    return { value: JSON.parse(text), error: "" };
  } catch (err) {
    const message = err instanceof Error ? err.message : "Invalid JSON";
    return { value: null, error: message };
  }
};

export default function FacilitiesIndexPage() {
  const [facilities, setFacilities] = useState<Facility[]>([]);
  const [name, setName] = useState("");
  const [areasText, setAreasText] = useState("[]");
  const [message, setMessage] = useState("");

  const loadFacilities = async () => {
    const res = await apiClient.get("/facilities");
    setFacilities(res.data.facilities || []);
  };

  useEffect(() => {
    loadFacilities();
  }, []);

  const createFacility = async () => {
    if (!name.trim()) {
      setMessage("Name is required.");
      return;
    }
    const parsed = parseJson(areasText);
    if (parsed.error) {
      setMessage(`Areas JSON error: ${parsed.error}`);
      return;
    }
    if (!Array.isArray(parsed.value)) {
      setMessage("Areas JSON must be an array.");
      return;
    }
    const res = await apiClient.post("/facilities", {
      name: name.trim(),
      areas: parsed.value,
    });
    setMessage("Facility created.");
    setName("");
    setAreasText("[]");
    await loadFacilities();
    const createdId = res.data?.id;
    if (createdId) {
      window.location.href = `/facilities/${createdId}`;
    }
  };

  return (
    <main className="page">
      <header className="hero">
        <div>
          <p className="eyebrow">Facilities</p>
          <h1>施設マスター</h1>
          <p className="subtle">施設の登録と設定をまとめて管理します。</p>
        </div>
        <TopNav />
      </header>

      <section className="panel">
        <header className="panel-header">
          <h2>新規施設の作成</h2>
        </header>
        <div className="form-grid">
          <label className="field">
            <span className="field-label">Name</span>
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <label className="field">
            <span className="field-label">Areas (JSON array)</span>
            <textarea
              className="textarea"
              value={areasText}
              onChange={(e) => setAreasText(e.target.value)}
              rows={6}
            />
          </label>
          <button className="btn primary" onClick={createFacility}>
            Create
          </button>
        </div>
      </section>

      <section className="panel">
        <header className="panel-header">
          <h2>登録済み施設</h2>
          <span className="badge">{facilities.length}</span>
        </header>
        {facilities.length === 0 ? (
          <p className="subtle">No facilities yet.</p>
        ) : (
          <div className="list">
            {facilities.map((facility) => (
              <Link key={facility.id} href={`/facilities/${facility.id}`} className="list-item">
                <div>
                  <p className="list-title">{facility.name}</p>
                  <p className="list-meta">{facility.id}</p>
                </div>
                <span className="ghost-link">詳細</span>
              </Link>
            ))}
          </div>
        )}
      </section>

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
          color: #f7f2e7;
          padding: 4px 10px;
          border-radius: 999px;
          font-size: 12px;
        }

        .form-grid {
          display: grid;
          gap: 16px;
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

        .input,
        .textarea {
          border: 1px solid rgba(25, 32, 30, 0.14);
          border-radius: 10px;
          padding: 8px 10px;
          background: #fbfbf9;
        }

        .btn {
          border: none;
          border-radius: 999px;
          padding: 10px 18px;
          background: #e6ebe9;
          color: #1f2a2a;
          font-weight: 600;
          cursor: pointer;
          width: fit-content;
        }

        .btn.primary {
          background: #1f2a2a;
          color: #f7f2e7;
        }

        .list {
          display: grid;
          gap: 12px;
        }

        .list-item {
          display: flex;
          justify-content: space-between;
          align-items: center;
          padding: 12px 14px;
          border-radius: 12px;
          border: 1px solid rgba(25, 32, 30, 0.06);
          background: #fbfbf9;
        }

        .list-title {
          margin: 0 0 4px;
          font-weight: 600;
        }

        .list-meta {
          margin: 0;
          font-size: 12px;
          color: #5f7b74;
        }

        .ghost-link {
          font-size: 13px;
          color: #5f7b74;
        }

        .message {
          margin-top: 12px;
          padding: 8px 12px;
          border-radius: 10px;
          background: #f0f4f2;
          font-size: 13px;
        }
      `}</style>
      <style jsx global>{`
        @import url("https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;700&family=Noto+Sans+JP:wght@400;600&display=swap");
      `}</style>
    </main>
  );
}
