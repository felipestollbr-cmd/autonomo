#!/usr/bin/env python3
"""
Gera os workflows do n8n (JSON importável) para o motor do Autonomo.
Rode: python3 build_workflows.py
Saída: workflows/01-discovery-and-proposal.json
       workflows/02-execution-and-delivery.json
"""
import json, uuid, os

OUT = os.path.join(os.path.dirname(__file__), "workflows")
os.makedirs(OUT, exist_ok=True)

def nid():
    return str(uuid.uuid4())

def guard_nodes(prefix, position):
    """Guard de custo: checa/incrementa o teto diario de chamadas de IA antes
    de cada chamada de IA. Retorna (node_check, node_if) — ligue o node_check
    depois do node de origem do prompt, e o node_if depois do node_check; o
    branch 'true' do IF segue pro node da IA, o 'false' termina ali (pula a
    chamada)."""
    x, y = position
    check = {
        "parameters": {
            "method": "POST",
            "url": "={{ $env.DASHBOARD_API_URL }}/guard/check",
            "sendHeaders": True,
            "headerParameters": {"parameters": [
                {"name": "content-type", "value": "application/json"},
                {"name": "x-api-key", "value": "={{ $env.DASHBOARD_API_KEY }}"},
            ]},
            "sendBody": True, "specifyBody": "json", "jsonBody": "={{ {} }}",
            "options": {},
        },
        "id": nid(), "name": f"{prefix} — Guard Check", "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.2, "position": [x, y],
    }
    gate = {
        "parameters": {"conditions": {"options": {"caseSensitive": True, "typeValidation": "strict"},
            "conditions": [{"leftValue": "={{ $json.allowed }}", "rightValue": True,
                            "operator": {"type": "boolean", "operation": "true"}}],
            "combinator": "and"}},
        "id": nid(), "name": f"{prefix} — Guard OK?", "type": "n8n-nodes-base.if",
        "typeVersion": 2, "position": [x + 110, y],
    }
    return check, gate

# ----------------------------------------------------------------------------
# WORKFLOW 1 — Descoberta + Rascunho de proposta
# Schedule -> HTTP (RemoteOK) -> Code(filtra/score/dedup/monta prompt)
#          -> HTTP (Gemini) -> Code(extrai texto) -> Telegram
# ----------------------------------------------------------------------------

# Code node: parsing, filtro por keyword (barato, sem IA), dedup. Combina 4
# fontes públicas (RemoteOK, Himalayas, Adzuna US, Adzuna DE — as duas últimas
# cobrindo USD e EUR de verdade) num shape único, e monta o prompt de UM lote
# pra a IA rankear por aderência real ao perfil (não só contagem de keyword) —
# só essa 1 chamada extra por execução, não uma por vaga, pra não estourar o
# Guard.
discovery_code = r"""
// ===== CONFIG (edite aqui) ==================================================
const KEYWORDS   = ["automation", "n8n", "ai", "python", "react", "workflow"];
const MIN_SALARY = 0;      // 0 = ignora salário. Ex.: 2000 (USD/ano no dado bruto)
const MAX_PROPOSALS_PER_RUN = 5;   // teto de propostas (e chamadas de IA) por execução
const RANK_POOL_SIZE = 15; // quantos candidatos entram no lote de ranking por IA
// ============================================================================

const missionsResp = $input.first().json;
const existing = (missionsResp && Array.isArray(missionsResp.missions)) ? missionsResp.missions : [];
const existingIds = new Set(existing.map(m => m.missionId));

const normalized = [];

// ---- RemoteOK — retorna array cujo PRIMEIRO item é um aviso legal ----------
try {
  const raw = $('RemoteOK API').first().json;
  let jobs = Array.isArray(raw) ? raw : (raw.body || []);
  jobs = jobs.filter(j => j && j.id && j.position);
  for (const j of jobs) {
    normalized.push({
      source: "remoteok",
      missionId: "remoteok-" + j.id,
      position: j.position,
      company: j.company || "",
      url: j.url || ("https://remoteok.com/remote-jobs/" + j.id),
      tags: Array.isArray(j.tags) ? j.tags.join(", ") : "",
      salaryMin: j.salary_min || null,
      salaryMax: j.salary_max || null,
      currency: "USD",
      salaryPeriod: "annual",
      description: String(j.description || "").replace(/<[^>]+>/g, " ").slice(0, 1800),
    });
  }
} catch (e) {}

// ---- Himalayas — sem auth, já vem com moeda por vaga (himalayas.app/docs) --
try {
  const raw = $('Himalayas API').first().json;
  const jobs = (raw && Array.isArray(raw.jobs)) ? raw.jobs : [];
  for (const j of jobs) {
    if (!j.guid) continue;
    normalized.push({
      source: "himalayas",
      missionId: "himalayas-" + j.guid,
      position: j.title,
      company: j.companyName || "",
      url: j.applicationLink || "",
      tags: Array.isArray(j.categories) ? j.categories.join(", ") : "",
      salaryMin: j.minSalary || null,
      salaryMax: j.maxSalary || null,
      currency: j.currency || "USD",
      salaryPeriod: j.salaryPeriod || null,
      description: String(j.excerpt || "").slice(0, 1800),
    });
  }
} catch (e) {}

// ---- Adzuna — precisa de app_id/app_key grátis; um node por país/moeda -----
function parseAdzuna(nodeName, currency) {
  try {
    const raw = $(nodeName).first().json;
    const jobs = (raw && Array.isArray(raw.results)) ? raw.results : [];
    for (const j of jobs) {
      if (!j.id) continue;
      normalized.push({
        source: "adzuna",
        missionId: "adzuna-" + j.id,
        position: j.title,
        company: (j.company && j.company.display_name) || "",
        url: j.redirect_url || "",
        tags: j.category ? String((j.category && j.category.label) || j.category) : "",
        salaryMin: j.salary_min || null,
        salaryMax: j.salary_max || null,
        currency,
        salaryPeriod: "annual",
        description: String(j.description || "").slice(0, 1800),
      });
    }
  } catch (e) {}
}
parseAdzuna("Adzuna US", "USD");
parseAdzuna("Adzuna DE", "EUR");

function scoreJob(j) {
  const hay = ((j.position||"") + " " + (j.description||"") + " " + (j.tags||"")).toLowerCase();
  let score = 0;
  for (const k of KEYWORDS) if (hay.includes(k.toLowerCase())) score += 1;
  return score;
}

// Dedup usando o dashboard (DynamoDB) como fonte de verdade — essa versão do
// n8n não persiste static data entre execuções agendadas, então não dá pra
// guardar "já vistas" do lado do n8n.
const fresh = [];
for (const j of normalized) {
  if (!j.position || !j.missionId) continue;
  if (existingIds.has(j.missionId)) continue;
  const score = scoreJob(j);
  if (score === 0) continue;
  const salary = Number(j.salaryMin || 0);
  if (MIN_SALARY > 0 && salary && salary < MIN_SALARY) continue;
  fresh.push({ ...j, keywordScore: score });
}

// Pool mais largo que o teto final — a IA vai re-rankear por aderência real
// (não só contagem de keyword) e escolher os melhores MAX_PROPOSALS_PER_RUN.
fresh.sort((a,b) => b.keywordScore - a.keywordScore);
const pool = fresh.slice(0, RANK_POOL_SIZE);

if (pool.length === 0) return [];  // nada pra rankear, workflow termina aqui

const listing = pool.map((j, i) =>
  `[${i}] missionId=${j.missionId}\nCargo: ${j.position} @ ${j.company || "?"}\n` +
  `Tags: ${j.tags}\nDescrição: ${(j.description || "").slice(0, 400)}`
).join("\n\n");

const rankPrompt =
`Você está ajudando o Felipe, um desenvolvedor e fundador de SaaS no Brasil (skills:
automação/n8n, IA aplicada, Python, React, workflows de integração), a escolher em
quais vagas freelance vale mais a pena investir tempo se candidatando.

Abaixo estão ${pool.length} vagas pré-filtradas por palavra-chave. Avalie CADA UMA e
dê uma nota de 0 a 10 pra o quão bem ela combina com esse perfil — considere aderência
técnica real (não só a palavra-chave estar presente), se o texto sugere trabalho
substancial vs. tarefa trivial, e se parece vaga vaga demais ou suspeita (nota baixa
nesse caso).

Responda SOMENTE um JSON array, sem texto antes ou depois, neste formato exato:
[{"missionId": "...", "fitScore": 0, "reason": "motivo em até 12 palavras"}]

Vagas:
${listing}`;

return [{ json: { pool, rankPrompt, maxProposals: MAX_PROPOSALS_PER_RUN } }];
"""

