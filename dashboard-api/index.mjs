// Autonomo Dashboard API — Lambda (Node.js 20+, ESM)
// Rotas:
//   GET    /missions          lista todas as missoes (publico, so leitura)
//   POST   /missions          cria/atualiza uma missao inteira (precisa de x-api-key)
//   PATCH  /missions/{id}     atualiza campos parciais de uma missao (precisa de x-api-key)
//   GET    /config            devolve config publica (ex: link de pagamento)
//   PUT    /config            atualiza config (precisa de x-api-key)
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
      const item = { configId: "main", ...body, updatedAt: new Date().toISOString() };
      await client.send(new PutCommand({ TableName: CONFIG_TABLE, Item: item }));
      return json(200, { ok: true, config: item });
    }

    return json(404, { error: "rota nao encontrada" });
  } catch (err) {
    console.error(err);
    return json(500, { error: "erro interno", detail: String(err && err.message) });
  }
};
