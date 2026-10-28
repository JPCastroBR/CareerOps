# CareerOps — Roadmap

> **Visão**: Pipeline de automação de busca de emprego (SRE/DevOps/Cloud) que coleta → filtra → analisa com IA → registra em Google Sheets + Telegram, com agendamento multi-horário resiliente a desligamento.
> **Repositório**: https://github.com/JPCastroBR/CareerOps
> **Atualizado**: 2026-10-28

---

## 📍 Status Atual: Fase 9 concluída + dashboard profissional, scheduler e workflows versionados entregues

O pipeline passou a usar o `profile.yaml` como fonte de verdade para **filtrar e pontuar** as vagas:

1. **Pré-filtro determinístico por título** (9.1) — descarta funções claramente fora do perfil (Full Stack/Dev/Data/QA/Arquiteto/Segurança/gestão) antes da IA; títulos ambíguos são preservados.
2. **Contexto do perfil no payload** (9.2) — `main.py` envia um `profile_context` compacto por vaga; o prompt do n8n passou a consumir esse contexto em vez do bloco `PERFIL` hardcoded.
3. **Scorer determinístico** (9.3) — `det_score` 0–100 orientado a função/níveis de experiência; o n8n combina `final = max(IA, det)` (com guarda para IA vazia).

Também corrigido um bug crítico de produção: `maxTokens` do modelo de IA estourava em prompts longos, gerando **saída vazia** e descarte silencioso de vagas (resolvido com `maxTokens` maior + guarda usando `det_score`).

Tudo com **testes unitários (124)**.

---

## 🗺 Fases & Marcos

### Fase 0 — Prova de Conceito (pré-repo)
- [x] Script Python inicial (`main.py`) varrendo países hardcoded via JobSpy (LinkedIn + Indeed)
- [x] Filtros regex locais (blocklist título/descrição) + anti-duplicidade (`vagas_processadas.txt`)
- [x] Envio para n8n via webhook (`/webhook/careerops-jobs`), rate-limit 2.5s
- [x] n8n workflows: `careerops-n8n`, `careerops-digest`, `careerops-curriculo`
- [x] Stack Docker (n8n + collector)

### Fase 1 — Primeira Estruturação & Agendamento (commit `b8d7ec7`)
- [x] Repo Git init + push para `JPCastroBR/CareerOps`
- [x] `.gitignore` protegendo `.env`, `credentials/`, `vagas_processadas.txt`, `logs/`
- [x] CLI `--country` + aliases PT (brasil, holanda, alemanha, etc.)
- [x] `scripts/install_cron_jobs.sh` — 12 jobs/dia, países hardcoded no cron
- [x] `scripts/run_careerops.sh` — wrapper por país, log por país/data
- [x] Adição de **Germany** aos alvos + alias `alemanha`

### Fase 2 — Documentação & Uso Genérico (commit `bd0d99f`)
- [x] README reescrito para uso genérico (não mais focado no caso pessoal)
- [x] `.env.example` com todas as variáveis documentadas (sem segredos reais)
- [x] Removidas referências pessoais do README (chat ID, service account, paths)

### Fase 3 — CLI & Fluxos Visuais (commit `a9dcba0`)
- [x] Novos modos CLI: `--all` (roda lista completa do código) e `--countries "Brazil,Netherlands"` (lista explícita numa execução)
- [x] README: seção "Pipeline Flow" com Mermaid + tabela de fases
- [x] `docs/careerops-flow.html` — diagrama SVG visual do fluxo completo

### Fase 4 — Configuração Externa (commit `4ab5876`)
- [x] **`config.json`** criado (inglês, padrão): `search_query`, `targets` (country + work_mode), `title_blocklist`, `results_per_country`, `brazil_remote_only`
- [x] `main.py` lê `config.json` se existir; **fallback seguro** para `worldwide` remoto-only se ausente
- [x] Mapeamento `work_mode`: `remote` / `onsite` / `any` / `hybrid` → `is_remote`
- [x] `title_blocklist` config-driven com `BASE_TITLE_BLOCKLIST` interna (safety net)
- [x] `config.json` versionado no repo (padrão do projeto; revise alvos pessoais antes de publicar)

### Fase 5 — Cron Config-Driven (commit `8d167b0`)
- [x] `install_cron_jobs.sh`: `SCHEDULES` agora só tem **horários + flag digest** (sem países)
- [x] `run_careerops.sh`: sem país → lê `config.json`; `digest` como arg único; país isolado ainda suportado
- [x] Cron reinstalada (7 jobs por horário, digest no último) — zero país hardcoded
- [x] README: Opção B + Design Resiliente atualizados para padrão config-driven