# Code node: parseia o ranking da IA, escolhe os melhores e monta o prompt de
# proposta de cada um. Se o parse falhar (ou a IA não avaliar algum item), cai
# pro score de keyword como desempate/fallback — nunca trava por causa disso.
rank_apply_code = r"""
const prep = $('Filter Candidates').first().json;
const pool = prep.pool || [];
const maxProposals = prep.maxProposals || 5;

let ranked;
try {
  const r = $input.first().json;
  const parts = r.candidates && r.candidates[0] && r.candidates[0].content
    ? r.candidates[0].content.parts : null;
  const text = Array.isArray(parts) ? parts.map(p => p.text || "").join("") : "";
  const match = text.match(/\[[\s\S]*\]/);
  const arr = match ? JSON.parse(match[0]) : [];
  const byId = {};
  for (const it of arr) if (it && it.missionId) byId[it.missionId] = it;
  ranked = pool.map(j => ({
    job: j,
    fitScore: byId[j.missionId] ? Number(byId[j.missionId].fitScore) : null,
  }));
} catch (e) {
  ranked = pool.map(j => ({ job: j, fitScore: null }));
}

ranked.sort((a, b) => {
  const fa = a.fitScore != null ? a.fitScore : -1;
  const fb = b.fitScore != null ? b.fitScore : -1;
  if (fb !== fa) return fb - fa;
  return b.job.keywordScore - a.job.keywordScore;   // desempate/fallback
});

const chosen = ranked.slice(0, maxProposals);

const out = [];
for (const c of chosen) {
  const j = c.job;

  const aiPrompt =
`Você é o Felipe, um desenvolvedor e fundador de SaaS no Brasil, escrevendo uma proposta
curta e humana para se candidatar a uma vaga freelance/remota. Escreva em INGLÊS,
no máximo 130 palavras, tom direto e confiante, sem clichês de "I am excited".
Abra com uma frase que mostre que você entendeu o problema específico do cliente.
Cite 1 resultado concreto que você entregaria. Feche com uma pergunta aberta.
NÃO invente experiências específicas; fale de capacidade, não de histórico falso.

Vaga: ${j.position} @ ${j.company || "?"}
Tags: ${j.tags}
Descrição: ${j.description}`;

  out.push({
    json: {
      missionId: j.missionId,
      source: j.source,
      position: j.position,
      company: j.company,
      url: j.url,
      score: c.fitScore != null ? c.fitScore : j.keywordScore,
      tags: j.tags,
      salaryMin: j.salaryMin,
      salaryMax: j.salaryMax,
      currency: j.currency,
      salaryPeriod: j.salaryPeriod,
      description: j.description,
      aiPrompt,
    }
  });
}

return out;
"""

# Code node: extrai o texto da resposta do Gemini e monta a msg do Telegram.
extract_code = r"""
const r = $input.first().json;
let text = "";
try {
  // Formato da Gemini generateContent API:
  // { candidates: [ { content: { parts: [ { text: '...' } ] } } ] }
  const parts = r.candidates && r.candidates[0] && r.candidates[0].content
    ? r.candidates[0].content.parts : null;
  if (Array.isArray(parts)) {
    text = parts.map(p => p.text || "").join("\n").trim();
  }
} catch (e) { text = ""; }
if (!text) text = "(a IA não retornou texto — verifique a credencial/o modelo/a cota gratuita)";

// O nó do Telegram sempre manda com parse_mode Markdown (não dá pra desligar),
// então escapamos os caracteres especiais do texto livre (gerado pela IA ou
// vindo da vaga) para nunca quebrar o parser do Telegram.
function escMd(s) {
  return String(s == null ? "" : s).replace(/([_*`\[])/g, "\\$1");
}

// Recupera os dados da vaga que trafegam junto (pinned via 'Merge'? não —
// aqui usamos o item anterior via $items). Como o HTTP substitui o json,
// buscamos os campos da vaga no nó de score.
const meta = $('Select by Fit').item.json;
const SOURCE_LABEL = { remoteok: "RemoteOK", himalayas: "Himalayas", adzuna: "Adzuna" };

const msg =
`🧭 *Nova vaga* (score ${meta.score})\n` +
`*${escMd(meta.position)}* — ${escMd(meta.company)}\n` +
`🔗 ${meta.url}\n` +
(meta.salaryMin ? `💰 ${meta.currency || "USD"} ${meta.salaryMin}${meta.salaryMax ? "–"+meta.salaryMax : ""}${meta.salaryPeriod ? " ("+meta.salaryPeriod+")" : ""}\n` : "") +
`_via ${SOURCE_LABEL[meta.source] || meta.source}_\n\n` +
`✍️ *Rascunho de proposta:*\n${escMd(text)}\n\n` +
`➡️ Se curtir, candidate-se você mesmo na fonte com esse texto.`;

return [{ json: {
  telegramText: msg,
  missionId: meta.missionId,
  source: meta.source,
  position: meta.position,
  company: meta.company,
  url: meta.url,
  score: meta.score,
  tags: meta.tags,
  salaryMin: meta.salaryMin,
  salaryMax: meta.salaryMax,
  currency: meta.currency,
  salaryPeriod: meta.salaryPeriod,
  description: meta.description,
  proposalText: text,
} }];
"""

n_schedule = {
    "parameters": {"rule": {"interval": [{"field": "hours", "hoursInterval": 2}]}},
    "id": nid(), "name": "Every 2h", "type": "n8n-nodes-base.scheduleTrigger",
    "typeVersion": 1.2, "position": [-360, 0],
}

