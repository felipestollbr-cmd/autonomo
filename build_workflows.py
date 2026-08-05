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
#          -> HTTP (Anthropic) -> Code(extrai texto) -> Telegram
# ----------------------------------------------------------------------------

# Code node: parsing, filtro, score, dedup e montagem do prompt de proposta.
discovery_code = r"""
// ===== CONFIG (edite aqui) ==================================================
const KEYWORDS   = ["automation", "n8n", "ai", "python", "react", "workflow"];
const MIN_SALARY = 0;      // 0 = ignora salário. Ex.: 2000 (USD/ano no dado bruto)
const MAX_PROPOSALS_PER_RUN = 5;   // Guard: teto de chamadas de IA por execução
const MODEL = "claude-sonnet-5";   // troque por "claude-haiku-4-5-20251001" p/ mais barato
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
      model: MODEL,
      aiPrompt,
    }
  });
}

return out;
"""

# Code node: extrai o texto da resposta da Anthropic e monta a msg do Telegram.
extract_code = r"""
const r = $input.first().json;
let text = "";
try {
  // Formato da Messages API: { content: [ { type:'text', text:'...' } ] }
  if (Array.isArray(r.content)) {
    text = r.content.filter(b => b.type === "text").map(b => b.text).join("\n").trim();
  }
} catch (e) { text = ""; }
if (!text) text = "(a IA não retornou texto — verifique a credencial/o modelo)";

// Recupera os dados da vaga que trafegam junto (pinned via 'Merge'? não —
// aqui usamos o item anterior via $items). Como o HTTP substitui o json,
// buscamos os campos da vaga no nó de score.
const meta = $('Score & Draft Prompt').item.json;

const msg =
`🧭 *Nova vaga* (score ${meta.score})\n` +
`*${meta.position}* — ${meta.company}\n` +
`🔗 ${meta.url}\n` +
(meta.salaryMin ? `💰 ${meta.salaryMin}${meta.salaryMax ? "–"+meta.salaryMax : ""}\n` : "") +
`_via RemoteOK_\n\n` +
`✍️ *Rascunho de proposta:*\n${text}\n\n` +
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
        "url": "https://api.anthropic.com/v1/messages",
        "sendHeaders": True,
        "headerParameters": {"parameters": [
            {"name": "anthropic-version", "value": "2023-06-01"},
            {"name": "content-type", "value": "application/json"},
        ]},
        "sendBody": True,
        "specifyBody": "json",
        "jsonBody": "={{ { \"model\": $json.model, \"max_tokens\": 1024, \"messages\": [ { \"role\": \"user\", \"content\": $json.aiPrompt } ] } }}",
        "genericAuthType": "httpHeaderAuth",
        "authentication": "genericCredentialType",
        "options": {},
    },
    "id": nid(), "name": "Anthropic — Draft", "type": "n8n-nodes-base.httpRequest",
    "typeVersion": 4.2, "position": [300, 0],
    "credentials": {"httpHeaderAuth": {"id": "REPLACE", "name": "Anthropic x-api-key"}},
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
    "credentials": {"telegramApi": {"id": "REPLACE", "name": "Telegram Bot"}},
}

wf1 = {
    "name": "Autonomo — 01 Discovery & Proposal",
    "nodes": [n_schedule, n_http_remoteok, n_score, n_http_ai, n_extract, n_telegram],
    "connections": {
        "Every 2h": {"main": [[{"node": "RemoteOK API", "type": "main", "index": 0}]]},
        "RemoteOK API": {"main": [[{"node": "Score & Draft Prompt", "type": "main", "index": 0}]]},
        "Score & Draft Prompt": {"main": [[{"node": "Anthropic — Draft", "type": "main", "index": 0}]]},
        "Anthropic — Draft": {"main": [[{"node": "Format Message", "type": "main", "index": 0}]]},
        "Format Message": {"main": [[{"node": "Telegram — Notify", "type": "main", "index": 0}]]},
    },
    "active": False, "settings": {"executionOrder": "v1"}, "pinData": {},
}

# ----------------------------------------------------------------------------
# WORKFLOW 2 — Execução + Rascunho de entrega
# Telegram Trigger (comando /exec <descrição>) -> Code(monta prompt)
#   -> HTTP (Anthropic) -> Code(extrai) -> Telegram (devolve rascunho p/ revisão)
# ----------------------------------------------------------------------------

exec_prep = r"""
const MODEL = "claude-sonnet-5";
const m = $input.first().json;
// Texto após "/exec"
const raw = (m.message && m.message.text) ? m.message.text : "";
const brief = raw.replace(/^\/exec\s*/i, "").trim();
const chatId = m.message && m.message.chat ? m.message.chat.id : null;

