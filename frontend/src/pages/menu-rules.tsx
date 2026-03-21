import { useEffect, useState } from "react";
import TopNav from "../components/TopNav";
import { apiClient } from "../services/apiClient";
import { DIET_TYPE_OPTIONS } from "../services/menuVocabulary";

type MenuRule = {
  id?: string;
  rule_type: "global" | "menu" | "facility";
  match_type?: string | null;
  menu_pattern?: string | null;
  facility_id?: string | null;
  daypart?: string | null;
  category?: string | null;
  diet_type?: string | null;
  unit_type?: string | null;
  qty_per_serving?: number | string | null;
  priority?: number | string | null;
  active?: boolean;
  isNew?: boolean;
};

const unitChoices = [
  { value: "g", label: "グラム(g)" },
  { value: "count", label: "個数" },
  { value: "cut", label: "切" },
];

const matchChoices = [
  { value: "exact", label: "完全一致" },
  { value: "contains", label: "部分一致" },
  { value: "regex", label: "正規表現" },
];

const createDraftRule = (ruleType: MenuRule["rule_type"]): MenuRule => ({
  rule_type: ruleType,
  match_type: ruleType === "global" ? null : "contains",
  menu_pattern: "",
  facility_id: "",
  daypart: "",
  category: "",
  diet_type: "",
  unit_type: "g",
  qty_per_serving: "",
  priority: "",
  active: true,
  isNew: true,
});