# ----------------------------------------------------------------------------
# Gatilho manual: o botão "Buscar vagas agora" do dashboard-web salva
# forceDiscovery=true no /config (mesma tabela usada pra paymentInfo). Esse
# poll de 1min detecta a flag, reseta ela (pra nao rodar de novo no proximo
# minuto) e entra na MESMA cadeia de descoberta do "Every 2h" -- reaproveita
# score/Guard/Gemini/dedup sem duplicar logica. Mesmo padrao de poll ja usado
# no WF02 (Poll Applied Missions).
# ----------------------------------------------------------------------------
n_manual_schedule = {
    "parameters": {"rule": {"interval": [{"field": "minutes", "minutesInterval": 1}]}},
    "id": nid(), "name": "Poll Manual Trigger (1min)", "type": "n8n-nodes-base.scheduleTrigger",
    "typeVersion": 1.2, "position": [-360, -160],
}
n_manual_get_config = {
    "parameters": {
        "url": "={{ $env.DASHBOARD_API_URL }}/config",
        "options": {"response": {"response": {"responseFormat": "json"}}},
    },
    "id": nid(), "name": "Get Config", "type": "n8n-nodes-base.httpRequest",
    "typeVersion": 4.2, "position": [-140, -160],
}
n_manual_if = {
    "parameters": {"conditions": {"options": {"caseSensitive": True, "typeValidation": "strict"},
        "conditions": [{"leftValue": "={{ $json.config.forceDiscovery }}", "rightValue": True,
                        "operator": {"type": "boolean", "operation": "true"}}],
        "combinator": "and"}},
    "id": nid(), "name": "Force Discovery?", "type": "n8n-nodes-base.if",
    "typeVersion": 2, "position": [80, -160],
}
n_manual_reset = {
    "parameters": {
        "method": "PUT",
        "url": "={{ $env.DASHBOARD_API_URL }}/config",
        "sendHeaders": True,
        "headerParameters": {"parameters": [
            {"name": "content-type", "value": "application/json"},
            {"name": "x-api-key", "value": "={{ $env.DASHBOARD_API_KEY }}"},
        ]},
        "sendBody": True, "specifyBody": "json",
        "jsonBody": "={{ { \"forceDiscovery\": false } }}",
        "options": {},
    },
    # Reseta ANTES de entrar na cadeia de descoberta (mesmo racional do "Mark
    # Mission In Progress" do WF02): evita re-disparar no proximo poll de 1min
    # se a descoberta demorar mais que isso.
    "id": nid(), "name": "Reset Force Discovery Flag", "type": "n8n-nodes-base.httpRequest",
    "typeVersion": 4.2, "position": [300, -160],
}
n_http_remoteok = {
    "parameters": {
        "url": "https://remoteok.com/api",
        "options": {"response": {"response": {"responseFormat": "json"}}},
        "sendHeaders": True,
        "headerParameters": {"parameters": [
            {"name": "User-Agent", "value": "AutonomoBot/1.0 (contato: seu-email@exemplo.com)"}
        ]},
    },
    # onError: as 4 fontes agora estão encadeadas numa linha só (ver
    # comentário na wiring do wf1) — sem isso, uma instabilidade pontual do
    # RemoteOK derrubaria a execução inteira e mataria Himalayas/Adzuna junto.
    "id": nid(), "name": "RemoteOK API", "type": "n8n-nodes-base.httpRequest",
    "typeVersion": 4.2, "position": [-140, 0], "onError": "continueRegularOutput",
}
n_http_himalayas = {
    "parameters": {
        "url": "https://himalayas.app/jobs/api",
        "sendQuery": True,
        "queryParameters": {"parameters": [{"name": "limit", "value": "20"}]},
        "options": {"response": {"response": {"responseFormat": "json"}}},
    },
    # Publica, sem auth (himalayas.app/docs/remote-jobs-api). Já vem com moeda
    # por vaga — cobre USD/EUR/etc sem precisar de heurística. onError: mesmo
    # motivo do node RemoteOK acima.
    "id": nid(), "name": "Himalayas API", "type": "n8n-nodes-base.httpRequest",
    "typeVersion": 4.2, "position": [-140, 140], "onError": "continueRegularOutput",
}
n_http_adzuna_us = {
    "parameters": {
        "url": "https://api.adzuna.com/v1/api/jobs/us/search/1",
        "sendQuery": True,
        "queryParameters": {"parameters": [
            {"name": "app_id", "value": "={{ $env.ADZUNA_APP_ID }}"},
            {"name": "app_key", "value": "={{ $env.ADZUNA_APP_KEY }}"},
            {"name": "results_per_page", "value": "20"},
            {"name": "content-type", "value": "application/json"},
            {"name": "what", "value": "automation"},
        ]},
        "options": {"response": {"response": {"responseFormat": "json"}}},
    },
    # Precisa de app_id/app_key grátis (developer.adzuna.com/signup) — não é
    # scraping, é registro de app como o próprio Adzuna espera. País "us" =
    # mercado em USD; ajuste "what" se quiser outras keywords além de automation.
    # onError: sem isso, falha de auth aqui (ex: sem app_id/app_key) para a
    # execução inteira e derruba RemoteOK/Himalayas também, já que estão antes
    # na mesma cadeia — com "continueRegularOutput" o Code node só vê essa
    # fonte vazia e segue com as outras 3.
    "id": nid(), "name": "Adzuna US", "type": "n8n-nodes-base.httpRequest",
    "typeVersion": 4.2, "position": [-140, 280], "onError": "continueRegularOutput",
}
n_http_adzuna_de = {
    "parameters": {
        "url": "https://api.adzuna.com/v1/api/jobs/de/search/1",
        "sendQuery": True,
        "queryParameters": {"parameters": [
            {"name": "app_id", "value": "={{ $env.ADZUNA_APP_ID }}"},
            {"name": "app_key", "value": "={{ $env.ADZUNA_APP_KEY }}"},
            {"name": "results_per_page", "value": "20"},
            {"name": "content-type", "value": "application/json"},
            {"name": "what", "value": "automation"},
        ]},
        "options": {"response": {"response": {"responseFormat": "json"}}},
    },
    # Mesma API, país "de" (Alemanha) = mercado em EUR. Mesmas credenciais do
    # node acima (app_id/app_key são globais na conta Adzuna, não por país).
    # onError: mesmo motivo do node "Adzuna US" acima.
    "id": nid(), "name": "Adzuna DE", "type": "n8n-nodes-base.httpRequest",
    "typeVersion": 4.2, "position": [-140, 420], "onError": "continueRegularOutput",
}
n_get_missions = {
    "parameters": {
        "url": "={{ $env.DASHBOARD_API_URL }}/missions",
        "options": {"response": {"response": {"responseFormat": "json"}}},
    },
    "id": nid(), "name": "Get Existing Missions", "type": "n8n-nodes-base.httpRequest",
    "typeVersion": 4.2, "position": [-30, 200],
}
n_score = {
    "parameters": {"jsCode": discovery_code},
    "id": nid(), "name": "Filter Candidates", "type": "n8n-nodes-base.code",
    "typeVersion": 2, "position": [80, 0],
}
n_rank_guard_check, n_rank_guard_if = guard_nodes("Rank", (190, 0))
n_rank_http = {
    "parameters": {
        "method": "POST",
        "url": "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent",
        "sendHeaders": True,
        "headerParameters": {"parameters": [
            {"name": "content-type", "value": "application/json"},
        ]},
        "sendBody": True,
        "specifyBody": "json",
        "jsonBody": "={{ { \"contents\": [ { \"parts\": [ { \"text\": $('Filter Candidates').item.json.rankPrompt } ] } ], \"generationConfig\": { \"maxOutputTokens\": 1536 } } }}",
        "genericAuthType": "httpHeaderAuth",
        "authentication": "genericCredentialType",
        "options": {},
    },
    "id": nid(), "name": "Rank by Fit (Gemini)", "type": "n8n-nodes-base.httpRequest",
    "typeVersion": 4.2, "position": [420, 0],
    "credentials": {"httpHeaderAuth": {"id": "WpY7l5UFa42YJTdG", "name": "Header Auth account"}},
}
n_rank_apply = {
    "parameters": {"jsCode": rank_apply_code},
    "id": nid(), "name": "Select by Fit", "type": "n8n-nodes-base.code",
    "typeVersion": 2, "position": [540, 0],
}
n_guard_check, n_guard_if = guard_nodes("Discovery", (650, 0))
n_http_ai = {
    "parameters": {
        "method": "POST",
        "url": "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent",
        "sendHeaders": True,
        "headerParameters": {"parameters": [
            {"name": "content-type", "value": "application/json"},
        ]},
        "sendBody": True,
        "specifyBody": "json",
        "jsonBody": "={{ { \"contents\": [ { \"parts\": [ { \"text\": $('Select by Fit').item.json.aiPrompt } ] } ], \"generationConfig\": { \"maxOutputTokens\": 1024 } } }}",
        "genericAuthType": "httpHeaderAuth",
        "authentication": "genericCredentialType",
        "options": {},
    },
    "id": nid(), "name": "Gemini — Draft", "type": "n8n-nodes-base.httpRequest",
    "typeVersion": 4.2, "position": [760, 0],
    "credentials": {"httpHeaderAuth": {"id": "WpY7l5UFa42YJTdG", "name": "Header Auth account"}},
}
n_extract = {
    "parameters": {"jsCode": extract_code},
    "id": nid(), "name": "Format Message", "type": "n8n-nodes-base.code",
    "typeVersion": 2, "position": [520, 0],
}
n_post_mission = {
    "parameters": {
        "method": "POST",
        "url": "={{ $env.DASHBOARD_API_URL }}/missions",
        "sendHeaders": True,
        "headerParameters": {"parameters": [
            {"name": "content-type", "value": "application/json"},
            {"name": "x-api-key", "value": "={{ $env.DASHBOARD_API_KEY }}"},
        ]},
        "sendBody": True,
        "specifyBody": "json",
        "jsonBody": "={{ { \"missionId\": $json.missionId, \"source\": $json.source, \"position\": $json.position, \"company\": $json.company, \"url\": $json.url, \"score\": $json.score, \"tags\": $json.tags, \"salaryMin\": $json.salaryMin, \"salaryMax\": $json.salaryMax, \"currency\": $json.currency, \"salaryPeriod\": $json.salaryPeriod, \"description\": $json.description, \"proposalText\": $json.proposalText, \"status\": \"found\" } }}",
        "options": {},
    },
    "id": nid(), "name": "Post Mission to Dashboard", "type": "n8n-nodes-base.httpRequest",
    "typeVersion": 4.2, "position": [630, 0],
}
n_telegram = {
    "parameters": {
        "chatId": "={{ $env.TELEGRAM_CHAT_ID }}",
        "text": "={{ $json.telegramText }}",
        "additionalFields": {"parse_mode": "Markdown"},
    },
    "id": nid(), "name": "Telegram — Notify", "type": "n8n-nodes-base.telegram",
    "typeVersion": 1.2, "position": [740, 0],
    "credentials": {"telegramApi": {"id": "OJb2YcyxzI8DOMlQ", "name": "Telegram account"}},
}

