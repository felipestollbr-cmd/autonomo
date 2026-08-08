# SETUP — do SSH ao agente rodando

## 0. Pré-requisitos
- Instância EC2 rodando, security group já limpo (só 22 no seu IP, 80/443 opcionais).
- O arquivo `autonomo-key.pem` no seu Mac. Se perdeu, use o **AWS SSM Session Manager**
  (conecta sem chave e sem porta 22).
- Uma chave de API do Gemini (aistudio.google.com — camada gratuita).
- Um bot do Telegram (crie no `@BotFather`, guarde o token).
- A API do dashboard já deployada (`docs/DEPLOY_API.md`) — os workflows dependem
  dela pra dedup, Guard e registro de missões. Guarde a URL e a API key geradas lá.

## 1. Conectar na instância
```bash
chmod 400 ~/.ssh/autonomo-key.pem            # se der "permissions too open"
ssh -i ~/.ssh/autonomo-key.pem ec2-user@SEU_IP
```
`ec2-user` = Amazon Linux; `ubuntu` = Ubuntu. O banner no login confirma o SO.

## 2. Rodar o setup
Copie `setup-ec2.sh` para a instância (ou clone o repo nela) e:
```bash
bash setup-ec2.sh
```
Isso cria swap, instala Docker, gera a chave de criptografia do n8n em `~/autonomo.env`
e sobe o n8n **preso ao localhost**. Se ele mandar reconectar por causa do grupo
`docker`, saia e entre de novo no SSH e rode de novo (é idempotente).

## 3. Descobrir seu chat id do Telegram
Fale qualquer coisa com o seu bot, depois no navegador:
```
https://api.telegram.org/bot<SEU_TOKEN>/getUpdates
```
Pegue o número em `chat.id`. Agora edite `~/autonomo.env` na instância com as
4 variáveis que os workflows esperam:
```
TELEGRAM_BOT_TOKEN=<token do BotFather>
TELEGRAM_CHAT_ID=<chat.id de cima>
DASHBOARD_API_URL=<a DashboardApiUrl do docs/DEPLOY_API.md, sem / no final>
DASHBOARD_API_KEY=<a API key gerada no deploy da API>
```
Reinicie: `sudo docker restart n8n`.

## 4. Abrir o n8n (túnel SSH — nada exposto à internet)
No **seu Mac**:
```bash
ssh -i ~/.ssh/autonomo-key.pem -L 5678:localhost:5678 ec2-user@SEU_IP
```
Com o túnel aberto, acesse `http://localhost:5678` e crie a conta de dono.

## 5. Criar as credenciais no n8n
**Credentials → New:**
- **Header Auth** (nome: `Header Auth account 2` — é esse nome exato que os nós
  `Gemini — *` já esperam ao importar): Header Name = `x-goog-api-key`,
  Value = sua chave do Gemini (aistudio.google.com).
- **Telegram API** (nome: `Telegram account` — nome exato esperado pelos nós
  `Telegram — *`): cole o token do BotFather.

## 6. Importar os workflows
**Workflows → Import from File** → importe os JSON de `workflows/` (01, 02 e,
se for usar Freelancer.com, o 03). Se os nomes das credenciais no passo 5
baterem exatamente, o import já linka sozinho; senão, abra cada nó
`Gemini — *` / `Telegram — *` e selecione a credencial manualmente.

## 6b. Só se for ativar o WF03 (Freelancer.com)
O WF03 não usa credencial n8n pro Freelancer — ele lê o token direto de
`$env.FREELANCER_API_TOKEN` (mesmo padrão de `TELEGRAM_BOT_TOKEN`). Adicione
em `~/autonomo.env`:
```
FREELANCER_API_TOKEN=<token gerado em developers.freelancer.com pra sua conta>
```
**Antes de ativar de verdade**: confirme em developers.freelancer.com que o
header `freelancer-oauth-v1` e os endpoints `projects/0.1/projects/active` e
`projects/0.1/bids/` ainda são os corretos — foram levantados por pesquisa,
não por doc oficial acessada ao vivo (comentado no topo do bloco do WF03 em
`build_workflows.py`). Teste primeiro só a descoberta (deixe uma missão parar
em "found" e confira o rascunho no dashboard) antes de aprovar um bid de
verdade.

## 7. Ajustar suas palavras-chave
Abra `Score & Draft Prompt` (workflow 01) e edite no topo do código:
`KEYWORDS`, `MIN_SALARY`, `MAX_PROPOSALS_PER_RUN`.

## 8. Testar e ativar
- Workflow 01: clique **Execute Workflow** uma vez. Deve chegar vaga(s) + rascunho no
  Telegram. Se vier, **ative** (toggle no topo) para rodar a cada 2h.
- Workflow 02: **ative**. No Telegram, mande `/exec escreva um post de 300 palavras
  sobre X`. Deve voltar um rascunho.

## Trocar para OpenAI (opcional)
Nos nós HTTP `Gemini — *` (WF01: `Gemini — Draft`; WF02: `Gemini — Execute` e
`Gemini — Execute Mission`), mude:
- URL → `https://api.openai.com/v1/chat/completions`
- Credencial Header Auth vira `Authorization` = `Bearer SUA_CHAVE`.
- Body → `{ "model": "gpt-4o", "messages": [ { "role":"user", "content": <o aiPrompt> } ] }`
- Nos Code nodes de extração (`Format Message`, `Format Delivery`,
  `Format Mission Delivery`), troque o parsing de
  `r.candidates[0].content.parts[].text` (formato Gemini) por
  `r.choices[0].message.content` (formato OpenAI).

## Deixar acessível por webhook (fase 2)
Enquanto o acesso for por túnel SSH, o Telegram Trigger do workflow 02 funciona porque
o n8n faz **long-polling** de saída (não precisa de porta aberta). Se um dia você quiser
webhooks de entrada, aí colocamos um subdomínio + Caddy com HTTPS e reabrimos 80/443 —
peça esse trecho quando chegar a hora.

## Segurança — o que NUNCA fazer
- Não publique a porta 5678 em `0.0.0.0`. Acesso só por túnel.
- Não commite `autonomo.env` nem o `.pem` (já estão no `.gitignore`).
- Se a `N8N_ENCRYPTION_KEY` mudar, o n8n perde as credenciais salvas — guarde o
  `autonomo.env`.