export default function MenuRulesPage() {
  const [globalRules, setGlobalRules] = useState<MenuRule[]>([]);
  const [menuRules, setMenuRules] = useState<MenuRule[]>([]);
  const [facilityRules, setFacilityRules] = useState<MenuRule[]>([]);
  const [message, setMessage] = useState("");

  const loadRules = async () => {
    try {
      const [globalRes, menuRes, facilityRes] = await Promise.all([
        apiClient.get("/menu-rules", { params: { rule_type: "global" } }),
        apiClient.get("/menu-rules", { params: { rule_type: "menu" } }),
        apiClient.get("/menu-rules", { params: { rule_type: "facility" } }),
      ]);
      setGlobalRules(globalRes.data.rules || []);
      setMenuRules(menuRes.data.rules || []);
      setFacilityRules(facilityRes.data.rules || []);
      setMessage("");
    } catch {
      setMessage("ルールの取得に失敗しました。");
    }
  };

  useEffect(() => {
    loadRules();
  }, []);

  const updateRuleField = (
    rules: MenuRule[],
    setRules: (value: MenuRule[]) => void,
    idx: number,
    field: keyof MenuRule,
    value: string | boolean
  ) => {
    const next = [...rules];
    next[idx] = { ...next[idx], [field]: value };
    setRules(next);
  };

  const saveRule = async (
    rules: MenuRule[],
    setRules: (value: MenuRule[]) => void,
    idx: number
  ) => {
    const rule = rules[idx];
    const payload = {
      rule_type: rule.rule_type,
      match_type: rule.match_type || null,
      menu_pattern: rule.menu_pattern || null,
      facility_id: rule.facility_id || null,
      daypart: rule.daypart || null,
      category: rule.category || null,
      diet_type: rule.diet_type || null,
      unit_type: rule.unit_type || null,
      qty_per_serving:
        rule.qty_per_serving === "" || rule.qty_per_serving == null
          ? null
          : Number(rule.qty_per_serving),
      priority:
        rule.priority === "" || rule.priority == null ? null : Number(rule.priority),
      active: rule.active ?? true,
    };
    try {
      if (!rule.id || rule.isNew) {
        const res = await apiClient.post("/menu-rules", payload);
        const updated = [...rules];
        updated[idx] = { ...res.data.rule, isNew: false };
        setRules(updated);
        setMessage("ルールを追加しました。");
      } else {
        await apiClient.put(`/menu-rules/${rule.id}`, payload);
        setMessage("ルールを更新しました。");
      }
    } catch {
      setMessage("ルールの保存に失敗しました。");
    }
  };

  const deleteRule = async (
    rules: MenuRule[],
    setRules: (value: MenuRule[]) => void,
    idx: number
  ) => {
    const rule = rules[idx];
    if (!rule.id || rule.isNew) {
      const next = rules.filter((_, i) => i !== idx);
      setRules(next);
      return;
    }
    try {
      await apiClient.delete(`/menu-rules/${rule.id}`);
      const next = rules.filter((_, i) => i !== idx);
      setRules(next);
      setMessage("ルールを削除しました。");
    } catch {
      setMessage("ルールの削除に失敗しました。");
    }
  };

  return (
    <main className="page">
      <header className="hero">
        <div>
          <p className="eyebrow">Menu Rules</p>
          <h1>メニュールール管理</h1>
          <p className="subtle">基本量・メニュー例外・施設例外をここで管理します。</p>
        </div>
        <TopNav />
      </header>

      {message && <p className="message">{message}</p>}

      <section className="panel">
        <header className="panel-header">
          <h2>基本ルール</h2>
          <button
            className="btn"
            onClick={() => setGlobalRules([...globalRules, createDraftRule("global")])}
          >
            追加
          </button>
        </header>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>時間帯</th>
                <th>区分</th>
                <th>食種</th>
                <th>単位</th>
                <th>量</th>
                <th>優先度</th>
                <th>有効</th>
                <th>保存</th>
                <th>削除</th>
              </tr>
            </thead>
            <tbody>
              {globalRules.length === 0 ? (
                <tr>
                  <td colSpan={9}>ルールがありません。</td>
                </tr>
              ) : (
                globalRules.map((rule, idx) => (
                  <tr key={rule.id || `global-${idx}`}>
                    <td>
                      <input
                        className="input"
                        value={rule.daypart || ""}
                        onChange={(e) =>
                          updateRuleField(globalRules, setGlobalRules, idx, "daypart", e.target.value)
                        }
                      />
                    </td>
                    <td>
                      <input
                        className="input"
                        value={rule.category || ""}
                        onChange={(e) =>
                          updateRuleField(globalRules, setGlobalRules, idx, "category", e.target.value)
                        }
                      />
                    </td>
                    <td>
                      <select
                        className="input"
                        value={rule.diet_type || ""}
                        onChange={(e) =>
                          updateRuleField(globalRules, setGlobalRules, idx, "diet_type", e.target.value)
                        }
                      >
                        {DIET_TYPE_OPTIONS.map((choice) => (
                          <option key={choice.value} value={choice.value}>
                            {choice.label}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <select
                        className="input"
                        value={rule.unit_type || ""}
                        onChange={(e) =>
                          updateRuleField(globalRules, setGlobalRules, idx, "unit_type", e.target.value)
                        }
                      >
                        <option value="">未選択</option>
                        {unitChoices.map((choice) => (
                          <option key={choice.value} value={choice.value}>
                            {choice.label}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <input
                        className="input"
                        type="number"
                        value={rule.qty_per_serving ?? ""}
                        onChange={(e) =>
                          updateRuleField(globalRules, setGlobalRules, idx, "qty_per_serving", e.target.value)
                        }
                      />
                    </td>
                    <td>
                      <input
                        className="input"
                        type="number"
                        value={rule.priority ?? ""}
                        onChange={(e) =>
                          updateRuleField(globalRules, setGlobalRules, idx, "priority", e.target.value)
                        }
                      />
                    </td>
                    <td>
                      <input
                        type="checkbox"
                        checked={rule.active ?? true}
                        onChange={(e) =>
                          updateRuleField(globalRules, setGlobalRules, idx, "active", e.target.checked)
                        }
                      />
                    </td>
                    <td>
                      <button className="btn" onClick={() => saveRule(globalRules, setGlobalRules, idx)}>
                        保存
                      </button>
                    </td>
                    <td>
                      <button className="btn" onClick={() => deleteRule(globalRules, setGlobalRules, idx)}>
                        削除
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel">
        <header className="panel-header">
          <h2>メニュー例外</h2>
          <button
            className="btn"
            onClick={() => setMenuRules([...menuRules, createDraftRule("menu")])}
          >
            追加
          </button>
        </header>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>マッチ</th>
                <th>メニュー</th>
                <th>時間帯</th>
                <th>区分</th>
                <th>食種</th>
                <th>単位</th>
                <th>量</th>
                <th>優先度</th>
                <th>有効</th>
                <th>保存</th>
                <th>削除</th>
              </tr>
            </thead>
            <tbody>
              {menuRules.length === 0 ? (
                <tr>
                  <td colSpan={11}>ルールがありません。</td>
                </tr>
              ) : (
                menuRules.map((rule, idx) => (
                  <tr key={rule.id || `menu-${idx}`}>
                    <td>
                      <select
                        className="input"
                        value={rule.match_type || ""}
                        onChange={(e) =>
                          updateRuleField(menuRules, setMenuRules, idx, "match_type", e.target.value)
                        }
                      >
                        <option value="">未選択</option>
                        {matchChoices.map((choice) => (
                          <option key={choice.value} value={choice.value}>
                            {choice.label}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <input
                        className="input"
                        value={rule.menu_pattern || ""}
                        onChange={(e) =>
                          updateRuleField(menuRules, setMenuRules, idx, "menu_pattern", e.target.value)
                        }
                      />
                    </td>
                    <td>
                      <input
                        className="input"
                        value={rule.daypart || ""}
                        onChange={(e) =>
                          updateRuleField(menuRules, setMenuRules, idx, "daypart", e.target.value)
                        }
                      />
                    </td>
                    <td>
                      <input
                        className="input"
                        value={rule.category || ""}
                        onChange={(e) =>
                          updateRuleField(menuRules, setMenuRules, idx, "category", e.target.value)
                        }
                      />
                    </td>
                    <td>
                      <select
                        className="input"
                        value={rule.diet_type || ""}
                        onChange={(e) =>
                          updateRuleField(menuRules, setMenuRules, idx, "diet_type", e.target.value)
                        }
                      >
                        {DIET_TYPE_OPTIONS.map((choice) => (
                          <option key={choice.value} value={choice.value}>
                            {choice.label}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <select
                        className="input"
                        value={rule.unit_type || ""}
                        onChange={(e) =>
                          updateRuleField(menuRules, setMenuRules, idx, "unit_type", e.target.value)
                        }
                      >
                        <option value="">未選択</option>
                        {unitChoices.map((choice) => (
                          <option key={choice.value} value={choice.value}>
                            {choice.label}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <input
                        className="input"
                        type="number"
                        value={rule.qty_per_serving ?? ""}
                        onChange={(e) =>
                          updateRuleField(menuRules, setMenuRules, idx, "qty_per_serving", e.target.value)
                        }
                      />
                    </td>
                    <td>
                      <input
                        className="input"
                        type="number"
                        value={rule.priority ?? ""}
                        onChange={(e) =>
                          updateRuleField(menuRules, setMenuRules, idx, "priority", e.target.value)
                        }
                      />
                    </td>
                    <td>
                      <input
                        type="checkbox"
                        checked={rule.active ?? true}
                        onChange={(e) =>
                          updateRuleField(menuRules, setMenuRules, idx, "active", e.target.checked)
                        }
                      />
                    </td>
                    <td>
                      <button className="btn" onClick={() => saveRule(menuRules, setMenuRules, idx)}>
                        保存
                      </button>
                    </td>
                    <td>
                      <button className="btn" onClick={() => deleteRule(menuRules, setMenuRules, idx)}>
                        削除
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel">
        <header className="panel-header">
          <h2>施設例外</h2>
          <button
            className="btn"
            onClick={() => setFacilityRules([...facilityRules, createDraftRule("facility")])}
          >
            追加
          </button>
        </header>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>施設ID</th>
                <th>マッチ</th>
                <th>メニュー</th>
                <th>時間帯</th>
                <th>区分</th>
                <th>食種</th>
                <th>単位</th>
                <th>量</th>
                <th>優先度</th>
                <th>有効</th>
                <th>保存</th>
                <th>削除</th>
              </tr>
            </thead>
            <tbody>
              {facilityRules.length === 0 ? (
                <tr>
                  <td colSpan={12}>ルールがありません。</td>
                </tr>
              ) : (
                facilityRules.map((rule, idx) => (
                  <tr key={rule.id || `facility-${idx}`}>
                    <td>
                      <input
                        className="input"
                        value={rule.facility_id || ""}
                        onChange={(e) =>
                          updateRuleField(facilityRules, setFacilityRules, idx, "facility_id", e.target.value)
                        }
                      />
                    </td>
                    <td>
                      <select
                        className="input"
                        value={rule.match_type || ""}
                        onChange={(e) =>
                          updateRuleField(facilityRules, setFacilityRules, idx, "match_type", e.target.value)
                        }
                      >
                        <option value="">未選択</option>
                        {matchChoices.map((choice) => (
                          <option key={choice.value} value={choice.value}>
                            {choice.label}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <input
                        className="input"
                        value={rule.menu_pattern || ""}
                        onChange={(e) =>
                          updateRuleField(
                            facilityRules,
                            setFacilityRules,
                            idx,
                            "menu_pattern",
                            e.target.value
                          )
                        }
                      />
                    </td>
                    <td>
                      <input
                        className="input"
                        value={rule.daypart || ""}
                        onChange={(e) =>
                          updateRuleField(facilityRules, setFacilityRules, idx, "daypart", e.target.value)
                        }
                      />
                    </td>
                    <td>
                      <input
                        className="input"
                        value={rule.category || ""}
                        onChange={(e) =>
                          updateRuleField(facilityRules, setFacilityRules, idx, "category", e.target.value)
                        }
                      />
                    </td>
                    <td>
                      <select
                        className="input"
                        value={rule.diet_type || ""}
                        onChange={(e) =>
                          updateRuleField(facilityRules, setFacilityRules, idx, "diet_type", e.target.value)
                        }
                      >
                        {DIET_TYPE_OPTIONS.map((choice) => (
                          <option key={choice.value} value={choice.value}>
                            {choice.label}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <select
                        className="input"
                        value={rule.unit_type || ""}
                        onChange={(e) =>
                          updateRuleField(facilityRules, setFacilityRules, idx, "unit_type", e.target.value)
                        }
                      >
                        <option value="">未選択</option>
                        {unitChoices.map((choice) => (
                          <option key={choice.value} value={choice.value}>
                            {choice.label}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <input
                        className="input"
                        type="number"
                        value={rule.qty_per_serving ?? ""}
                        onChange={(e) =>
                          updateRuleField(
                            facilityRules,
                            setFacilityRules,
                            idx,
                            "qty_per_serving",
                            e.target.value
                          )
                        }
                      />
                    </td>
                    <td>
                      <input
                        className="input"
                        type="number"
                        value={rule.priority ?? ""}
                        onChange={(e) =>
                          updateRuleField(facilityRules, setFacilityRules, idx, "priority", e.target.value)
                        }
                      />
                    </td>
                    <td>
                      <input
                        type="checkbox"
                        checked={rule.active ?? true}
                        onChange={(e) =>
                          updateRuleField(facilityRules, setFacilityRules, idx, "active", e.target.checked)
                        }
                      />
                    </td>
                    <td>
                      <button className="btn" onClick={() => saveRule(facilityRules, setFacilityRules, idx)}>
                        保存
                      </button>
                    </td>
                    <td>
                      <button className="btn" onClick={() => deleteRule(facilityRules, setFacilityRules, idx)}>
                        削除
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>

      <style jsx>{`
        :global(body) {
          background: radial-gradient(circle at top left, #f8f4ea, #f4f7f6 40%, #eef1f0 100%);
          color: #1f2a2a;
          font-family: "Manrope", "Noto Sans JP", sans-serif;
        }

        :global(*) {
          box-sizing: border-box;
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

        .table-wrap {
          overflow-x: auto;
        }

        table {
          width: 100%;
          border-collapse: collapse;
          font-size: 13px;
        }

        th,
        td {
          padding: 10px;
          text-align: left;
        }

        thead {
          background: #f4f1ea;
        }

        tbody tr:nth-child(even) {
          background: #faf9f5;
        }

        .btn {
          border: none;
          border-radius: 999px;
          padding: 6px 12px;
          background: #e6ebe9;
          color: #1f2a2a;
          font-weight: 600;
          cursor: pointer;
        }

        .input {
          border: 1px solid rgba(25, 32, 30, 0.14);
          border-radius: 10px;
          padding: 6px 8px;
          background: #fbfbf9;
        }

        .message {
          margin: 12px 0 16px;
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
