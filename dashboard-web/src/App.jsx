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
  approved: "Aprovada — a receber",
  paid: "Pago ✓",
  bid_approved: "Bid aprovado — enviando…",
  submitting_bid: "Enviando bid…",
  bid_submitted: "Bid enviado — aguardando cliente",
};

const CURRENCIES = ["USD", "EUR", "GBP", "BRL"];

function formatMoney(value, currency) {
  return `${currency} ${Number(value).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

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
  const [valueInput, setValueInput] = useState(mission.agreedValue ?? mission.bidAmount ?? "");
  const [currencyInput, setCurrencyInput] = useState(mission.agreedCurrency || mission.currency || "USD");
  const [valueSaved, setValueSaved] = useState(false);

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

  async function approveBid() {
    setBusy(true);
    try {
      await apiPatch(`/missions/${encodeURIComponent(mission.missionId)}`, { status: "bid_approved" });
      onChange();
    } catch (e) {
      alert(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function saveValue() {
    if (valueInput === "" || Number.isNaN(Number(valueInput))) return;
    setBusy(true);
    try {
      await apiPatch(`/missions/${encodeURIComponent(mission.missionId)}`, {
        agreedValue: Number(valueInput),
        agreedCurrency: currencyInput,
      });
      setValueSaved(true);
      setTimeout(() => setValueSaved(false), 1500);
      onChange();
    } catch (e) {
      alert(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function markPaid() {
    setBusy(true);
    try {
      await apiPatch(`/missions/${encodeURIComponent(mission.missionId)}`, {
        status: "paid",
        paidAt: new Date().toISOString(),
      });
      onChange();
    } catch (e) {
      alert(e.message);
    } finally {
      setBusy(false);
    }
  }

  const salary = mission.salaryMin
    ? `${mission.currency || "USD"} ${mission.salaryMin}${mission.salaryMax ? "–" + mission.salaryMax : ""}` +
      (mission.salaryPeriod ? ` (${mission.salaryPeriod})` : "/ano")
    : null;

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

      {(mission.status === "delivered" || mission.status === "approved") && (
        <div className="value-box">
          <span className="meta">Valor combinado:</span>
          <input
            type="number"
            step="0.01"
            placeholder="0.00"
            value={valueInput}
            onChange={(e) => setValueInput(e.target.value)}
          />
          <select value={currencyInput} onChange={(e) => setCurrencyInput(e.target.value)}>
            {CURRENCIES.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
          <button className="secondary" disabled={busy} onClick={saveValue}>
            {valueSaved ? "Salvo ✓" : "Salvar valor"}
          </button>
        </div>
      )}

      <div className="actions">
        {mission.status === "found" && mission.source === "freelancer" && (
          <button disabled={busy} onClick={approveBid}>
            Aprovar e enviar bid (via API)
          </button>
        )}
        {mission.status === "found" && mission.source !== "freelancer" && (
          <button disabled={busy} onClick={markApplied}>
            Marcar como aplicada
          </button>
        )}
        {mission.status === "bid_submitted" && (
          <button disabled={busy} onClick={markApplied}>
            Cliente aceitou — iniciar execução
          </button>
        )}
        {mission.status === "delivered" && (
          <button disabled={busy} onClick={approve}>
            Aprovar entrega
          </button>
        )}
        {mission.status === "approved" && (
          <button disabled={busy} onClick={markPaid}>
            Marcar pagamento recebido
          </button>
        )}
        {mission.status === "paid" && (
          <span className="meta">
            Pago{mission.paidAt ? " em " + new Date(mission.paidAt).toLocaleDateString("pt-BR") : ""}
            {mission.agreedValue ? " · " + formatMoney(mission.agreedValue, mission.agreedCurrency || "USD") : ""}
          </span>
        )}
      </div>
    </div>
  );
}

function sumByCurrency(missions) {
  const totals = {};
  for (const m of missions) {
    if (!m.agreedValue) continue;
    const cur = m.agreedCurrency || "USD";
    totals[cur] = (totals[cur] || 0) + Number(m.agreedValue);
  }
  return totals;
}

function FinanceSummary({ missions }) {
  const receber = sumByCurrency(missions.filter((m) => m.status === "approved"));
  const recebido = sumByCurrency(missions.filter((m) => m.status === "paid"));
  const receberCurrencies = Object.keys(receber);
  const recebidoCurrencies = Object.keys(recebido);

  if (receberCurrencies.length === 0 && recebidoCurrencies.length === 0) {
    return (
      <div className="finance-box">
        <div className="meta">
          Nada com valor combinado ainda — preencha "Valor combinado" numa missão entregue pra aparecer aqui.
        </div>
      </div>
    );
  }

  return (
    <div className="finance-box">
      <div className="finance-col">
        <div className="finance-label">A receber</div>
        {receberCurrencies.length === 0 ? (
          <div className="meta">—</div>
        ) : (
          receberCurrencies.map((c) => (
            <div className="finance-value" key={c}>{formatMoney(receber[c], c)}</div>
          ))
        )}
      </div>
      <div className="finance-col">
        <div className="finance-label">Recebido</div>
        {recebidoCurrencies.length === 0 ? (
          <div className="meta">—</div>
        ) : (
          recebidoCurrencies.map((c) => (
            <div className="finance-value paid" key={c}>{formatMoney(recebido[c], c)}</div>
          ))
        )}
      </div>
    </div>
  );
}

function DiscoveryTrigger() {
  const [busy, setBusy] = useState(false);
  const [requested, setRequested] = useState(false);

  async function trigger() {
    setBusy(true);
    try {
      await apiPutConfig({ forceDiscovery: true, forceDiscoveryAt: new Date().toISOString() });
      setRequested(true);
    } catch (e) {
      alert(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="discovery-trigger">
      <button disabled={busy} onClick={trigger}>
        {busy ? "Solicitando…" : "Buscar vagas agora"}
      </button>
      {requested && <span className="meta">Solicitado ✓ — roda em até 1 min</span>}
    </div>
  );
}

// paymentInfo agora é um objeto { USD: "...", EUR: "...", ... } -- a Wise (e a
// maioria dos provedores multi-moeda) dá dados de recebimento DIFERENTES por
// moeda (conta local em USD, IBAN em EUR, sort code em GBP...), um campo único
// não dava conta disso. Formato antigo (string única) é tratado como legado:
// se vier assim, ignora e começa do zero -- não vale a pena migrar um valor
// que não sabemos a qual moeda pertencia.
function PaymentMethodsBox() {
  const [methods, setMethods] = useState({});
  const [saved, setSaved] = useState({});
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    apiGet("/config")
      .then((r) => {
        const pi = r.config?.paymentInfo;
        setMethods(pi && typeof pi === "object" && !Array.isArray(pi) ? pi : {});
        setLoaded(true);
      })
      .catch(() => setLoaded(true));
  }, []);

  async function saveCurrency(cur) {
    const next = { ...methods, [cur]: methods[cur] || "" };
    await apiPutConfig({ paymentInfo: next });
    setMethods(next);
    setSaved((s) => ({ ...s, [cur]: true }));
    setTimeout(() => setSaved((s) => ({ ...s, [cur]: false })), 1500);
  }

  if (!loaded) return <div className="meta">Carregando…</div>;

  return (
    <div className="payment-methods">
      {CURRENCIES.map((cur) => (
        <div className="payment-row" key={cur}>
          <div className="payment-currency">{cur}</div>
          <textarea
            placeholder={`Dados de recebimento em ${cur} (conta Wise, IBAN, e-mail, link...)`}
            value={methods[cur] || ""}
            onChange={(e) => setMethods((m) => ({ ...m, [cur]: e.target.value }))}
          />
          <button className="secondary" onClick={() => saveCurrency(cur)}>
            {saved[cur] ? "Salvo ✓" : "Salvar"}
          </button>
        </div>
      ))}
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
    paid: sorted.filter((m) => m.status === "paid").length,
  };

  return (
    <div className="app">
      <header className="top">
        <div>
          <h1>Autonomo</h1>
          <div className="sub">missões encontradas e geridas pelo motor</div>
        </div>
        <DiscoveryTrigger />
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
          <div className="l">A receber</div>
        </div>
        <div className="stat">
          <div className="n">{counts.paid}</div>
          <div className="l">Pagas</div>
        </div>
      </div>

      <div className="section-title">Financeiro</div>
      <FinanceSummary missions={sorted} />

      <div className="section-title">Formas de recebimento</div>
      <PaymentMethodsBox />

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
