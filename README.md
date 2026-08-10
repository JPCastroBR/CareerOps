📄 PROJETO: CareerOps - Job Hunting Automation Pipeline

1. Visão Geral e Objetivo
O CareerOps é um pipeline automatizado de engenharia de dados e recrutamento focado em buscar, filtrar, avaliar e registrar oportunidades de emprego para o perfil de Engenheiro de Infraestrutura / SRE Sênior (CareerOps).

O foco do perfil técnico inclui Cloud (AWS, OCI), Observabilidade (Grafana, Zabbix), IaC/Automação (Terraform, Ansible, PowerShell) e Containers (Docker, Kubernetes). O objetivo do sistema é garimpar vagas de trabalho remoto no Brasil e vagas com suporte à realocação/visto em países europeus estratégicos de alto custo-benefício.

2. Stack Tecnológico
- **Linguagem & Ambiente**: Python 3.12 gerenciado via uv
- **Web Scraping**: Biblioteca JobSpy (focada em LinkedIn e Indeed)
- **Orquestração**: n8n rodando localmente via Docker (Container: careerops-n8n)
- **Inteligência Artificial**: Integração do n8n com a API da OpenAI (GPT) via nó Basic LLM Chain
- **Banco de Dados/Output**: Google Sheets via API (Service Account)
- **Agendamento**: Cron do Linux (via `install_cron_jobs.sh`) — NÃO usa horários fixos rígidos; o wrapper `run_careerops.sh` é idempotente e seguro para execução "catch-up" se o computador estava desligado no horário agendado
- **Logs**: Arquivos por país/data em `logs/careerops-<pais>-YYYYMMDD.log`

3. Fluxo de Execução (Pipeline)

Fase A: Coleta (Python - main.py)
- Query Otimizada: Busca por '"Site Reliability Engineer" OR "SRE" OR "Infrastructure Engineer" OR "Cloud Infrastructure" OR "DevOps"'
- Alvos Estratégicos (configuráveis via `--country`):
  - Brasil (Apenas Remoto) — 4x/dia: 06h, 11h, 16h, 21h
  - Países Europeus (Remoto + Presencial/Híbrido) — 1x/dia pela manhã (horário local Europa):
    - Holanda (08h), Alemanha (08h), Irlanda (09h), Reino Unido (09h), Polônia (09h), Suíça (09h)
  - América do Norte — 1x/dia manhã:
    - Canadá (08h)
  - Worldwide (Global B2B/PJ) — 1x/dia noite (23h)
- Plataformas: LinkedIn e Indeed (parâmetro `country_indeed` configurado)
- Filtro Brasil: Apenas vagas remoto (descarta presencial/híbrido via regex)

Fase B: Filtragem Local e Anti-Duplicidade (Python)
- Blocklist Baseada em Regex (títulos + descrições): Remove desenvolvedores puros, IA/Dados, Suporte N1, ERPs, vagas exigindo idiomas fluentes (alemão, francês, holandês, nórdicos), vistos não patrocinados, clearances militares, estágios
- Memória de Estado: `vagas_processadas.txt` — descarta URLs já processadas

Fase C: Orquestração e Envio (Python → n8n)
- Webhook: POST para `http://n8n:5678/webhook/careerops-jobs` com payload completo da vaga
- Rate Limiting: Sleep 2.5s entre envios (protege Google Sheets contra HTTP 429)
- Digest Diário: Disparado APENAS na última execução do Brasil (21h) via `http://n8n:5678/webhook/careerops-digest` — evita spam de múltiplos resumos/dia

Fase D: n8n Workflows (Orquestração Pós-Coleta)
1. `careerops-jobs` — Recebe vagas, aplica Guard Rails, chama IA (OpenAI), roteia por score
2. `careerops-digest` — Consolida vagas do dia, envia resumo único via Telegram
3. `careerops-curriculo` — Pipeline de geração de currículo ATS PT/EN (Google Docs → IA → Obsidian → Telegram)

4. Estrutura do Projeto
CareerOps
├── README.md
├── docker-compose.yml
├── pyproject.toml
├── uv.lock
├── vagas_processadas.txt          # Memória de estado (NÃO versionar)
├── .env                           # Segredos (NÃO versionar)
├── credentials/
│   └── carrerops-502122-b51206f3c366.json   # Google Service Account (NÃO versionar)
├── scripts/
│   ├── install_cron_jobs.sh       # Gerenciador de cron jobs multi-país
│   ├── run_careerops.sh           # Wrapper por país (log por país/data)
│   └── run_digest.sh              # Disparo manual do digest
├── logs/                          # Logs de execução (NÃO versionar)
└── src/
    └── careerops/
        ├── __init__.py
        ├── collector/
        │   ├── __init__.py
        │   └── jobspy.py
        └── main.py                # Entry point CLI (--country, --list, --digest)