### Fase 6 — Portais de Carreira de Bancos BR (Gupy + Workday)
- [x] `src/careerops/collector/banks.py` — adapter **Gupy** (Itaú/Sicredi/BMG) via `__NEXT_DATA__` SSR
- [x] Adapter **Workday** (Santander) — search `cxs/jobs` `limit=20` + detalhe via JSON-LD; `httpx[http2]`
- [x] Rate gate (≤8 req/60s) + backoff 60s + stop-on-throttle (Workday rejeita `limit≥22` e rajadas com HTTP 400)
- [x] `config.json`: campo `banks[]` com `source` + `workday.{tenant,site}`; `--source banks|all`
- [x] Vagas de bancos fluem pelo mesmo blocklist / anti-dup / filtro do JobSpy
- [x] README: seção banks Workday + peculiaridade do `limit`

### Fase 7 — Qualidade de Filtragem & Perfil Estruturado
- [x] **Filtro de modalidade BR**: `collector/filters.py` — aceita **remoto + híbrido**, descarta **presencial**
- [x] Corrigido bug de `is_remote` (string `"False"` era tratada como verdadeira) + reconhecimento de localização (`Brazil`/`Brasil`/token `BR`)
- [x] **Dedup normalizada** (`collector/dedup.py`): mesma URL **ou** empresa+título+localização (sem acentos/pontuação/caixa)
- [x] **`profile.yaml`** — fonte única de verdade (níveis `hands_on`/`working_knowledge`/`learning`/`conceptual`/`none`, objetivos, preferências, matching guidance)
- [x] **`profile.py`** — carregador/validador (schema, chaves duplicadas, idiomas sem níveis técnicos)
- [x] `main.py` carrega/loga o perfil no início (sem alterar decisões do pipeline)
- [x] `pyyaml` adicionado às dependências
- [x] Testes unitários: filtros (15), dedup (13), perfil (13)

### Fase 8 — Portais InHire + Descoberta Incremental
- [x] `collector/inhire.py` — API pública (`api.inhire.app/job-posts/public/pages`, header `X-Tenant`)
- [x] Mapeamento `workplaceType` → `is_remote`; URL `https://<slug>.inhire.app/vagas/<jobId>/<slug>`
- [x] `collector/inhire_discovery.py` — descoberta via **crt.sh + Wayback** (sem custo), filtro de slugs internos e validação pela API
- [x] `data/inhire_companies.json` — **164 tenants validados**
- [x] `--source inhire` + seção `inhire` no `config.json` (`enabled`, `companies`, `results_per_company`, `include_keywords`)
- [x] `scripts/discover_inhire.sh` — atualização periódica (cron 1x/dia recomendado)
- [x] Testes unitários de InHire/discovery (10)

### Fase 9 — Pré-filtro & Matching orientado ao perfil
- [x] **9.1 Pré-filtro determinístico por título** (`collector/prefilter.py`): regras em `profile.yaml → title_matching` (target / off_profile hard|soft); negativo vence, target resgata soft, ambíguo preservado; relatório de mantidas/descartadas/ambíguas por motivo
- [x] **9.2 Contexto do perfil** (`profile.build_profile_context`): contexto compacto e determinístico (objetivo, cargos, senioridade, skills por nível, limitações, idiomas, modalidade) enviado no payload; prompt do n8n substituiu o bloco `PERFIL` hardcoded por `{{ $('Webhook').item.json.body.profile_context }}`
- [x] **9.3 Scorer determinístico** (`collector/scoring.py`): `det_score` 0–100 (função + skills por nível + senioridade + modalidade; `learning` não reprova; hard blockers). `det_score`/`det_rationale` no payload; n8n usa `final = max(IA, det)` com guarda para IA vazia
- [x] `skill_aliases` no `profile.yaml` (mapeia termos das vagas → skill id)
- [x] Correção de produção: `maxTokens` do modelo + guarda `det_score` para **saída vazia da IA** (evitava descarte silencioso)
- [x] Testes: pré-filtro (16), contexto (6), scorer (12)

---

## 🔧 Arquitetura Atual