wf1 = {
    "id": "850914a8-85df-4d9c-befd-6887e325eee1",  # fixo p/ reimport atualizar em vez de duplicar
    "name": "Autonomo — 01 Discovery & Proposal",
    "nodes": [
        n_schedule, n_manual_schedule, n_manual_get_config, n_manual_if, n_manual_reset,
        n_http_remoteok, n_http_himalayas, n_http_adzuna_us, n_http_adzuna_de,
        n_get_missions, n_score, n_rank_guard_check, n_rank_guard_if, n_rank_http, n_rank_apply,
        n_guard_check, n_guard_if, n_http_ai, n_extract, n_post_mission, n_telegram,
    ],
    "connections": {
        "Poll Manual Trigger (1min)": {"main": [[{"node": "Get Config", "type": "main", "index": 0}]]},
        "Get Config": {"main": [[{"node": "Force Discovery?", "type": "main", "index": 0}]]},
        "Force Discovery?": {"main": [[{"node": "Reset Force Discovery Flag", "type": "main", "index": 0}], []]},
        "Reset Force Discovery Flag": {"main": [[{"node": "RemoteOK API", "type": "main", "index": 0}]]},
        "Every 2h": {"main": [[{"node": "RemoteOK API", "type": "main", "index": 0}]]},
        # Cadeia linear só pra ordenar a execução — "Filter Candidates" lê
        # cada fonte por nome via $('NodeName'), não pela conexão direta
        # (mesmo padrão já usado pra ler '$('RemoteOK API')' de dentro de um
        # node mais à frente na cadeia). Evita precisar de um node Merge.
        "RemoteOK API": {"main": [[{"node": "Himalayas API", "type": "main", "index": 0}]]},
        "Himalayas API": {"main": [[{"node": "Adzuna US", "type": "main", "index": 0}]]},
        "Adzuna US": {"main": [[{"node": "Adzuna DE", "type": "main", "index": 0}]]},
        "Adzuna DE": {"main": [[{"node": "Get Existing Missions", "type": "main", "index": 0}]]},
        "Get Existing Missions": {"main": [[{"node": "Filter Candidates", "type": "main", "index": 0}]]},
        # Ranking por IA: 1 chamada em lote (barato) pra escolher os melhores
        # candidatos antes de gastar 1 chamada de IA por proposta individual.
        "Filter Candidates": {"main": [[{"node": "Rank — Guard Check", "type": "main", "index": 0}]]},
        "Rank — Guard Check": {"main": [[{"node": "Rank — Guard OK?", "type": "main", "index": 0}]]},
        "Rank — Guard OK?": {"main": [[{"node": "Rank by Fit (Gemini)", "type": "main", "index": 0}], []]},
        "Rank by Fit (Gemini)": {"main": [[{"node": "Select by Fit", "type": "main", "index": 0}]]},
        "Select by Fit": {"main": [[{"node": "Discovery — Guard Check", "type": "main", "index": 0}]]},
        "Discovery — Guard Check": {"main": [[{"node": "Discovery — Guard OK?", "type": "main", "index": 0}]]},
        "Discovery — Guard OK?": {"main": [[{"node": "Gemini — Draft", "type": "main", "index": 0}], []]},
        "Gemini — Draft": {"main": [[{"node": "Format Message", "type": "main", "index": 0}]]},
        "Format Message": {"main": [[{"node": "Post Mission to Dashboard", "type": "main", "index": 0}]]},
        "Post Mission to Dashboard": {"main": [[{"node": "Telegram — Notify", "type": "main", "index": 0}]]},
    },
    "active": False, "settings": {"executionOrder": "v1"}, "pinData": {},
}

# ----------------------------------------------------------------------------
# WORKFLOW 2 — Execução + Rascunho de entrega
# Schedule (1 min) -> HTTP (Telegram getUpdates, polling) -> Code(filtra /exec,
#   monta prompt) -> HTTP (Gemini) -> Code(extrai) -> Telegram (devolve
#   rascunho p/ revisão). Em paralelo, confirma as mensagens lidas.
#
# Por que polling e não "Telegram Trigger": o trigger nativo do n8n exige um
# webhook público em HTTPS. Esse motor roda propositalmente sem exposição à
# internet (só acesso por túnel SSH) — então usamos getUpdates.
#
# Por que sem static data: essa versão do n8n não persiste o "static data" do
# workflow entre execuções agendadas (fica sempre vazio no próximo ciclo), então
# não dá pra guardar o offset do nosso lado. Em vez disso usamos o próprio
# mecanismo do Telegram: ao chamar getUpdates com um offset, o servidor do
# Telegram marca aquelas mensagens como lidas e nunca mais as reenvia — não
# precisamos lembrar de nada entre execuções. A cada ciclo: (1) busca mensagens
# pendentes com offset=0, (2) processa os /exec, (3) em paralelo, confirma a
# leitura de tudo que veio (mesmo o que não era /exec) chamando getUpdates de
# novo com offset = maior update_id + 1, descartando a resposta.
# ----------------------------------------------------------------------------

build_poll_url = r"""
const token = $env.TELEGRAM_BOT_TOKEN;
return [{ json: { pollUrl: "https://api.telegram.org/bot" + token + "/getUpdates?timeout=0&offset=0" } }];
"""

poll_parse = r"""
// Le a resposta original do "Get Telegram Updates" (nao a do "Acknowledge
// Updates", que roda antes deste node na mesma linha e cuja resposta chega
// aqui como input, mas nao interessa).
const resp = $('Get Telegram Updates').first().json;
const updates = (resp && Array.isArray(resp.result)) ? resp.result : [];
const out = [];

for (const u of updates) {
  const msg = u.message;
  const text = msg && msg.text ? msg.text : "";
  if (!/^\/exec\b/i.test(text)) continue;          // ignora tudo que não for /exec

  const brief = text.replace(/^\/exec\s*/i, "").trim();
  const chatId = msg.chat ? msg.chat.id : null;
  if (!brief || !chatId) continue;

  const aiPrompt =
`Você é um profissional entregando um trabalho freelance. Produza a ENTREGA em si
(não uma proposta), pronta para revisão humana antes de enviar ao cliente.
Se for redação, entregue o texto. Se for auditoria/análise, entregue as conclusões
estruturadas. Seja concreto e utilizável. Marque com [VERIFICAR] qualquer ponto que
dependa de dado que você não tem, para o revisor conferir.

Tarefa do cliente:
${brief}`;

  out.push({ json: { chatId, aiPrompt } });
}

return out;
"""

build_ack_url = r"""
const token = $env.TELEGRAM_BOT_TOKEN;
const resp = $input.first().json;
const updates = (resp && Array.isArray(resp.result)) ? resp.result : [];

let maxId = 0;
for (const u of updates) {
  if (typeof u.update_id === "number" && u.update_id + 1 > maxId) maxId = u.update_id + 1;
}
if (maxId === 0) return [];   // nada pendente, não precisa confirmar nada

return [{ json: { ackUrl: "https://api.telegram.org/bot" + token + "/getUpdates?timeout=0&offset=" + maxId } }];
"""

exec_extract = r"""
const r = $input.first().json;
let text = "";
const parts = r.candidates && r.candidates[0] && r.candidates[0].content
  ? r.candidates[0].content.parts : null;
if (Array.isArray(parts)) {
  text = parts.map(p => p.text || "").join("\n").trim();
}
if (!text) text = "(sem retorno da IA)";

// O nó do Telegram sempre manda com parse_mode Markdown (não dá pra desligar),
// então escapamos os caracteres especiais do texto gerado pela IA para nunca
// quebrar o parser do Telegram.
function escMd(s) {
  return String(s == null ? "" : s).replace(/([_*`\[])/g, "\\$1");
}

const chatId = $('Parse /exec Commands').item.json.chatId;
return [{ json: { chatId, telegramText: "🛠️ *Rascunho de entrega* (revise antes de enviar):\n\n" + escMd(text) } }];
"""

# ----------------------------------------------------------------------------
# Sub-fluxo: missões marcadas como "applied" no dashboard são executadas
# automaticamente pela IA e devolvidas como "delivered" para revisão humana.
# Roda numa trigger de agenda própria (não encadeada com o fluxo do Telegram
# Trigger), pra não sofrer com erro em execução paralela na mesma run.
# ----------------------------------------------------------------------------