if (!brief) {
  return [{ json: { skip: true, chatId,
    reply: "Envie: /exec <descrição da tarefa a executar>" } }];
}

const aiPrompt =
`Você é um profissional entregando um trabalho freelance. Produza a ENTREGA em si
(não uma proposta), pronta para revisão humana antes de enviar ao cliente.
Se for redação, entregue o texto. Se for auditoria/análise, entregue as conclusões
estruturadas. Seja concreto e utilizável. Marque com [VERIFICAR] qualquer ponto que
dependa de dado que você não tem, para o revisor conferir.

Tarefa do cliente:
${brief}`;

return [{ json: { skip: false, chatId, model: MODEL, aiPrompt } }];
"""

exec_extract = r"""
const r = $input.first().json;
let text = "";
if (Array.isArray(r.content)) {
  text = r.content.filter(b => b.type === "text").map(b => b.text).join("\n").trim();
}
if (!text) text = "(sem retorno da IA)";
const chatId = $('Prepare Exec').item.json.chatId;
return [{ json: { chatId, telegramText: "🛠️ *Rascunho de entrega* (revise antes de enviar):\n\n" + text } }];
"""

t_trigger = {
    "parameters": {"updates": ["message"], "additionalFields": {}},
    "id": nid(), "name": "Telegram Trigger", "type": "n8n-nodes-base.telegramTrigger",
    "typeVersion": 1.1, "position": [-360, 0],
    "credentials": {"telegramApi": {"id": "REPLACE", "name": "Telegram Bot"}},
}
t_prep = {
    "parameters": {"jsCode": exec_prep},
    "id": nid(), "name": "Prepare Exec", "type": "n8n-nodes-base.code",
    "typeVersion": 2, "position": [-140, 0],
}
t_if = {
    "parameters": {"conditions": {"options": {"caseSensitive": True, "typeValidation": "strict"},
        "conditions": [{"leftValue": "={{ $json.skip }}", "rightValue": False,
                        "operator": {"type": "boolean", "operation": "false"}}],
        "combinator": "and"}},
    "id": nid(), "name": "Has brief?", "type": "n8n-nodes-base.if",
    "typeVersion": 2, "position": [80, 0],
}
t_http_ai = {
    "parameters": {
        "method": "POST", "url": "https://api.anthropic.com/v1/messages",
        "sendHeaders": True,
        "headerParameters": {"parameters": [
            {"name": "anthropic-version", "value": "2023-06-01"},
            {"name": "content-type", "value": "application/json"},
        ]},
        "sendBody": True, "specifyBody": "json",
        "jsonBody": "={{ { \"model\": $json.model, \"max_tokens\": 2048, \"messages\": [ { \"role\": \"user\", \"content\": $json.aiPrompt } ] } }}",
        "genericAuthType": "httpHeaderAuth", "authentication": "genericCredentialType",
        "options": {},
    },
    "id": nid(), "name": "Anthropic — Execute", "type": "n8n-nodes-base.httpRequest",
    "typeVersion": 4.2, "position": [300, -60],
    "credentials": {"httpHeaderAuth": {"id": "REPLACE", "name": "Anthropic x-api-key"}},
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
    "credentials": {"telegramApi": {"id": "REPLACE", "name": "Telegram Bot"}},
}

wf2 = {
    "name": "Autonomo — 02 Execute & Deliver",
    "nodes": [t_trigger, t_prep, t_if, t_http_ai, t_extract, t_reply],
    "connections": {
        "Telegram Trigger": {"main": [[{"node": "Prepare Exec", "type": "main", "index": 0}]]},
        "Prepare Exec": {"main": [[{"node": "Has brief?", "type": "main", "index": 0}]]},
        "Has brief?": {"main": [
            [{"node": "Anthropic — Execute", "type": "main", "index": 0}],
            []
        ]},
        "Anthropic — Execute": {"main": [[{"node": "Format Delivery", "type": "main", "index": 0}]]},
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
