# Autonomo — Motor (n8n)

O **cérebro** que faltava nas versões de frontend. Um agente que roda 24h e trabalha
por você, com você nos dois pontos de decisão que importam: aprovar a candidatura e
revisar a entrega.

Não é mais um painel. São os workflows que de fato descobrem vaga, pontuam, rascunham
proposta e rascunham entrega — rodando numa instância EC2 sua, dentro do n8n.

## O que este motor faz

1. **Descoberta + proposta** (`workflows/01-discovery-and-proposal.json`)
   A cada 2h puxa vagas da API pública do RemoteOK, filtra pelas suas palavras-chave,
   pontua por relevância, aplica um teto de custo (Guard) e gera um rascunho de
   proposta com IA. Manda tudo no seu Telegram. **Você** decide e se candidata na fonte.

2. **Execução + entrega** (`workflows/02-execution-and-delivery.json`)
   Você manda `/exec <descrição da tarefa>` no Telegram; ele rascunha a entrega
   (texto, auditoria, análise) marcando `[VERIFICAR]` no que depende de dado externo,
   e devolve para **sua revisão** antes de qualquer envio ao cliente.

## O que este motor deliberadamente NÃO faz

Nada de auto-candidatar em massa, scraping logado no Upwork/Fiverr/LinkedIn, ou entrega
direto ao cliente sem revisão. Esse é o comportamento que **bane suas contas e queima
cliente** — o ganho de tempo real vem do rascunho automático + sua aprovação, não de
tirar o humano do circuito. A fonte escolhida (RemoteOK) é um board público feito para
ser consumido via API, onde a candidatura acontece fora da plataforma.

## Estrutura

```
autonomo-engine/
├── setup-ec2.sh                     # sobe swap + Docker + n8n na EC2
├── docker-compose.yml               # alternativa via compose
├── .env.example
├── workflows/                       # importe estes 2 JSON no n8n
│   ├── 01-discovery-and-proposal.json
│   └── 02-execution-and-delivery.json
├── prompts/                         # o que cada prompt faz e onde editar
├── guard/guard-config.json          # política de custo/segurança (referência)
├── build_workflows.py               # regera os JSON se você mudar algo
└── docs/SETUP.md                    # passo a passo de ponta a ponta
```

## Começo rápido

Veja **`docs/SETUP.md`**. Resumo: conecta na EC2 por SSH → `bash setup-ec2.sh` →
abre túnel SSH → cria conta no n8n → importa os 2 workflows → configura credenciais
(Anthropic + Telegram) → ativa. Pronto: agente rodando.

## Trocar a IA

Os workflows chamam a API da Anthropic (`claude-sonnet-5`) via HTTP Request. Para usar
mais barato, troque o modelo para `claude-haiku-4-5-20251001` no Code node. Para usar
OpenAI, veja o diff em `docs/SETUP.md`. **Sua chave nunca fica no código** — entra como
credencial dentro do n8n.