mission_prep = r"""
const resp = $input.first().json;
const missions = (resp && Array.isArray(resp.missions)) ? resp.missions : [];
const applied = missions.filter(m => m.status === "applied");
if (applied.length === 0) return [];

// processa só 1 por ciclo, pra não estourar cota de IA de uma vez
const m = applied[0];

const aiPrompt =
`Você é um profissional entregando um trabalho freelance para o qual já foi
contratado. Produza a ENTREGA em si (não uma proposta), pronta para revisão
humana antes de enviar ao cliente. Se for redação, entregue o texto. Se for
auditoria/análise, entregue as conclusões estruturadas. Seja concreto e
utilizável. Marque com [VERIFICAR] qualquer ponto que dependa de dado que
você não tem, para o revisor conferir.

Vaga: ${m.position} @ ${m.company || "?"}
Descrição: ${m.description || "(sem descrição)"}
Proposta enviada anteriormente: ${m.proposalText || "(sem proposta salva)"}`;

return [{ json: { missionId: m.missionId, position: m.position, aiPrompt } }];
"""

mission_deliver_extract = r"""
const r = $input.first().json;
let text = "";
const parts = r.candidates && r.candidates[0] && r.candidates[0].content
  ? r.candidates[0].content.parts : null;
if (Array.isArray(parts)) {
  text = parts.map(p => p.text || "").join("\n").trim();
}
if (!text) text = "(sem retorno da IA)";

function escMd(s) {
  return String(s == null ? "" : s).replace(/([_*`\[])/g, "\\$1");
}

const missionId = $('Find Applied Mission').item.json.missionId;
const position = $('Find Applied Mission').item.json.position;
const telegramText = "✅ *Entrega pronta pra revisão:* " + escMd(position) + "\nConfira e aprove no dashboard.";
return [{ json: { missionId, deliveryText: text, telegramText } }];
"""

t_schedule = {
    "parameters": {"rule": {"interval": [{"field": "minutes", "minutesInterval": 1}]}},
    "id": nid(), "name": "Poll Telegram (1min)", "type": "n8n-nodes-base.scheduleTrigger",
    "typeVersion": 1.2, "position": [-800, 0],
}
t_build_url = {
    "parameters": {"jsCode": build_poll_url},
    "id": nid(), "name": "Build Poll URL", "type": "n8n-nodes-base.code",
    "typeVersion": 2, "position": [-580, 0],
}
t_poll = {
    "parameters": {
        "url": "={{ $json.pollUrl }}",
        "options": {"response": {"response": {"responseFormat": "json"}}},
    },
    "id": nid(), "name": "Get Telegram Updates", "type": "n8n-nodes-base.httpRequest",
    "typeVersion": 4.2, "position": [-360, 0],
}
t_prep = {
    "parameters": {"jsCode": poll_parse},
    "id": nid(), "name": "Parse /exec Commands", "type": "n8n-nodes-base.code",
    "typeVersion": 2, "position": [-140, 0],
}
t_build_ack = {
    "parameters": {"jsCode": build_ack_url},
    "id": nid(), "name": "Build Ack URL", "type": "n8n-nodes-base.code",
    "typeVersion": 2, "position": [-140, 140],
}
t_ack = {
    "parameters": {
        "url": "={{ $json.ackUrl }}",
        "options": {"response": {"response": {"responseFormat": "json"}}},
    },
    "id": nid(), "name": "Acknowledge Updates", "type": "n8n-nodes-base.httpRequest",
    "typeVersion": 4.2, "position": [80, 140],
}
t_guard_check, t_guard_if = guard_nodes("Exec", (180, -60))
t_http_ai = {
    "parameters": {
        "method": "POST",
        "url": "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent",
        "sendHeaders": True,
        "headerParameters": {"parameters": [
            {"name": "content-type", "value": "application/json"},
        ]},
        "sendBody": True, "specifyBody": "json",
        "jsonBody": "={{ { \"contents\": [ { \"parts\": [ { \"text\": $('Parse /exec Commands').item.json.aiPrompt } ] } ], \"generationConfig\": { \"maxOutputTokens\": 2048 } } }}",
        "genericAuthType": "httpHeaderAuth", "authentication": "genericCredentialType",
        "options": {},
    },
    "id": nid(), "name": "Gemini — Execute", "type": "n8n-nodes-base.httpRequest",
    "typeVersion": 4.2, "position": [400, -60],
    "credentials": {"httpHeaderAuth": {"id": "WpY7l5UFa42YJTdG", "name": "Header Auth account"}},
}
t_extract = {
    "parameters": {"jsCode": exec_extract},
    "id": nid(), "name": "Format Delivery", "type": "n8n-nodes-base.code",
    "typeVersion": 2, "position": [520, -60],
}
t_reply = {
    "parameters": {
        "chatId": "={{ $json.chatId }}", "text": "={{ $json.telegramText }}",
        "additionalFields": {"parse_mode": "Markdown"},
    },
    "id": nid(), "name": "Telegram — Reply", "type": "n8n-nodes-base.telegram",
    "typeVersion": 1.2, "position": [740, -60],
    "credentials": {"telegramApi": {"id": "OJb2YcyxzI8DOMlQ", "name": "Telegram account"}},
}

m_schedule = {
    "parameters": {"rule": {"interval": [{"field": "minutes", "minutesInterval": 1}]}},
    "id": nid(), "name": "Poll Applied Missions (1min)", "type": "n8n-nodes-base.scheduleTrigger",
    "typeVersion": 1.2, "position": [-800, 300],
}
m_get_missions = {
    "parameters": {
        "url": "={{ $env.DASHBOARD_API_URL }}/missions",
        "options": {"response": {"response": {"responseFormat": "json"}}},
    },
    "id": nid(), "name": "Get All Missions", "type": "n8n-nodes-base.httpRequest",
    "typeVersion": 4.2, "position": [-580, 300],
}
m_prep = {
    "parameters": {"jsCode": mission_prep},
    "id": nid(), "name": "Find Applied Mission", "type": "n8n-nodes-base.code",
    "typeVersion": 2, "position": [-360, 300],
}
m_guard_check, m_guard_if = guard_nodes("Mission", (-250, 300))
# Trava a missao como "in_progress" assim que o Guard libera, ANTES de chamar a
# IA (que pode demorar). Sem isso, se um ciclo passar de 1min (ex.: Gemini
# lento/retry), o proximo poll pega a mesma missao "applied" de novo -> chamada
# de IA duplicada e notificacao duplicada no Telegram (mesma classe de bug ja
# corrigida pro polling do Telegram no commit 1b482e6). "Find Applied Mission"
# so seleciona status === "applied", entao marcar "in_progress" aqui tira a
# missao da fila do proximo poll.
m_mark_progress = {
    "parameters": {
        "method": "PATCH",
        "url": "={{ $env.DASHBOARD_API_URL + \"/missions/\" + $('Find Applied Mission').item.json.missionId }}",
        "sendHeaders": True,
        "headerParameters": {"parameters": [
            {"name": "content-type", "value": "application/json"},
            {"name": "x-api-key", "value": "={{ $env.DASHBOARD_API_KEY }}"},
        ]},
        "sendBody": True, "specifyBody": "json",
        "jsonBody": "={{ { \"status\": \"in_progress\" } }}",
        "options": {},
    },
    "id": nid(), "name": "Mark Mission In Progress", "type": "n8n-nodes-base.httpRequest",
    "typeVersion": 4.2, "position": [-140, 300],
}
m_http_ai = {
    "parameters": {
        "method": "POST",
        "url": "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent",
        "sendHeaders": True,
        "headerParameters": {"parameters": [
            {"name": "content-type", "value": "application/json"},
        ]},
        "sendBody": True, "specifyBody": "json",
        "jsonBody": "={{ { \"contents\": [ { \"parts\": [ { \"text\": $('Find Applied Mission').item.json.aiPrompt } ] } ], \"generationConfig\": { \"maxOutputTokens\": 2048 } } }}",
        "genericAuthType": "httpHeaderAuth", "authentication": "genericCredentialType",
        "options": {},
    },
    "id": nid(), "name": "Gemini — Execute Mission", "type": "n8n-nodes-base.httpRequest",
    "typeVersion": 4.2, "position": [-30, 300],
    "credentials": {"httpHeaderAuth": {"id": "WpY7l5UFa42YJTdG", "name": "Header Auth account"}},
}
m_extract = {
    "parameters": {"jsCode": mission_deliver_extract},
    "id": nid(), "name": "Format Mission Delivery", "type": "n8n-nodes-base.code",
    "typeVersion": 2, "position": [80, 300],
}
m_update = {
    "parameters": {
        "method": "PATCH",
        "url": "={{ $env.DASHBOARD_API_URL + \"/missions/\" + $json.missionId }}",
        "sendHeaders": True,
        "headerParameters": {"parameters": [
            {"name": "content-type", "value": "application/json"},
            {"name": "x-api-key", "value": "={{ $env.DASHBOARD_API_KEY }}"},
        ]},
        "sendBody": True, "specifyBody": "json",
        "jsonBody": "={{ { \"status\": \"delivered\", \"deliveryText\": $json.deliveryText } }}",
        "options": {},
    },
    "id": nid(), "name": "Update Mission Delivered", "type": "n8n-nodes-base.httpRequest",
    "typeVersion": 4.2, "position": [300, 300],
}
m_telegram = {
    "parameters": {
        "chatId": "={{ $env.TELEGRAM_CHAT_ID }}",
        "text": "={{ $json.telegramText }}",
        "additionalFields": {"parse_mode": "Markdown"},
    },
    "id": nid(), "name": "Telegram — Notify Delivery", "type": "n8n-nodes-base.telegram",
    "typeVersion": 1.2, "position": [520, 300],
    "credentials": {"telegramApi": {"id": "OJb2YcyxzI8DOMlQ", "name": "Telegram account"}},
}

