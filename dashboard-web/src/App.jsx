import React, { useEffect, useState, useCallback } from "react";

const API_URL = import.meta.env.VITE_API_URL || "";
const API_KEY = import.meta.env.VITE_API_KEY || "";

async function apiGet(path) {
  const res = await fetch(API_URL + path);
  if (!res.ok) throw new Error("Falha ao buscar " + path);
  return res.json();
}

async function apiPatch(path, body) {
  const res = await fetch(API_URL + path, {
    method: "PATCH",
    headers: { "content-type": "application/json", "x-api-key": API_KEY },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error("Falha ao atualizar " + path);
  return res.json();
}

async function apiPutConfig(body) {
  const res = await fetch(API_URL + "/config", {
    method: "PUT",
    headers: { "content-type": "application/json", "x-api-key": API_KEY },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error("Falha ao salvar config");
  return res.json();
}

const STATUS_LABEL = {
  found: "Encontrada",
  applied: "Aplicada — aguardando IA",
  in_progress: "IA executando…",
  delivered: "Entregue — revisar",
  approved: "Aprovada",
};

function timeAgo(iso) {
  if (!iso) return "";
  const diffMs = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return "agora";
  if (mins < 60) return `${mins} min atrás`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h atrás`;
  return `${Math.floor(hrs / 24)}d atrás`;
}

function MissionCard({ mission, onChange }) {
  const [busy, setBusy] = useState(false);
  const [showText, setShowText] = useState(false);

  async function markApplied() {
    setBusy(true);
    try {
      await apiPatch(`/missions/${encodeURIComponent(mission.missionId)}`, { status: "applied" });
      onChange();
    } catch (e) {
      alert(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function approve() {
    setBusy(true);
    try {
      await apiPatch(`/missions/${encodeURIComponent(mission.missionId)}`, { status: "approved" });
      onChange();
    } catch (e) {
      alert(e.message);
    } finally {
      setBusy(false);
    }
  }

  const salary =
    mission.salaryMin ? `$${mission.salaryMin}${mission.salaryMax ? "–$" + mission.salaryMax : ""}/ano` : null;

  return (
    <div className="mission">
      <div className="row1">
        <div>
          <h3>{mission.position}</h3>
          <div className="company">{mission.company || "?"}</div>
        </div>
        <span className={`badge ${mission.status}`}>{STATUS_LABEL[mission.status] || mission.status}</span>
      </div>

      <div className="meta">
        {salary ? salary + " · " : ""}
        score {mission.score ?? "-"} · {timeAgo(mission.updatedAt || mission.createdAt)}
        {mission.url ? (
          <>
            {" "}
            ·{" "}
            <a className="link" href={mission.url} target="_blank" rel="noreferrer">
              ver vaga original
            </a>
          </>
        ) : null}
      </div>

      {mission.proposalText ? (
        <>
          <button className="secondary" style={{ marginTop: 10 }} onClick={() => setShowText((s) => !s)}>
            {showText ? "Esconder proposta" : "Ver proposta gerada"}
          </button>
          {showText && <div className="text-block">{mission.proposalText}</div>}
        </>
      ) : null}

      {mission.status === "delivered" && mission.deliveryText ? (
        <div className="text-block">{mission.deliveryText}</div>
      ) : null}

      <div className="actions">
        {mission.status === "found" && (
          <button disabled={busy} onClick={markApplied}>
            Marcar como aplicada
          </button>
        )}
        {mission.status === "delivered" && (
          <button disabled={busy} onClick={approve}>
            Aprovar entrega
          </button>
        )}
        {mission.status === "approved" && (
          <span className="meta">Pronta para enviar ao cliente e receber o pagamento.</span>
        )}
      </div>
    </div>
  );
}

function ConfigBox() {
  const [value, setValue] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    apiGet("/config")
      .then((r) => setValue(r.config?.paymentInfo || ""))
      .catch(() => {});
  }, []);

  async function save() {
    await apiPutConfig({ paymentInfo: value });
    setSaved(true);
    setTimeout(() => setSaved(false), 1500);
  }

  return (
    <div className="config-box">
      <input
        placeholder="Link ou e-mail de pagamento (Wise)"
        value={value}
        onChange={(e) => setValue(e.target.value)}
      />
      <button onClick={save}>{saved ? "Salvo ✓" : "Salvar"}</button>
    </div>
  );
}

export default function App() {
  const [missions, setMissions] = useState(null);
  const [error, setError] = useState(null);

  const load = useCallback(() => {
    apiGet("/missions")
      .then((r) => setMissions(r.missions || []))
      .catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 20000);
    return () => clearInterval(id);
  }, [load]);

  const sorted = (missions || [])
    .slice()
    .sort((a, b) => (b.salaryMin || 0) - (a.salaryMin || 0) || (b.score || 0) - (a.score || 0));

  const counts = {
    found: sorted.filter((m) => m.status === "found").length,
    applied: sorted.filter((m) => m.status === "applied").length,
    delivered: sorted.filter((m) => m.status === "delivered").length,
    approved: sorted.filter((m) => m.status === "approved").length,
  };

  return (
    <div className="app">
      <header className="top">
        <div>
          <h1>Autonomo</h1>
          <div className="sub">missões encontradas e geridas pelo motor</div>
        </div>
      </header>

      {!API_URL && (
        <div className="error">
          VITE_API_URL não configurada — defina as variáveis de ambiente do Amplify.
        </div>
      )}
      {error && <div className="error">{error}</div>}

      <div className="stats">
        <div className="stat">
          <div className="n">{counts.found}</div>
          <div className="l">Encontradas</div>
        </div>
        <div className="stat">
          <div className="n">{counts.applied}</div>
          <div className="l">Em execução</div>
        </div>
        <div className="stat">
          <div className="n">{counts.delivered}</div>
          <div className="l">Pra revisar</div>
        </div>
        <div className="stat">
          <div className="n">{counts.approved}</div>
          <div className="l">Aprovadas</div>
        </div>
      </div>

      <div className="section-title">Recebimento de pagamento</div>
      <ConfigBox />

      <div className="section-title">Missões</div>
      {missions === null ? (
        <div className="empty">Carregando…</div>
      ) : sorted.length === 0 ? (
        <div className="empty">Nenhuma missão ainda. O motor busca vagas a cada 2h.</div>
      ) : (
        sorted.map((m) => <MissionCard key={m.missionId} mission={m} onChange={load} />)
      )}
    </div>
  );
}
