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

# Code node: parsing, filtro, score, dedup e montagem do prompt de proposta.
discovery_code = r"""
// ===== CONFIG (edite aqui) ==================================================
const KEYWORDS   = ["automation", "n8n", "ai", "python", "react", "workflow"];
const MIN_SALARY = 0;      // 0 = ignora salário. Ex.: 2000 (USD/ano no dado bruto)
const MAX_PROPOSALS_PER_RUN = 5;   // Guard: teto de chamadas de IA por execução
// ============================================================================

// RemoteOK retorna um array cujo PRIMEIRO item é um aviso legal, não uma vaga.
const raw = $('RemoteOK API').first().json;
let jobs = Array.isArray(raw) ? raw : (raw.body || []);
jobs = jobs.filter(j => j && j.id && j.position);

// Dedup usando o dashboard (DynamoDB) como fonte de verdade — essa versão do
// n8n não persiste static data entre execuções agendadas, então não dá pra
// guardar "já vistas" do lado do n8n. Em vez disso, checa quais missionIds já
// existem no dashboard antes de gerar proposta pra elas de novo.
const missionsResp = $input.first().json;
const existing = (missionsResp && Array.isArray(missionsResp.missions)) ? missionsResp.missions : [];
const existingIds = new Set(existing.map(m => m.missionId));

function scoreJob(j) {
  const hay = ((j.position||"") + " " + (j.description||"") + " " +
               (Array.isArray(j.tags) ? j.tags.join(" ") : "")).toLowerCase();
  let score = 0;
  for (const k of KEYWORDS) if (hay.includes(k.toLowerCase())) score += 1;
  return score;
}

const fresh = [];
for (const j of jobs) {
  if (existingIds.has("remoteok-" + j.id)) continue;   // já registrada no dashboard
  const score = scoreJob(j);
  if (score === 0) continue;                     // sem match de keyword
  const salary = Number(j.salary_min || 0);
  if (MIN_SALARY > 0 && salary && salary < MIN_SALARY) continue;
  fresh.push({ job: j, score });
}

// Ordena por relevância e aplica o teto (Guard).
fresh.sort((a,b) => b.score - a.score);
const chosen = fresh.slice(0, MAX_PROPOSALS_PER_RUN);

// Monta a saída: 1 item por vaga, já com o prompt de IA pronto.
const out = [];
for (const c of chosen) {
  const j = c.job;
  const url = j.url || ("https://remoteok.com/remote-jobs/" + j.id);
  const tags = Array.isArray(j.tags) ? j.tags.join(", ") : "";
  const descr = String(j.description || "").replace(/<[^>]+>/g, " ").slice(0, 1800);

  const aiPrompt =
`Você é o Felipe, um desenvolvedor e fundador de SaaS no Brasil, escrevendo uma proposta
curta e humana para se candidatar a uma vaga freelance/remota. Escreva em INGLÊS,
no máximo 130 palavras, tom direto e confiante, sem clichês de "I am excited".
Abra com uma frase que mostre que você entendeu o problema específico do cliente.
Cite 1 resultado concreto que você entregaria. Feche com uma pergunta aberta.
NÃO invente experiências específicas; fale de capacidade, não de histórico falso.

Vaga: ${j.position} @ ${j.company || "?"}
Tags: ${tags}
Descrição: ${descr}`;

  out.push({
    json: {
      jobId: j.id,
      position: j.position,
      company: j.company || "",
      url,
      score: c.score,
      tags,
      salaryMin: j.salary_min || null,
      salaryMax: j.salary_max || null,
      description: descr,
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
const meta = $('Score & Draft Prompt').item.json;

const msg =
`🧭 *Nova vaga* (score ${meta.score})\n` +
`*${escMd(meta.position)}* — ${escMd(meta.company)}\n` +
`🔗 ${meta.url}\n` +
(meta.salaryMin ? `💰 ${meta.salaryMin}${meta.salaryMax ? "–"+meta.salaryMax : ""}\n` : "") +
`_via RemoteOK_\n\n` +
`✍️ *Rascunho de proposta:*\n${escMd(text)}\n\n` +
`➡️ Se curtir, candidate-se você mesmo na fonte com esse texto.`;

return [{ json: {
  telegramText: msg,
  missionId: "remoteok-" + meta.jobId,
  position: meta.position,
  company: meta.company,
  url: meta.url,
  score: meta.score,
  tags: meta.tags,
  salaryMin: meta.salaryMin,
  salaryMax: meta.salaryMax,
  description: meta.description,
  proposalText: text,
} }];
"""