wf2 = {
    "id": "af758c9f-ea17-4523-b38a-a1a314f56a47",  # fixo p/ reimport atualizar em vez de duplicar
    "name": "Autonomo — 02 Execute & Deliver",
    "nodes": [
        t_schedule, t_build_url, t_poll, t_prep, t_build_ack, t_ack, t_guard_check, t_guard_if, t_http_ai, t_extract, t_reply,
        m_schedule, m_get_missions, m_prep, m_guard_check, m_guard_if, m_mark_progress, m_http_ai, m_extract, m_update, m_telegram,
    ],
    "connections": {
        "Poll Telegram (1min)": {"main": [[{"node": "Build Poll URL", "type": "main", "index": 0}]]},
        "Build Poll URL": {"main": [[{"node": "Get Telegram Updates", "type": "main", "index": 0}]]},
        "Get Telegram Updates": {"main": [[{"node": "Build Ack URL", "type": "main", "index": 0}]]},
        "Build Ack URL": {"main": [[{"node": "Acknowledge Updates", "type": "main", "index": 0}]]},
        "Acknowledge Updates": {"main": [[{"node": "Parse /exec Commands", "type": "main", "index": 0}]]},
        "Parse /exec Commands": {"main": [[{"node": "Exec — Guard Check", "type": "main", "index": 0}]]},
        "Exec — Guard Check": {"main": [[{"node": "Exec — Guard OK?", "type": "main", "index": 0}]]},
        "Exec — Guard OK?": {"main": [[{"node": "Gemini — Execute", "type": "main", "index": 0}], []]},
        "Gemini — Execute": {"main": [[{"node": "Format Delivery", "type": "main", "index": 0}]]},
        "Format Delivery": {"main": [[{"node": "Telegram — Reply", "type": "main", "index": 0}]]},

        "Poll Applied Missions (1min)": {"main": [[{"node": "Get All Missions", "type": "main", "index": 0}]]},
        "Get All Missions": {"main": [[{"node": "Find Applied Mission", "type": "main", "index": 0}]]},
        "Find Applied Mission": {"main": [[{"node": "Mission — Guard Check", "type": "main", "index": 0}]]},
        "Mission — Guard Check": {"main": [[{"node": "Mission — Guard OK?", "type": "main", "index": 0}]]},
        "Mission — Guard OK?": {"main": [[{"node": "Mark Mission In Progress", "type": "main", "index": 0}], []]},
        "Mark Mission In Progress": {"main": [[{"node": "Gemini — Execute Mission", "type": "main", "index": 0}]]},
        "Gemini — Execute Mission": {"main": [[{"node": "Format Mission Delivery", "type": "main", "index": 0}]]},
        "Format Mission Delivery": {"main": [[{"node": "Update Mission Delivered", "type": "main", "index": 0}]]},
        "Update Mission Delivered": {"main": [[{"node": "Telegram — Notify Delivery", "type": "main", "index": 0}]]},
    },
    "active": False, "settings": {"executionOrder": "v1"}, "pinData": {},
}

# ----------------------------------------------------------------------------
# WORKFLOW 3 — Descoberta + Bid no Freelancer.com (via API oficial)
#
# Diferente do WF01 (RemoteOK): lá a candidatura é sempre manual, na fonte,
# porque não existe API pública de bid. No Freelancer.com existe API oficial
# de bid (developers.freelancer.com) — então aqui o "aplicar" pode ser
# automatizado, mas SEM abrir mão do gargalo humano: o bid só é submetido
# depois que você aprova explicitamente no dashboard (status "bid_approved").
# Nunca envia bid sozinho a partir da descoberta.
#
# ATENCAO — validar antes de ativar: os endpoints/nomes de parametro abaixo
# foram levantados via pesquisa (nao ha docs publicas sem login em
# developers.freelancer.com). Confira contra a doc oficial da sua app antes
# de rodar de verdade:
#   - Auth: header customizado "freelancer-oauth-v1: <token>" (NAO e
#     "Authorization: Bearer"). Gere um token pessoal em developers.freelancer.com
#     pra sua propria conta (evita implementar o fluxo OAuth2 completo aqui).
#   - Busca: GET /api/projects/0.1/projects/active?query=<texto>&compact=true
#     (client-side scoring depois, igual ao WF01 — mais robusto do que confiar
#     em filtro server-side que pode exigir job IDs em vez de texto livre).
#   - Bid: POST /api/projects/0.1/bids/ com {project_id, amount, period,
#     description}.
# ----------------------------------------------------------------------------

freelancer_discovery_code = r"""
// ===== CONFIG (edite aqui) ==================================================
const KEYWORDS   = ["automation", "n8n", "ai", "python", "react", "workflow"];
const MAX_PROPOSALS_PER_RUN = 5;   // Guard: teto de chamadas de IA por execução
const BID_PERIOD_DAYS = 7;         // prazo de entrega sugerido no bid
// ============================================================================

const raw = $('Freelancer Search').first().json;
let projects = Array.isArray(raw && raw.result && raw.result.projects) ? raw.result.projects : [];

const missionsResp = $input.first().json;
const existing = (missionsResp && Array.isArray(missionsResp.missions)) ? missionsResp.missions : [];
const existingIds = new Set(existing.map(m => m.missionId));

function scoreProject(p) {
  const hay = ((p.title || "") + " " + (p.preview_description || "")).toLowerCase();
  let score = 0;
  for (const k of KEYWORDS) if (hay.includes(k.toLowerCase())) score += 1;
  return score;
}

const fresh = [];
for (const p of projects) {
  if (!p || !p.id || !p.title) continue;
  if (existingIds.has("freelancer-" + p.id)) continue;
  const score = scoreProject(p);
  if (score === 0) continue;
  fresh.push({ project: p, score });
}

fresh.sort((a, b) => b.score - a.score);
const chosen = fresh.slice(0, MAX_PROPOSALS_PER_RUN);

const out = [];
for (const c of chosen) {
  const p = c.project;
  const budget = p.budget || {};
  const budgetMin = budget.minimum || 0;
  const budgetMax = budget.maximum || 0;
  // heurística simples de lance inicial: teto do orçamento do cliente, ou o
  // mínimo se não tiver máximo — ajuste depois de ver os primeiros resultados.
  const bidAmount = budgetMax || budgetMin || 0;
  const descr = String(p.preview_description || "").slice(0, 1800);
  const url = "https://www.freelancer.com/projects/" + (p.seo_url || p.id);

  const aiPrompt =
`Você é o Felipe, um desenvolvedor e fundador de SaaS no Brasil, escrevendo um bid
curto e humano para um projeto no Freelancer.com. Escreva em INGLÊS, no máximo
130 palavras, tom direto e confiante, sem clichês de "I am excited".
Abra com uma frase que mostre que você entendeu o problema específico do cliente.
Cite 1 resultado concreto que você entregaria. Feche com uma pergunta aberta.
NÃO invente experiências específicas; fale de capacidade, não de histórico falso.

Projeto: ${p.title}
Orçamento: ${budgetMin}-${budgetMax} ${budget.currency_code || ""}
Descrição: ${descr}`;

  out.push({
    json: {
      projectId: p.id,
      title: p.title,
      url,
      score: c.score,
      budgetMin, budgetMax,
      currency: budget.currency_code || "USD",
      bidAmount,
      bidPeriod: BID_PERIOD_DAYS,
      description: descr,
      aiPrompt,
    }
  });
}

return out;
"""

