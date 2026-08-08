# Deploy do painel (dashboard-web) no AWS Amplify Hosting

Cobre a Fase 4 do checkpoint: "consertar a IAM Role do Amplify e subir o
dashboard-web". Duas causas cobrem a esmagadora maioria dos casos de "Amplify
não builda" neste repo — vale checar as duas **antes** de mexer em IAM,
porque o erro de permissão às vezes é só sintoma da causa 1.

## Causa 1 (mais provável): root directory do monorepo

Este repo tem o frontend dentro de `dashboard-web/`, não na raiz. Se o app do
Amplify foi conectado apontando pra raiz do repo (`autonomo/`), o build falha
porque não existe `package.json` nem `amplify.yml` ali — e essa falha às vezes
aparece no console como erro genérico de permissão, não como "diretório errado".

**Fix**: Console Amplify → seu app → **App settings → Build settings → Edit** →
em *App build spec* / *Monorepo*, marque que é monorepo e defina
**App root directory = `dashboard-web`**. O `dashboard-web/amplify.yml` já
existente cobre o resto (`npm ci` → `npm run build` → artifacts em `dist/`).

## Causa 2: service role ausente/quebrada

Sintoma real de IAM: o build para na etapa de deploy com erro tipo
"not authorized to perform: amplify:...". Fix pelo console (mais simples que
recriar via CLI/CloudFormation):
1. **App settings → General → Service role** → **Edit**.
2. Se não tiver role selecionada, ou a role listada não existir mais (fica
   sinalizado), escolha **"Create and use a new service role"** — o Amplify
   cria uma role com a policy gerenciada `AdministratorAccess-Amplify`
   (permissões de deploy padrão, é a que o Amplify Console usa por padrão).
3. Salve e rode **Redeploy this version**.

Se preferir garantir a trust policy manualmente (IAM Console → Roles → a role
do Amplify → **Trust relationships**), o principal tem que ser:
```json
{
  "Effect": "Allow",
  "Principal": { "Service": "amplify.amazonaws.com" },
  "Action": "sts:AssumeRole"
}
```

## Variáveis de ambiente do build

O `dashboard-web/src/App.jsx` lê `import.meta.env.VITE_API_URL` e
`VITE_API_KEY` — o Vite só embute variáveis prefixadas com `VITE_`, e só na
hora do build (não dá pra trocar depois sem rebuildar). Configure em
**App settings → Environment variables**:
```
VITE_API_URL = <DashboardApiUrl do docs/DEPLOY_API.md, sem / no final>
VITE_API_KEY = <a mesma API key do deploy da API>
```
Lembre: isso vai pro bundle JS público (qualquer um que abrir o site vê a
chave no código-fonte). Não é um segredo de verdade — só evita escrita
casual. Não reuse essa chave pra nada que precise de proteção real.

Depois de setar as env vars, rode **Redeploy this version** (env var nova só
entra em builds novos).

## Checklist de teste
1. Abrir a URL do Amplify (`https://<branch>.<app-id>.amplifyapp.com`)
2. Deve carregar a lista de missões (vazia se a Fase 2/3 ainda não gerou
   nenhuma) — se der tela em branco ou erro de rede no console do navegador,
   confira `VITE_API_URL` (sem `/` sobrando no final quebra as rotas)
3. Marcar uma missão como "Aplicada" pelo painel → conferir no DynamoDB
   (ou via `curl .../missions`) que o `status` mudou