n_schedule = {
    "parameters": {"rule": {"interval": [{"field": "hours", "hoursInterval": 2}]}},
    "id": nid(), "name": "Every 2h", "type": "n8n-nodes-base.scheduleTrigger",
    "typeVersion": 1.2, "position": [-360, 0],
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
    "id": nid(), "name": "RemoteOK API", "type": "n8n-nodes-base.httpRequest",
    "typeVersion": 4.2, "position": [-140, 0],
}
n_get_missions = {
    "parameters": {
        "url": "={{ $env.DASHBOARD_API_URL }}/missions",
        "options": {"response": {"response": {"responseFormat": "json"}}},
    },
    "id": nid(), "name": "Get Existing Missions", "type": "n8n-nodes-base.httpRequest",
    "typeVersion": 4.2, "position": [-30, 0],
}
n_score = {
    "parameters": {"jsCode": discovery_code},
    "id": nid(), "name": "Score & Draft Prompt", "type": "n8n-nodes-base.code",
    "typeVersion": 2, "position": [80, 0],
}
n_guard_check, n_guard_if = guard_nodes("Discovery", (190, 0))
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
        "jsonBody": "={{ { \"contents\": [ { \"parts\": [ { \"text\": $('Score & Draft Prompt').item.json.aiPrompt } ] } ], \"generationConfig\": { \"maxOutputTokens\": 1024 } } }}",
        "genericAuthType": "httpHeaderAuth",
        "authentication": "genericCredentialType",
        "options": {},
    },
    "id": nid(), "name": "Gemini — Draft", "type": "n8n-nodes-base.httpRequest",
    "typeVersion": 4.2, "position": [300, 0],
    "credentials": {"httpHeaderAuth": {"id": "XaqRaStZpqtsnlTy", "name": "Header Auth account 2"}},
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
        "jsonBody": "={{ { \"missionId\": $json.missionId, \"source\": \"remoteok\", \"position\": $json.position, \"company\": $json.company, \"url\": $json.url, \"score\": $json.score, \"tags\": $json.tags, \"salaryMin\": $json.salaryMin, \"salaryMax\": $json.salaryMax, \"description\": $json.description, \"proposalText\": $json.proposalText, \"status\": \"found\" } }}",
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
    "credentials": {"telegramApi": {"id": "RMTEzP9OeiVy7Sms", "name": "Telegram account"}},
}

wf1 = {
    "id": "850914a8-85df-4d9c-befd-6887e325eee1",  # fixo p/ reimport atualizar em vez de duplicar
    "name": "Autonomo — 01 Discovery & Proposal",
    "nodes": [n_schedule, n_http_remoteok, n_get_missions, n_score, n_guard_check, n_guard_if, n_http_ai, n_extract, n_post_mission, n_telegram],
    "connections": {
        "Every 2h": {"main": [[{"node": "RemoteOK API", "type": "main", "index": 0}]]},
        "RemoteOK API": {"main": [[{"node": "Get Existing Missions", "type": "main", "index": 0}]]},
        "Get Existing Missions": {"main": [[{"node": "Score & Draft Prompt", "type": "main", "index": 0}]]},
        "Score & Draft Prompt": {"main": [[{"node": "Discovery — Guard Check", "type": "main", "index": 0}]]},
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
    "credentials": {"httpHeaderAuth": {"id": "XaqRaStZpqtsnlTy", "name": "Header Auth account 2"}},
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
    "credentials": {"telegramApi": {"id": "RMTEzP9OeiVy7Sms", "name": "Telegram account"}},
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
    "credentials": {"httpHeaderAuth": {"id": "XaqRaStZpqtsnlTy", "name": "Header Auth account 2"}},
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
    "credentials": {"telegramApi": {"id": "RMTEzP9OeiVy7Sms", "name": "Telegram account"}},
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

with open(os.path.join(OUT, "01-discovery-and-proposal.json"), "w") as f:
    json.dump(wf1, f, indent=2, ensure_ascii=False)
with open(os.path.join(OUT, "02-execution-and-delivery.json"), "w") as f:
    json.dump(wf2, f, indent=2, ensure_ascii=False)

print("OK: workflows gerados")
for fn in ("01-discovery-and-proposal.json", "02-execution-and-delivery.json"):
    p = os.path.join(OUT, fn)
    json.load(open(p))  # valida
    print("  válido:", fn, os.path.getsize(p), "bytes")