freelancer_extract_code = r"""
const r = $input.first().json;
let text = "";
try {
  const parts = r.candidates && r.candidates[0] && r.candidates[0].content
    ? r.candidates[0].content.parts : null;
  if (Array.isArray(parts)) {
    text = parts.map(p => p.text || "").join("\n").trim();
  }
} catch (e) { text = ""; }
if (!text) text = "(a IA não retornou texto — verifique a credencial/o modelo/a cota gratuita)";

function escMd(s) {
  return String(s == null ? "" : s).replace(/([_*`\[])/g, "\\$1");
}

const meta = $('Score & Draft Bid').item.json;

const msg =
`💼 *Novo projeto (Freelancer.com)* (score ${meta.score})\n` +
`*${escMd(meta.title)}*\n` +
`🔗 ${meta.url}\n` +
`💰 ${meta.currency} ${meta.budgetMin}-${meta.budgetMax} · lance sugerido ${meta.currency} ${meta.bidAmount}\n\n` +
`✍️ *Rascunho de bid:*\n${escMd(text)}\n\n` +
`➡️ Aprove no dashboard pra enviar o bid via API (não sai sozinho daqui).`;

return [{ json: {
  telegramText: msg,
  missionId: "freelancer-" + meta.projectId,
  platformProjectId: meta.projectId,
  title: meta.title,
  position: meta.title,
  url: meta.url,
  score: meta.score,
  budgetMin: meta.budgetMin,
  budgetMax: meta.budgetMax,
  currency: meta.currency,
  bidAmount: meta.bidAmount,
  bidPeriod: meta.bidPeriod,
  description: meta.description,
  proposalText: text,
} }];
"""

freelancer_find_approved = r"""
const resp = $input.first().json;
const missions = (resp && Array.isArray(resp.missions)) ? resp.missions : [];
const approved = missions.filter(m => m.status === "bid_approved" && m.source === "freelancer");
if (approved.length === 0) return [];

// processa só 1 por ciclo, mesmo racional do WF02: nao acumula custo/risco de
// uma vez, e mantém previsível qual bid foi enviado quando.
const m = approved[0];
return [{ json: {
  missionId: m.missionId,
  platformProjectId: m.platformProjectId,
  bidAmount: m.bidAmount,
  bidPeriod: m.bidPeriod || 7,
  proposalText: m.proposalText || "",
  title: m.position || m.title,
} }];
"""

freelancer_bid_format = r"""
const src = $('Find Approved Bid').item.json;
const telegramText = "📤 *Bid enviado:* " + src.title + "\nAcompanhe a resposta do cliente na plataforma.";
return [{ json: { missionId: src.missionId, telegramText } }];
"""

f_schedule = {
    "parameters": {"rule": {"interval": [{"field": "hours", "hoursInterval": 2}]}},
    "id": nid(), "name": "Every 2h (Freelancer)", "type": "n8n-nodes-base.scheduleTrigger",
    "typeVersion": 1.2, "position": [-360, 600],
}
f_search = {
    "parameters": {
        "url": "https://www.freelancer.com/api/projects/0.1/projects/active",
        "sendQuery": True,
        "queryParameters": {"parameters": [
            {"name": "query", "value": "automation"},
            {"name": "compact", "value": "true"},
        ]},
        "sendHeaders": True,
        "headerParameters": {"parameters": [
            {"name": "freelancer-oauth-v1", "value": "={{ $env.FREELANCER_API_TOKEN }}"},
        ]},
        "options": {"response": {"response": {"responseFormat": "json"}}},
    },
    "id": nid(), "name": "Freelancer Search", "type": "n8n-nodes-base.httpRequest",
    "typeVersion": 4.2, "position": [-140, 600],
}
f_get_missions = {
    "parameters": {
        "url": "={{ $env.DASHBOARD_API_URL }}/missions",
        "options": {"response": {"response": {"responseFormat": "json"}}},
    },
    "id": nid(), "name": "Get Existing Missions (F)", "type": "n8n-nodes-base.httpRequest",
    "typeVersion": 4.2, "position": [-30, 600],
}
f_score = {
    "parameters": {"jsCode": freelancer_discovery_code},
    "id": nid(), "name": "Score & Draft Bid", "type": "n8n-nodes-base.code",
    "typeVersion": 2, "position": [80, 600],
}
f_guard_check, f_guard_if = guard_nodes("Freelancer Discovery", (190, 600))
f_http_ai = {
    "parameters": {
        "method": "POST",
        "url": "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent",
        "sendHeaders": True,
        "headerParameters": {"parameters": [
            {"name": "content-type", "value": "application/json"},
        ]},
        "sendBody": True, "specifyBody": "json",
        "jsonBody": "={{ { \"contents\": [ { \"parts\": [ { \"text\": $('Score & Draft Bid').item.json.aiPrompt } ] } ], \"generationConfig\": { \"maxOutputTokens\": 1024 } } }}",
        "genericAuthType": "httpHeaderAuth", "authentication": "genericCredentialType",
        "options": {},
    },
    "id": nid(), "name": "Gemini — Draft Bid", "type": "n8n-nodes-base.httpRequest",
    "typeVersion": 4.2, "position": [300, 600],
    "credentials": {"httpHeaderAuth": {"id": "WpY7l5UFa42YJTdG", "name": "Header Auth account"}},
}
f_extract = {
    "parameters": {"jsCode": freelancer_extract_code},
    "id": nid(), "name": "Format Bid Message", "type": "n8n-nodes-base.code",
    "typeVersion": 2, "position": [520, 600],
}
f_post_mission = {
    "parameters": {
        "method": "POST",
        "url": "={{ $env.DASHBOARD_API_URL }}/missions",
        "sendHeaders": True,
        "headerParameters": {"parameters": [
            {"name": "content-type", "value": "application/json"},
            {"name": "x-api-key", "value": "={{ $env.DASHBOARD_API_KEY }}"},
        ]},
        "sendBody": True, "specifyBody": "json",
        "jsonBody": "={{ { \"missionId\": $json.missionId, \"source\": \"freelancer\", \"platformProjectId\": $json.platformProjectId, \"position\": $json.title, \"url\": $json.url, \"score\": $json.score, \"budgetMin\": $json.budgetMin, \"budgetMax\": $json.budgetMax, \"currency\": $json.currency, \"bidAmount\": $json.bidAmount, \"bidPeriod\": $json.bidPeriod, \"description\": $json.description, \"proposalText\": $json.proposalText, \"status\": \"found\" } }}",
        "options": {},
    },
    "id": nid(), "name": "Post Mission to Dashboard (F)", "type": "n8n-nodes-base.httpRequest",
    "typeVersion": 4.2, "position": [630, 600],
}
f_telegram = {
    "parameters": {
        "chatId": "={{ $env.TELEGRAM_CHAT_ID }}",
        "text": "={{ $json.telegramText }}",
        "additionalFields": {"parse_mode": "Markdown"},
    },
    "id": nid(), "name": "Telegram — Notify (F)", "type": "n8n-nodes-base.telegram",
    "typeVersion": 1.2, "position": [740, 600],
    "credentials": {"telegramApi": {"id": "OJb2YcyxzI8DOMlQ", "name": "Telegram account"}},
}

