# SETUP — do SSH ao agente rodando

## 0. Pré-requisitos
- Instância EC2 rodando, security group já limpo (só 22 no seu IP, 80/443 opcionais).
- O arquivo `autonomo-key.pem` no seu Mac. Se perdeu, use o **AWS SSM Session Manager**
  (conecta sem chave e sem porta 22).
- Uma chave de API da Anthropic (ou OpenAI).
- Um bot do Telegram (crie no `@BotFather`, guarde o token).

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
Pegue o número em `chat.id` e coloque em `~/autonomo.env` na linha `TELEGRAM_CHAT_ID=`.
Reinicie: `sudo docker restart n8n`.

## 4. Abrir o n8n (túnel SSH — nada exposto à internet)
No **seu Mac**:
```bash
ssh -i ~/.ssh/autonomo-key.pem -L 5678:localhost:5678 ec2-user@SEU_IP
```
Com o túnel aberto, acesse `http://localhost:5678` e crie a conta de dono.

## 5. Criar as credenciais no n8n
**Credentials → New:**
- **Header Auth** (nome: `Anthropic x-api-key`): Header Name = `x-api-key`,
  Value = sua chave da Anthropic.
- **Telegram API** (nome: `Telegram Bot`): cole o token do BotFather.

## 6. Importar os workflows
**Workflows → Import from File** → importe os dois JSON de `workflows/`.
Em cada nó marcado com credencial (`Anthropic — Draft/Execute`, `Telegram — *`),
selecione a credencial que você criou no passo 5 (o import deixa como "REPLACE").

## 7. Ajustar suas palavras-chave
Abra `Score & Draft Prompt` (workflow 01) e edite no topo do código:
`KEYWORDS`, `MIN_SALARY`, `MAX_PROPOSALS_PER_RUN`, `MODEL`.

## 8. Testar e ativar
- Workflow 01: clique **Execute Workflow** uma vez. Deve chegar vaga(s) + rascunho no
  Telegram. Se vier, **ative** (toggle no topo) para rodar a cada 2h.
- Workflow 02: **ative**. No Telegram, mande `/exec escreva um post de 300 palavras
  sobre X`. Deve voltar um rascunho.

## Trocar para OpenAI (opcional)
No nó HTTP de IA, mude:
- URL → `https://api.openai.com/v1/chat/completions`
- Header → remova `anthropic-version`; a credencial Header Auth vira
  `Authorization` = `Bearer SUA_CHAVE`.
- Body → `{ "model": "gpt-4o", "messages": [ { "role":"user", "content": $json.aiPrompt } ] }`
- No Code de extração, troque `r.content[].text` por `r.choices[0].message.content`.

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