5. Chaves/APIs Necessárias e Onde Estão

| Serviço | Credencial | Local | Observação |
|---------|------------|-------|------------|
| **OpenAI / OpenRouter** | `OPENAI_API_KEY` ou `OPENROUTER_API_KEY` | n8n Credentials (id: `zXajfEyPnxUoFzej`) | Usado no nó Basic LLM Chain para análise de vaga + currículo |
| **Google Sheets / Docs** | Service Account JSON (`carrerops-502122-b51206f3c366.json`) | `credentials/` + n8n Credential (id: `87M1CpQdV64Rdg4z`) | Service account `n8n-bot@carrerops-502122.iam.gserviceaccount.com` — NÃO expira |
| **Google OAuth2 (fallback)** | Refresh/Access Token | `~/.hermes/.env` ou n8n | Expira — preferir Service Account |
| **Telegram Bot** | `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | `~/.hermes/.env` + n8n Credentials | Chat ID: `8866154869` |
| **n8n API** | `N8N_API_KEY` | `~/.hermes/.env` | Para gestão remota de workflows |
| **LinkedIn/Indeed (JobSpy)** | Cookies/Headers | Variáveis de ambiente no container `careerops-collector` | JobSpy usa sessão de navegador; cookies podem expirar |

⚠️ **Segurança**: `.env`, `credentials/*.json`, `vagas_processadas.txt`, `logs/` estão no `.gitignore` — NUNCA commitá-los.

6. Agendamento Automático (Cron) — Design "Resiliente a Desligamento"

O sistema NÃO assume que o computador está sempre ligado. O agendamento é feito via cron tradicional (horários fixos), mas:

- **Wrapper idempotente**: `run_careerops.sh` pode ser executado a qualquer momento — se o computador estava desligado às 06h, ao ligar às 10h você roda manualmente `./run_careerops.sh Brazil` e ele processa normalmente
- **Memória de estado**: `vagas_processadas.txt` garante que não há duplicatas mesmo se rodar múltiplas vezes
- **Digest único**: Apenas a última execução do Brasil (21h) dispara o digest — se você rodar manualmente às 10h, ele NÃO dispara digest (flag `--digest` controla isso)
- **Logs por país/data**: Cada execução cria `logs/careerops-<pais>-YYYYMMDD.log` — rastreabilidade total

**Comandos de gerenciamento:**
```bash
# Instalar/atualizar cron jobs (edite SCHEDULES no script antes)
/home/castro/Projects/CareerOps/scripts/install_cron_jobs.sh install

# Listar jobs ativos
/home/castro/Projects/CareerOps/scripts/install_cron_jobs.sh list

# Remover todos os jobs CareerOps
/home/castro/Projects/CareerOps/scripts/install_cron_jobs.sh remove

# Testar país específico
/home/castro/Projects/CareerOps/scripts/install_cron_jobs.sh test Brazil
```

**Schedule atual (12 jobs/dia):**
| País | Cron | Digest |
|------|------|--------|
| Brazil | 0 6,11,16,21 * * * | 21h apenas |
| Netherlands | 0 8 * * * | — |
| Germany | 0 8 * * * | — |
| Ireland | 0 9 * * * | — |
| United Kingdom | 0 9 * * * | — |
| Poland | 0 9 * * * | — |
| Switzerland | 0 9 * * * | — |
| Canada | 0 8 * * * | — |
| Worldwide | 0 23 * * * | — |

7. Uso Manual (CLI)
```bash
# Via wrapper (sobe stack Docker + loga)
/home/castro/Projects/CareerOps/scripts/run_careerops.sh Brazil
/home/castro/Projects/CareerOps/scripts/run_careerops.sh "United Kingdom" digest

# Direto no container (mais rápido, stack já up)
docker exec careerops-collector uv run src/careerops/main.py --country Brazil
docker exec careerops-collector uv run src/careerops/main.py --country "United Kingdom" --digest

# Listar alvos sem buscar (dry-run)
docker exec careerops-collector uv run src/careerops/main.py --country Germany --list
```

8. Próximos Passos do Projeto
- Manter sintonia fina da Blocklist em `main.py` conforme o mercado muda
- Feedback loop: marcar no Sheets "Entrevista Agendada" → re-treinar prompt IA
- Auto-apply para scores > 85 (Playwright)
- Enriquecimento empresa: Glassdoor rating, size, funding (Crunchbase API)
- Dashboard Grafana: funil completo (coletas → guard rails → IA → sheets → entrevistas → ofertas)
- Pipeline de currículo 100% Hermes/Python (remover dependência n8n para escrita no Obsidian)