# Sub-fluxo: missões aprovadas (status "bid_approved") tem o bid submetido de
# verdade via API. So dispara depois de aprovacao humana explicita no
# dashboard — nunca a partir da descoberta.
b_schedule = {
    "parameters": {"rule": {"interval": [{"field": "minutes", "minutesInterval": 1}]}},
    "id": nid(), "name": "Poll Approved Bids (1min)", "type": "n8n-nodes-base.scheduleTrigger",
    "typeVersion": 1.2, "position": [-800, 900],
}
b_get_missions = {
    "parameters": {
        "url": "={{ $env.DASHBOARD_API_URL }}/missions",
        "options": {"response": {"response": {"responseFormat": "json"}}},
    },
    "id": nid(), "name": "Get All Missions (F)", "type": "n8n-nodes-base.httpRequest",
    "typeVersion": 4.2, "position": [-580, 900],
}
b_find = {
    "parameters": {"jsCode": freelancer_find_approved},
    "id": nid(), "name": "Find Approved Bid", "type": "n8n-nodes-base.code",
    "typeVersion": 2, "position": [-360, 900],
}
b_mark_submitting = {
    "parameters": {
        "method": "PATCH",
        "url": "={{ $env.DASHBOARD_API_URL + \"/missions/\" + $json.missionId }}",
        "sendHeaders": True,
        "headerParameters": {"parameters": [
            {"name": "content-type", "value": "application/json"},
            {"name": "x-api-key", "value": "={{ $env.DASHBOARD_API_KEY }}"},
        ]},
        "sendBody": True, "specifyBody": "json",
        "jsonBody": "={{ { \"status\": \"submitting_bid\" } }}",
        "options": {},
    },
    # Marca ANTES de chamar a API do Freelancer, mesma razão do "Mark Mission
    # In Progress" do WF02: sem isso, se o poll de 1min se sobrepuser a uma
    # chamada lenta, o mesmo bid pode ser enviado duas vezes.
    "id": nid(), "name": "Mark Submitting Bid", "type": "n8n-nodes-base.httpRequest",
    "typeVersion": 4.2, "position": [-140, 900],
}
b_submit = {
    "parameters": {
        "method": "POST",
        "url": "https://www.freelancer.com/api/projects/0.1/bids/",
        "sendHeaders": True,
        "headerParameters": {"parameters": [
            {"name": "content-type", "value": "application/json"},
            {"name": "freelancer-oauth-v1", "value": "={{ $env.FREELANCER_API_TOKEN }}"},
        ]},
        "sendBody": True, "specifyBody": "json",
        "jsonBody": "={{ { \"project_id\": $('Find Approved Bid').item.json.platformProjectId, \"amount\": $('Find Approved Bid').item.json.bidAmount, \"period\": $('Find Approved Bid').item.json.bidPeriod, \"description\": $('Find Approved Bid').item.json.proposalText } }}",
        "options": {},
    },
    "id": nid(), "name": "Submit Bid (Freelancer API)", "type": "n8n-nodes-base.httpRequest",
    "typeVersion": 4.2, "position": [80, 900],
}
b_update = {
    "parameters": {
        "method": "PATCH",
        "url": "={{ $env.DASHBOARD_API_URL + \"/missions/\" + $('Find Approved Bid').item.json.missionId }}",
        "sendHeaders": True,
        "headerParameters": {"parameters": [
            {"name": "content-type", "value": "application/json"},
            {"name": "x-api-key", "value": "={{ $env.DASHBOARD_API_KEY }}"},
        ]},
        "sendBody": True, "specifyBody": "json",
        "jsonBody": "={{ { \"status\": \"bid_submitted\" } }}",
        "options": {},
    },
    "id": nid(), "name": "Update Bid Submitted", "type": "n8n-nodes-base.httpRequest",
    "typeVersion": 4.2, "position": [300, 900],
}
b_format = {
    "parameters": {"jsCode": freelancer_bid_format},
    "id": nid(), "name": "Format Bid Notify", "type": "n8n-nodes-base.code",
    "typeVersion": 2, "position": [420, 900],
}
b_telegram = {
    "parameters": {
        "chatId": "={{ $env.TELEGRAM_CHAT_ID }}",
        "text": "={{ $json.telegramText }}",
        "additionalFields": {"parse_mode": "Markdown"},
    },
    "id": nid(), "name": "Telegram — Notify Bid Sent", "type": "n8n-nodes-base.telegram",
    "typeVersion": 1.2, "position": [540, 900],
    "credentials": {"telegramApi": {"id": "OJb2YcyxzI8DOMlQ", "name": "Telegram account"}},
}

wf3 = {
    "id": "c3f6e2a1-9b4d-4e7a-8c1f-2d6a9e4b7c05",  # fixo p/ reimport atualizar em vez de duplicar
    "name": "Autonomo — 03 Freelancer.com Discovery & Bid",
    "nodes": [
        f_schedule, f_search, f_get_missions, f_score, f_guard_check, f_guard_if, f_http_ai, f_extract, f_post_mission, f_telegram,
        b_schedule, b_get_missions, b_find, b_mark_submitting, b_submit, b_update, b_format, b_telegram,
    ],
    "connections": {
        "Every 2h (Freelancer)": {"main": [[{"node": "Freelancer Search", "type": "main", "index": 0}]]},
        "Freelancer Search": {"main": [[{"node": "Get Existing Missions (F)", "type": "main", "index": 0}]]},
        "Get Existing Missions (F)": {"main": [[{"node": "Score & Draft Bid", "type": "main", "index": 0}]]},
        "Score & Draft Bid": {"main": [[{"node": "Freelancer Discovery — Guard Check", "type": "main", "index": 0}]]},
        "Freelancer Discovery — Guard Check": {"main": [[{"node": "Freelancer Discovery — Guard OK?", "type": "main", "index": 0}]]},
        "Freelancer Discovery — Guard OK?": {"main": [[{"node": "Gemini — Draft Bid", "type": "main", "index": 0}], []]},
        "Gemini — Draft Bid": {"main": [[{"node": "Format Bid Message", "type": "main", "index": 0}]]},
        "Format Bid Message": {"main": [[{"node": "Post Mission to Dashboard (F)", "type": "main", "index": 0}]]},
        "Post Mission to Dashboard (F)": {"main": [[{"node": "Telegram — Notify (F)", "type": "main", "index": 0}]]},

        "Poll Approved Bids (1min)": {"main": [[{"node": "Get All Missions (F)", "type": "main", "index": 0}]]},
        "Get All Missions (F)": {"main": [[{"node": "Find Approved Bid", "type": "main", "index": 0}]]},
        "Find Approved Bid": {"main": [[{"node": "Mark Submitting Bid", "type": "main", "index": 0}]]},
        "Mark Submitting Bid": {"main": [[{"node": "Submit Bid (Freelancer API)", "type": "main", "index": 0}]]},
        "Submit Bid (Freelancer API)": {"main": [[{"node": "Update Bid Submitted", "type": "main", "index": 0}]]},
        "Update Bid Submitted": {"main": [[{"node": "Format Bid Notify", "type": "main", "index": 0}]]},
        "Format Bid Notify": {"main": [[{"node": "Telegram — Notify Bid Sent", "type": "main", "index": 0}]]},
    },
    "active": False, "settings": {"executionOrder": "v1"}, "pinData": {},
}

# A Lambda da dashboard-api costuma ter cold-start (~1-2s) quando fica um
# tempo sem receber chamadas -- retorna "Service Unavailable" na primeira
# tentativa nesses casos. Liga retry automatico em todo node HTTP que chama
# essa API, pra n8n tentar de novo em vez de abortar o workflow inteiro.
for wf in (wf1, wf2, wf3):
    for node in wf["nodes"]:
        if node.get("type") != "n8n-nodes-base.httpRequest":
            continue
        url = node.get("parameters", {}).get("url", "")
        if "DASHBOARD_API_URL" in url:
            node["retryOnFail"] = True
            node["maxTries"] = 3
            node["waitBetweenTries"] = 1000

with open(os.path.join(OUT, "01-discovery-and-proposal.json"), "w") as f:
    json.dump(wf1, f, indent=2, ensure_ascii=False)
with open(os.path.join(OUT, "02-execution-and-delivery.json"), "w") as f:
    json.dump(wf2, f, indent=2, ensure_ascii=False)
with open(os.path.join(OUT, "03-freelancer-discovery-and-bid.json"), "w") as f:
    json.dump(wf3, f, indent=2, ensure_ascii=False)

print("OK: workflows gerados")
for fn in ("01-discovery-and-proposal.json", "02-execution-and-delivery.json", "03-freelancer-discovery-and-bid.json"):
    p = os.path.join(OUT, fn)
    json.load(open(p))  # valida
    print("  válido:", fn, os.path.getsize(p), "bytes")
