# Arquitetura

```
              ┌──────────── EC2 (n8n em Docker, só localhost) ─────────────┐
              │                                                             │
  Schedule ─► RemoteOK API ─► Score/Filter/Dedup ─► Guard/check ─► Gemini ─►│
  (2h)        (público)       (dedup via dashboard)   (Lambda)   (rascunho) │
              │                                                             │
              └─► POST /missions (Lambda+DynamoDB) ─► Telegram (você)   (WF01)
              ┌─────────────────────────────────────────────────────────────┐
  Telegram ─► Prepare ─► Guard/check ─► Gemini ─► Telegram (rascunho p/     │  (WF02)
  /exec       (brief)     (Lambda)     (entrega)  sua revisão)              │
              └─────────────────────────────────────────────────────────────┘
                         ▲ acesso: túnel SSH -L 5678
```

Persistência: Lambda (`dashboard-api/index.mjs`) + DynamoDB (`autonomo-missions`,
`autonomo-config`), acessada pelos workflows via `$env.DASHBOARD_API_URL`.
Visão: `dashboard-web` (React/Vite) lê a mesma API e mostra o funil por status
(found → applied → done).

## Decisões

- **n8n como motor**, não mais um frontend. É onde vive a lógica que faltava.
- **Fonte pública (RemoteOK)** em vez de scraping logado. Legítima, sem auth,
  candidatura fora da plataforma. Zero risco de ToS.
- **Humano nos dois gargalos**: aprovar candidatura e revisar entrega. É o que
  diferencia "agente útil" de "bot que queima conta".
- **Dedup via DynamoDB** (dashboard como fonte de verdade) — o n8n consulta os
  `missionId` já existentes antes de gerar proposta de novo para a mesma vaga.
- **IA via HTTP Request** (não nó nativo) para não depender de versão de nó e
  poder trocar de provedor com um diff pequeno. Provedor atual: **Gemini**
  (`gemini-flash-latest`), trocado do Anthropic para usar a camada gratuita.

## Camada Guard (custo + segurança)

Documentada em `guard/guard-config.json` e aplicada em dois pontos:
- `MAX_PROPOSALS_PER_RUN` no Code node do WF01 limita chamadas de IA por execução.
- `POST /guard/check` (Lambda) impõe o teto diário (`hard_daily_ai_call_cap`) real,
  persistido no DynamoDB — sobrevive a restart do n8n.
- Aprovação humana antes de candidatar; revisão humana antes de entregar.
- `auto_apply_to_platforms: false` e `logged_in_scraping: false` são invariantes —
  não ligue isso sem entender o risco de banimento.

## Próximos tijolos (quando quiser evoluir)
1. Confirmar deploy da Lambda/DynamoDB em produção e consertar a IAM Role do
   Amplify para o `dashboard-web` ficar acessível.
2. Segunda fonte legítima (WeWorkRemotely RSS, HN "Who is hiring" via Algolia).
3. Login no painel.
4. Empacotar como oferta B2B dentro do AdsFlow.
