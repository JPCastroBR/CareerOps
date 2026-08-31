# CareerOps — Roadmap

> **Visão**: Pipeline de automação de busca de emprego (SRE/DevOps/Cloud) que coleta → filtra → analisa com IA → registra em Google Sheets + Telegram, com agendamento multi-horário resiliente a desligamento.
> **Repositório**: https://github.com/JPCastroBR/CareerOps (privado)
> **Atualizado**: 2026-08-19

---

## 📍 Status Atual: Fase 6 + 6.5 — Bancos BR & InHire (CONCLUÍDAS)

O pipeline varre **portais próprios de bancos brasileiros** (Gupy/Workday) e **tenants InHire** (`*.inhire.app`) além do LinkedIn/Indeed. Bancos: **Gupy** (Itaú, Sicredi, BMG) via `__NEXT_DATA__` e **Workday** (Santander) via API `cxs/jobs` + JSON-LD. InHire: discovery (seed + Wayback) → validação via API pública → coleta → matching local contra o CV → dedupe, tudo em JSON/JSONL (`data/`). Ambas entram no fluxo comum (blocklist → anti-dup → Brasil remoto-only → n8n).

---

## 🗺 Fases & Marcos

### Fase 0 — Prova de Conceito (pré-repo)
- [x] Script Python inicial (`main.py`) varrendo países hardcoded via JobSpy (LinkedIn + Indeed)
- [x] Filtros regex locais (blocklist título/descrição) + anti-duplicidade (`vagas_processadas.txt`)
- [x] Envio para n8n via webhook (`careerops-jobs`), rate-limit 2.5s
- [x] n8n workflows: `careerops-jobs`, `careerops-digest`, `careerops-curriculo`
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
- [x] `config.json` adicionado ao `.gitignore` (por usuário, não sobe)

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
- [x] Vagas de bancos fluem pelo mesmo blocklist / anti-dup / `brazil_remote_only` do JobSpy
- [x] README: seção banks Workday + pecularidade do `limit`

---

### Fase 6.5 — Fonte InHire (tenants `*.inhire.app`)
- [x] **Fase 1 (audit)**: arquitetura sem base class, DataFrames, persistência JSON (sem DB), argparse
- [x] **Fase 2 (discovery)**: `tenant_discovery.py` — `seed` (file) + `wayback` (CDX); `slug_from_host`
- [x] **Fase 3 (validator)**: `tenant_validator.py` — API `GET /job-posts/public/pages` + header `X-Tenant`; `count_jobs_from_pages`
- [x] **Fase 4 (persistence)**: `repository.py` — `TenantRepository`/`JobRepository` (JSON/JSONL em `data/`)
- [x] **Fase 5 (collector)**: `job_collector.py` — `collect_all_tenants`/`collect_jobs_for_tenant`; mapeia schema `jobsPage`
- [x] **Fase 6 (url)**: `url.py` — `build_job_url`/`title_slug` (`https://<tenant>.inhire.app/vagas/<id>/<slug>`)
- [x] **Fase 7 (normalize)**: `normalize.py` — workplace/seniority/title/company/dates
- [x] **Fase 8 (cv_profile)**: `cv_profile.py` — `CVProfile` (roles/skills/seniority/countries) + `compute_match`
- [x] **Fase 9 (matching)**: `matching.py` — 7 dimensões (role/skills/cloud/seniority/location/remote/exp)
- [x] **Fase 10 (dedup)**: `dedup.py` — `dedupe_jobs` por `tenant_slug+source_id`
- [x] **Fase 11 (CLI)**: `main.py` subcomando `inhire {discover,validate,jobs,run}` + `--source inhire`
- [x] **Fase 12 (integration)**: `fetch_inhire_jobs` espelha `fetch_bank_jobs`/`fetch_recent_jobs`
- [x] **Fase 13 (robustness)**: `http.py` retry/backoff/rate-gate/cache (timeout 10s, 3 retries, delay 0.4s)
- [x] **Fase 14 (tests)**: `tests/test_inhire.py` (17 testes offline: url/normalize/matching/dedup/config/repo)
- [ ] **Fase 15**: métricas de performance por fonte (stats já coletados em `collect_all_tenants`)
- [ ] **Fase 16**: descoberta de empresas-alvo (`inhire companies --source linkedin`) p/ seed automático

---

## 🔧 Arquitetura Atual

```
cron (horários) 
   └─> run_careerops.sh (wrapper)
         ├─ lê config.json  ──────> países + work_mode + query + blocklist + bancos
          └─> main.py (JobSpy + portais bancos Gupy/Workday + tenants InHire → filtros → n8n webhook)
                └─> n8n (Guard Rails → IA OpenAI → Sheets/Telegram)
```

**Princípio-chave**: Código = lógica. Config = intenção (por usuário, gitignored).

---

## 🚀 Roadmap Futuro

### Fase 7 — Robustez & Observabilidade
- [ ] **Testes automatizados** (`pytest`): parsing de config, mapeamento work_mode, filtros
- [ ] **Validação de schema** do `config.json` (jsonschema) com mensagens de erro claras
- [ ] **Metrics endpoint**: vagas/dia, taxa guard rails, distribuição de scores → Prometheus/Grafana
- [ ] **Alertas de watchdog**: cron parado > 4h dispara notificação

### Fase 8 — Enriquecimento & Inteligência
- [ ] **Auto-apply** para scores > 85 (Playwright/Greenhouse/Lever)
- [ ] **Feedback loop**: Sheets "Entrevista Agendada" → re-treinar prompt IA
- [ ] **Enriquecimento de empresa**: Glassdoor rating, size, funding (Crunchbase API)
- [ ] **Multi-currículo**: matcher de currículo por país/idioma (PT/EN/DE)

### Fase 9 — Deploy & Distribuição
- [ ] **Docker image do collector** publicada (em vez de `uv run` no container)
- [ ] **Helm chart / docker-compose prod** com healthchecks
- [ ] **Setup wizard**: `careerops init` que gera `config.json` + `.env` interativamente
- [ ] **CI**: lint + test em PR (GitHub Actions)

### Fase 10 — UX & Extensibilidade
- [ ] **Plugin de blocklist**: arquivos YAML separados por categoria (vistos, idiomas, níveis)
- [ ] **Suporte a mais fontes**: Greenhouse, Lever, Hacker News, Wellfound
- [ ] **Dashboard web** (Streamlit) para revisar scores < 70 sem abrir Telegram
- [ ] **Modo "dry-run" global**: `--dry-run` que lista vagas sem enviar ao n8n

---

## 📊 Métricas de Sucesso (Definir)
- Tempo de setup para novo usuário: < 15 min (clone → config → cron)
- Taxa de falsos positivos (vaga irrelevante aprovada): < 10%
- Cobertura de testes: > 70% das funções de config/filtro
- Uptime do pipeline: > 99% (cron + container)

---

## 🗒 Notas de Implementação
- **Commits**: Fase 0-5 (`b8d7ec7` → `8d167b0`) + Fase 6 (Gupy/Workday, atual)
- **Arquivos versionados**: ~21 (sem `.env`, `credentials/`, `vagas_processadas.txt`, `logs/`, `config.json`)
- **Stack**: Python 3.12 (uv), JobSpy, httpx[http2], n8n (Docker), OpenAI/OpenRouter, Google Sheets, Telegram, Cron
- **Fontes de vagas**: LinkedIn + Indeed (JobSpy), Gupy (Itaú/Sicredi/BMG), Workday (Santander), InHire (`*.inhire.app` via API pública)
- **Padrão de branch**: `master` direto (repo pessoal/privado)
