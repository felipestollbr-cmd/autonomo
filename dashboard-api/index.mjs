// Autonomo Dashboard API — Lambda (Node.js 20+, ESM)
// Rotas:
//   GET    /missions          lista todas as missoes (publico, so leitura)
//   POST   /missions          cria/atualiza uma missao inteira (precisa de x-api-key)
//   PATCH  /missions/{id}     atualiza campos parciais de uma missao (precisa de x-api-key)
//   GET    /config            devolve config publica (ex: link de pagamento)
//   PUT    /config            atualiza config (precisa de x-api-key)
//   GET    /guard/status      le o teto diario sem incrementar (publico, so leitura)
//   POST   /guard/check       incrementa e verifica o teto diario de chamadas de IA (precisa de x-api-key)
import { DynamoDBClient } from "@aws-sdk/client-dynamodb";
import {
  DynamoDBDocumentClient,
  PutCommand,
  GetCommand,
  UpdateCommand,
  ScanCommand,
} from "@aws-sdk/lib-dynamodb";

const client = DynamoDBDocumentClient.from(new DynamoDBClient({}));
const MISSIONS_TABLE = process.env.MISSIONS_TABLE || "autonomo-missions";
const CONFIG_TABLE = process.env.CONFIG_TABLE || "autonomo-config";
const API_KEY = process.env.API_KEY;
const DAILY_AI_CALL_CAP = Number(process.env.DAILY_AI_CALL_CAP || 30);

const cors = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "content-type,x-api-key",
  "Access-Control-Allow-Methods": "GET,POST,PATCH,PUT,OPTIONS",
};

function json(statusCode, body) {
  return { statusCode, headers: { "content-type": "application/json", ...cors }, body: JSON.stringify(body) };
}

function requireApiKey(event) {
  const key = event.headers?.["x-api-key"] || event.headers?.["X-Api-Key"];
  return API_KEY && key === API_KEY;
}

export const handler = async (event) => {
  const method = event.requestContext?.http?.method || event.httpMethod;
  const rawPath = event.rawPath || event.path || "/";

  if (method === "OPTIONS") return { statusCode: 204, headers: cors, body: "" };

  try {
    // ---- /missions ----
    if (rawPath === "/missions" && method === "GET") {
      const out = await client.send(new ScanCommand({ TableName: MISSIONS_TABLE }));
      const items = (out.Items || []).sort((a, b) => (b.createdAt || "").localeCompare(a.createdAt || ""));
      return json(200, { missions: items });
    }

    if (rawPath === "/missions" && method === "POST") {
      if (!requireApiKey(event)) return json(401, { error: "unauthorized" });
      const body = JSON.parse(event.body || "{}");
      if (!body.missionId) return json(400, { error: "missionId obrigatorio" });
      const now = new Date().toISOString();
      const item = {
        ...body,
        status: body.status || "found",
        createdAt: body.createdAt || now,
        updatedAt: now,
      };
      await client.send(new PutCommand({ TableName: MISSIONS_TABLE, Item: item }));
      return json(200, { ok: true, mission: item });
    }

    // ---- /missions/{id} ----
    const m = rawPath.match(/^\/missions\/([^/]+)$/);
    if (m && method === "PATCH") {
      if (!requireApiKey(event)) return json(401, { error: "unauthorized" });
      const missionId = decodeURIComponent(m[1]);
      const body = JSON.parse(event.body || "{}");
      const fields = Object.keys(body).filter((k) => k !== "missionId");
      if (fields.length === 0) return json(400, { error: "nada para atualizar" });

      const names = {};
      const values = { ":updatedAt": new Date().toISOString() };
      const sets = ["#updatedAt = :updatedAt"];
      names["#updatedAt"] = "updatedAt";
      for (const f of fields) {
        names[`#${f}`] = f;
        values[`:${f}`] = body[f];
        sets.push(`#${f} = :${f}`);
      }
      await client.send(
        new UpdateCommand({
          TableName: MISSIONS_TABLE,
          Key: { missionId },
          UpdateExpression: "SET " + sets.join(", "),
          ExpressionAttributeNames: names,
          ExpressionAttributeValues: values,
        })
      );
      return json(200, { ok: true });
    }

    // ---- /config ----
    if (rawPath === "/config" && method === "GET") {
      const out = await client.send(new GetCommand({ TableName: CONFIG_TABLE, Key: { configId: "main" } }));
      return json(200, { config: out.Item || {} });
    }
    if (rawPath === "/config" && method === "PUT") {
      if (!requireApiKey(event)) return json(401, { error: "unauthorized" });
      const body = JSON.parse(event.body || "{}");
      // Mescla com o que ja existe -- PUT aqui e usado como partial update pelo
      // frontend (ex: so paymentInfo, ou so forceDiscovery), nunca o doc inteiro.
      // Sem o merge, cada chamada apagaria os campos que a outra tela salvou.
      const existing = await client.send(new GetCommand({ TableName: CONFIG_TABLE, Key: { configId: "main" } }));
      const item = { ...(existing.Item || {}), configId: "main", ...body, updatedAt: new Date().toISOString() };
      await client.send(new PutCommand({ TableName: CONFIG_TABLE, Item: item }));
      return json(200, { ok: true, config: item });
    }

    // ---- /guard/status ----
    // Leitura passiva do teto diario, sem incrementar -- pro dashboard mostrar
    // o consumo de IA do dia sem contar como uma chamada real. Se o registro
    // salvo for de um dia anterior, reporta zerado (o dia ainda nao começou
    // do lado do Guard, so zera de fato na proxima chamada de /guard/check).
    if (rawPath === "/guard/status" && method === "GET") {
      const today = new Date().toISOString().slice(0, 10);
      const out = await client.send(new GetCommand({ TableName: CONFIG_TABLE, Key: { configId: "guard" } }));
      const cur = out.Item || {};
      const sameDay = cur.date === today;
      const count = sameDay ? cur.count || 0 : 0;
      const cap = cur.cap || DAILY_AI_CALL_CAP;
      return json(200, { date: today, count, cap, remaining: Math.max(0, cap - count), capped: count >= cap });
    }

    // ---- /guard/check ----
    // Guard de custo: teto diario de chamadas de IA, pra nao estourar cota
    // gratuita nem gastar sem controle. Cada chamada de IA no n8n bate aqui
    // antes de executar; se o teto do dia estourou, ela pula a chamada.
    if (rawPath === "/guard/check" && method === "POST") {
      if (!requireApiKey(event)) return json(401, { error: "unauthorized" });
      const today = new Date().toISOString().slice(0, 10); // YYYY-MM-DD (UTC)
      const out = await client.send(new GetCommand({ TableName: CONFIG_TABLE, Key: { configId: "guard" } }));
      const cur = out.Item || {};
      const sameDay = cur.date === today;
      const count = (sameDay ? cur.count || 0 : 0) + 1;
      const allowed = count <= DAILY_AI_CALL_CAP;
      await client.send(
        new PutCommand({
          TableName: CONFIG_TABLE,
          Item: { configId: "guard", date: today, count, cap: DAILY_AI_CALL_CAP, updatedAt: new Date().toISOString() },
        })
      );
      return json(200, { allowed, count, cap: DAILY_AI_CALL_CAP, remaining: Math.max(0, DAILY_AI_CALL_CAP - count) });
    }

    return json(404, { error: "rota nao encontrada" });
  } catch (err) {
    console.error(err);
    return json(500, { error: "erro interno", detail: String(err && err.message) });
  }
};