```
scheduler (container, config.json → schedule)
   └─> main.py  (run_careerops.sh = exec manual)

               ├─ carrega/valida profile.yaml (profile.py)   [observabilidade]
               ├─ fontes:
               │    ├─ JobSpy: LinkedIn + Indeed
               │    ├─ banks.py: Gupy + Workday (Itaú/Sicredi/BMG/Santander)
               │    └─ inhire.py: portais InHire ← data/inhire_companies.json
               │          (inhire_discovery.py: crt.sh + Wayback → valida → JSON)
               ├─ filtros: blocklist (main.py) + modalidade (filters.py) + dedup (dedup.py) + pré-filtro por título (prefilter.py)
               ├─ perfil: envia `profile_context` (profile.py) e `det_score` (scoring.py) no payload
               └─ POST 1 vaga/request → n8n (IA + `final = max(IA, det)`) → Sheets/Telegram
```

**Princípio-chave**: Código = lógica. Config = intenção (versionado; revise alvos pessoais). Perfil = fonte de verdade do candidato (`profile.yaml`).

---

## 🚀 Roadmap Futuro

### Fase 10 — Busca por intenção & Robustez (PRÓXIMA)
- [ ] **Revisar a busca por intenção** (SRE/Reliability, DevOps/Platform, Cloud/Infra, Observabilidade) em vez de uma query booleana única
- [ ] **Alvos**: internacional contratando residentes no BR / relocação com visto (hoje só Brasil)
- [ ] **Cron para `discover_inhire.sh`** (atualização automática de empresas)
- [ ] **Nó Code no n8n** para preencher `OBS`/`SKILLS` a partir do `det_rationale` quando a IA vier vazia
- [ ] **Validação de schema** do `config.json` (jsonschema) com mensagens claras
- [x] **Metrics**: dashboard expõe funil (coleta → pré-filtro → IA → aprovadas) e distribuição de scores (`/api/funnel`, `/api/overview`)
- [ ] **Alertas de watchdog**: cron parado > 4h

### Fase 11 — Auto-apply & Enriquecimento
- [ ] **Auto-apply** para scores > 85 (Playwright/Greenhouse/Lever/InHire)
- [ ] **Feedback loop**: Sheets "Entrevista Agendada" → ajuste do scorer/prompt
- [ ] **Enriquecimento de empresa**: rating, size, funding (Glassdoor/Crunchbase)
- [ ] **Multi-currículo**: matcher por país/idioma (PT/EN/DE)

### Fase 12 — Deploy, Distribuição & UX
- [ ] **Docker image** do collector publicada (em vez de `uv run` no container)
- [ ] **CI**: lint + test em PR (GitHub Actions)
- [ ] **Setup wizard**: `careerops init` (config.json + .env + profile.yaml interativos)
- [x] **Dashboard web** profissional (Visão geral, Vagas, Analisadas, Config, Perfil, Agenda, Workflows, Segredos)
- [x] **Workflows versionados** em `n8n/workflows/` + `n8n-sync` (auto-import quando banco vazio)
- [x] **Scheduler no container** (`config.json → schedule`, sem cron)
- [ ] **Mais fontes**: Greenhouse, Lever, Hacker News, Wellfound

---

## 📊 Métricas de Sucesso
- Taxa de falsos positivos (vaga irrelevante aprovada): < 10%
- Redução de chamadas de IA via pré-filtro: ≥ 50%
- Cobertura de testes: config/filtro/dedup/perfil/InHire/pré-filtro/scorer (hoje: 124 testes unitários)
- Uptime do pipeline: > 99% (scheduler no container)

---

## 🗒 Notas de Implementação
- **Commits**: Fase 0-5 (`b8d7ec7` → `8d167b0`) + Fase 6 (Gupy/Workday) + Fase 7 (filtro/dedup/perfil) + Fase 8 (InHire) + Fase 9 (pré-filtro/contexto/scorer)
- **Arquivos versionados**: inclui `config.json`, `profile.yaml`, `data/inhire_companies.json`, `data/inhire_seed.txt`, `n8n/workflows/*.json` (sem `.env`, `secrets/`, `vagas_processadas.txt`, `logs/`, `data/telemetry/`)
- **Stack**: Python 3.12 (uv), JobSpy, httpx[http2], PyYAML, n8n (Docker), IA (OpenAI/DeepSeek), Google Sheets, Telegram, scheduler (container)
- **Fontes de vagas**: LinkedIn + Indeed (JobSpy), Gupy (Itaú/Sicredi/BMG), Workday (Santander), **InHire (164 tenants)**
- **Testes**: `uv run --project . python -m unittest discover -s tests` (124 testes; `tests/` versionado)
- **Padrão de branch**: `master` direto (repo pessoal/privado)
