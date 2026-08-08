# Deploy da API (Lambda + DynamoDB) — via AWS CloudShell

Cobre a Fase 2 do checkpoint: publicar `dashboard-api/index.mjs` como Lambda com
Function URL, e criar as tabelas `autonomo-missions` e `autonomo-config`. Usa
`template.yaml` (AWS SAM) na raiz do repo. CloudShell já vem com `aws`, `sam` e
`git` instalados — dá pra fazer tudo pelo navegador, sem instalar nada local.

**Antes de começar:** se você vai isolar o Autonomo numa conta AWS separada
(decisão do checkpoint), entre no CloudShell **dessa conta nova**, não da de
produção do AdsFlow.

## 1. Abrir o CloudShell e clonar o repo
No console AWS, ícone do CloudShell (topo, ao lado do sino) → aguarde provisionar.
```bash
git clone https://github.com/felipestollbr-cmd/autonomo.git
cd autonomo
```

## 2. Gerar a API key (a que o n8n e o dashboard vão usar)
```bash
openssl rand -hex 32
```
Guarde esse valor — vai entrar como parâmetro no deploy (não fica no código) e
depois vira `DASHBOARD_API_KEY` no n8n e `VITE_API_KEY` no build do frontend.

## 3. Deploy guiado
```bash
sam build
sam deploy --guided
```
Responda:
- **Stack Name**: `autonomo-dashboard` (ou o que preferir)
- **AWS Region**: a mesma da sua EC2 do n8n, evita latência cross-region
- **Parameter ApiKey**: cole o valor gerado no passo 2
- **Parameter DailyAiCallCap**: Enter para aceitar o padrão (60)
- **Confirm changes before deploy**: Y
- **Allow SAM CLI IAM role creation**: Y (ele cria a role da Lambda com permissão
  só nas duas tabelas — `DynamoDBCrudPolicy`, nada mais amplo)
- **Save arguments to configuration file**: Y → grava um `samconfig.toml` local
  (fica de fora do git; próximos `sam deploy` não pedem tudo de novo)

No fim, o output mostra `DashboardApiUrl` — é a base da API. Copie.

## 4. Testar
```bash
curl https://SEU_ID.lambda-url.SUA_REGIAO.on.aws/missions
# esperado: {"missions":[]}
```

## 5. Ligar o resto ao endpoint
- **n8n** (env vars do container, `~/autonomo.env` na EC2):
  `DASHBOARD_API_URL=https://SEU_ID.lambda-url.SUA_REGIAO.on.aws` (sem `/` no
  final) e `DASHBOARD_API_KEY=<a chave do passo 2>`. Depois `sudo docker restart n8n`.
- **dashboard-web** (Amplify): variáveis de build `VITE_API_URL` (mesmo valor
  acima) e `VITE_API_KEY` (mesma chave — lembre que ela vai pro bundle público,
  não é secreta de verdade; só impede escrita casual, não é controle de acesso real).

## Redeploy depois de editar `dashboard-api/index.mjs`
```bash
sam build && sam deploy
```
(sem `--guided` — já usa o `samconfig.toml` salvo)

## Se preferir não usar SAM
`template.yaml` também é CloudFormation puro (SAM é um superset). Dá pra rodar
`aws cloudformation deploy --template-file template.yaml --stack-name autonomo-dashboard
--capabilities CAPABILITY_IAM --parameter-overrides ApiKey=... DailyAiCallCap=60`,
mas sem o `sam build` você precisa empacotar a `CodeUri` manualmente antes. `sam` é
mais simples pra este caso.
