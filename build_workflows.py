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
const raw = $input.first().json;
let jobs = Array.isArray(raw) ? raw : (raw.body || []);
jobs = jobs.filter(j => j && j.id && j.position);

// Dedup entre execuções usando o static data do workflow.
const store = $getWorkflowStaticData("global");
store.seen = store.seen || {};

function scoreJob(j) {
  const hay = ((j.position||"") + " " + (j.description||"") + " " +
               (Array.isArray(j.tags) ? j.tags.join(" ") : "")).toLowerCase();
  let score = 0;
  for (const k of KEYWORDS) if (hay.includes(k.toLowerCase())) score += 1;
  return score;
}

const fresh = [];
for (const j of jobs) {
  const key = String(j.id);
  if (store.seen[key]) continue;                 // já vista antes
  const score = scoreJob(j);
  if (score === 0) continue;                     // sem match de keyword
  const salary = Number(j.salary_min || 0);
  if (MIN_SALARY > 0 && salary && salary < MIN_SALARY) continue;
  fresh.push({ job: j, score });
}

// Ordena por relevância e aplica o teto (Guard).
fresh.sort((a,b) => b.score - a.score);
const chosen = fresh.slice(0, MAX_PROPOSALS_PER_RUN);

// Marca como vistas (as escolhidas) para não repetir na próxima rodada.
for (const c of chosen) store.seen[String(c.job.id)] = Date.now();

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

return [{ json: { telegramText: msg, jobId: meta.jobId } }];
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
n_score = {
    "parameters": {"jsCode": discovery_code},
    "id": nid(), "name": "Score & Draft Prompt", "type": "n8n-nodes-base.code",
    "typeVersion": 2, "position": [80, 0],
}
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
        "jsonBody": "={{ { \"contents\": [ { \"parts\": [ { \"text\": $json.aiPrompt } ] } ], \"generationConfig\": { \"maxOutputTokens\": 1024 } } }}",
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
    "nodes": [n_schedule, n_http_remoteok, n_score, n_http_ai, n_extract, n_telegram],
    "connections": {
        "Every 2h": {"main": [[{"node": "RemoteOK API", "type": "main", "index": 0}]]},
        "RemoteOK API": {"main": [[{"node": "Score & Draft Prompt", "type": "main", "index": 0}]]},
        "Score & Draft Prompt": {"main": [[{"node": "Gemini — Draft", "type": "main", "index": 0}]]},
        "Gemini — Draft": {"main": [[{"node": "Format Message", "type": "main", "index": 0}]]},
        "Format Message": {"main": [[{"node": "Telegram — Notify", "type": "main", "index": 0}]]},
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
t_http_ai = {
    "parameters": {
        "method": "POST",
        "url": "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent",
        "sendHeaders": True,
        "headerParameters": {"parameters": [
            {"name": "content-type", "value": "application/json"},
        ]},
        "sendBody": True, "specifyBody": "json",
        "jsonBody": "={{ { \"contents\": [ { \"parts\": [ { \"text\": $json.aiPrompt } ] } ], \"generationConfig\": { \"maxOutputTokens\": 2048 } } }}",
        "genericAuthType": "httpHeaderAuth", "authentication": "genericCredentialType",
        "options": {},
    },
    "id": nid(), "name": "Gemini — Execute", "type": "n8n-nodes-base.httpRequest",
    "typeVersion": 4.2, "position": [300, -60],
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

wf2 = {
    "id": "af758c9f-ea17-4523-b38a-a1a314f56a47",  # fixo p/ reimport atualizar em vez de duplicar
    "name": "Autonomo — 02 Execute & Deliver",
    "nodes": [t_schedule, t_build_url, t_poll, t_prep, t_build_ack, t_ack, t_http_ai, t_extract, t_reply],
    "connections": {
        "Poll Telegram (1min)": {"main": [[{"node": "Build Poll URL", "type": "main", "index": 0}]]},
        "Build Poll URL": {"main": [[{"node": "Get Telegram Updates", "type": "main", "index": 0}]]},
        "Get Telegram Updates": {"main": [[{"node": "Build Ack URL", "type": "main", "index": 0}]]},
        "Build Ack URL": {"main": [[{"node": "Acknowledge Updates", "type": "main", "index": 0}]]},
        "Acknowledge Updates": {"main": [[{"node": "Parse /exec Commands", "type": "main", "index": 0}]]},
        "Parse /exec Commands": {"main": [[{"node": "Gemini — Execute", "type": "main", "index": 0}]]},
        "Gemini — Execute": {"main": [[{"node": "Format Delivery", "type": "main", "index": 0}]]},
        "Format Delivery": {"main": [[{"node": "Telegram — Reply", "type": "main", "index": 0}]]},
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
