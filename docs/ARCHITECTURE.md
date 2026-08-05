# Arquitetura

```
              ┌──────────── EC2 (n8n em Docker, só localhost) ────────────┐
              │                                                            │
  Schedule ─► RemoteOK API ─► Score/Filter/Dedup ─► Anthropic ─► Telegram │  (WF01)
  (2h)        (público)       + Guard (teto)         (rascunho)   (você)   │
              │                                                            │
  Telegram ─► Prepare ─► Anthropic ─► Telegram (rascunho p/ sua revisão)   │  (WF02)
  /exec       (brief)    (entrega)                                         │
              └────────────────────────────────────────────────────────────┘
                         ▲ acesso do seu Mac: túnel SSH -L 5678
```

## Decisões

- **n8n como motor**, não mais um frontend. É onde vive a lógica que faltava.
- **Fonte pública (RemoteOK)** em vez de scraping logado. Legítima, sem auth,
  candidatura fora da plataforma. Zero risco de ToS.
- **Humano nos dois gargalos**: aprovar candidatura e revisar entrega. É o que
  diferencia "agente útil" de "bot que queima conta".
- **Dedup por static data** do próprio n8n — não precisa de banco externo no MVP.
- **IA via HTTP Request** (não nó nativo) para não depender de versão de nó e
  poder trocar de provedor com um diff pequeno.

## Camada Guard (custo + segurança)

Documentada em `guard/guard-config.json` e aplicada no Code node do WF01:
- `MAX_PROPOSALS_PER_RUN` limita chamadas de IA por execução (custo previsível).
- Aprovação humana antes de candidatar; revisão humana antes de entregar.
- `auto_apply_to_platforms: false` e `logged_in_scraping: false` são invariantes —
  não ligue isso sem entender o risco de banimento.

## Próximos tijolos (quando quiser evoluir)
1. Persistir vagas/decisões num Postgres (troca o dedup por static data).
2. Ligar o painel que você já tem (AWS/Manus) a este n8n via webhook, para ver o
   funil num dashboard de verdade.
3. Segunda fonte legítima (WeWorkRemotely RSS, HN "Who is hiring" via Algolia).
4. Empacotar como oferta B2B dentro do AdsFlow.
