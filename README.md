# Autonomo — Motor (n8n)

O **cérebro** que faltava nas versões de frontend. Um agente que roda 24h e trabalha
por você, com você nos dois pontos de decisão que importam: aprovar a candidatura e
revisar a entrega.

Não é mais um painel. São os workflows que de fato descobrem vaga, pontuam, rascunham
proposta e rascunham entrega — rodando numa instância EC2 sua, dentro do n8n.

## O que este motor faz

1. **Descoberta + proposta** (`workflows/01-discovery-and-proposal.json`)
   A cada 2h puxa vagas de 4 fontes públicas (RemoteOK, Himalayas, Adzuna US e
   Adzuna DE — cobrindo USD e EUR), filtra pelas suas palavras-chave, pontua por
   relevância, aplica um teto de custo (Guard) e gera um rascunho de proposta com
   IA. Manda tudo no seu Telegram. **Você** decide e se candidata na fonte.

2. **Execução + entrega** (`workflows/02-execution-and-delivery.json`)
   Você manda `/exec <descrição da tarefa>` no Telegram; ele rascunha a entrega
   (texto, auditoria, análise) marcando `[VERIFICAR]` no que depende de dado externo,
   e devolve para **sua revisão** antes de qualquer envio ao cliente.

3. **Descoberta + bid no Freelancer.com** (`workflows/03-freelancer-discovery-and-bid.json`)
   Único marketplace da pesquisa com API oficial que permite bid automatizado sem
   violar ToS (Upwork/Fiverr/LinkedIn proíbem isso explicitamente). Mesmo assim, o
   bid só sai depois que **você aprova** no dashboard — a descoberta nunca envia
   nada sozinha. Ver `docs/SETUP.md#6b`.

## O que este motor deliberadamente NÃO faz

Nada de auto-candidatar em massa, scraping logado no Upwork/Fiverr/LinkedIn, ou entrega
direto ao cliente sem revisão. Esse é o comportamento que **bane suas contas e queima
cliente** — o ganho de tempo real vem do rascunho automático + sua aprovação, não de
tirar o humano do circuito. As fontes de descoberta (RemoteOK e afins) são boards
públicos feitos para ser consumidos via API; o Freelancer.com é a exceção onde a própria
plataforma expõe bid via API oficial — e mesmo assim o envio passa por aprovação humana.

## Estrutura

```
autonomo/
├── setup-ec2.sh                     # sobe swap + Docker + n8n na EC2
├── docker-compose.yml               # alternativa via compose
├── template.yaml                    # SAM: deploy da API (Lambda+DynamoDB)
├── workflows/                       # importe estes JSON no n8n
│   ├── 01-discovery-and-proposal.json
│   ├── 02-execution-and-delivery.json
│   └── 03-freelancer-discovery-and-bid.json   # opcional, ver docs/SETUP.md#6b
├── dashboard-api/index.mjs          # Lambda: persistência das missões + Guard
├── dashboard-web/                   # painel React/Vite (funil found→delivered)
├── prompts/                         # o que cada prompt faz e onde editar
├── guard/guard-config.json          # política de custo/segurança (referência)
├── build_workflows.py               # regera os JSON se você mudar algo
└── docs/
    ├── DEPLOY_API.md                 # deploy da Lambda+DynamoDB (via CloudShell)
    ├── DEPLOY_FRONTEND.md            # deploy do painel no Amplify Hosting
    ├── SETUP.md                      # EC2 + n8n de ponta a ponta
    └── ARCHITECTURE.md               # diagrama e decisões
```

## Começo rápido

1. Deploy da API — **`docs/DEPLOY_API.md`** (Lambda + DynamoDB via CloudShell).
2. Motor n8n — **`docs/SETUP.md`**: conecta na EC2 por SSH → `bash setup-ec2.sh` →
   abre túnel SSH → cria conta no n8n → importa os 2 workflows → configura
   credenciais (Gemini + Telegram) → seta as env vars da API do passo 1 → ativa.
3. Painel — **`docs/DEPLOY_FRONTEND.md`** (Amplify Hosting, root directory do
   monorepo e env vars do build).

Pronto: agente rodando, com o funil visível no `dashboard-web`.

## Trocar a IA

Os workflows chamam a API do Gemini (`gemini-flash-latest`) via HTTP Request —
camada gratuita. Para usar OpenAI, veja o diff em `docs/SETUP.md`. **Sua chave
nunca fica no código** — entra como credencial dentro do n8n.
