# CareerOps — Job Hunting Automation Pipeline

> **Pipeline automatizado de busca, filtragem, análise com IA e registro de vagas.**
> Configure seus critérios, rode localmente e receba apenas oportunidades relevantes no Google Sheets + Telegram.

---

## 🎯 Para que serve

Automatiza a procura de emprego para perfis tech (SRE, DevOps, Cloud, Platform, Infra):
- **Busca** em LinkedIn + Indeed via JobSpy
- **Filtra** localmente (blocklist, anti-duplicidade, modalidade)
- **Analisa** com IA (OpenAI/OpenRouter) comparando vaga vs seu currículo
- **Roteia**: Score alto → Google Sheets (candidatar) | Score baixo → Telegram (revisar)
- **Agenda**: Cron multi-país/horário, resiliente a desligamento (catch-up manual)

---

## 🔄 Pipeline Flow

### Visão Geral (Mermaid)

```mermaid
flowchart TD
    subgraph Sched["Cron Scheduler (Linux)"]
        C1[Brazil 06/11/16/21h] --> C2[NL/DE/CA 08h]
        C2 --> C3[IE/UK/PL/CH 09h]
        C3 --> C4[Worldwide 23h]
        C5["--all / --countries lista"]
    end

    subgraph CLI["Python CLI (main.py)"]
        W[run_careerops.sh wrapper] --> P[JobSpy: LinkedIn+Indeed]
        P --> F[Blocklist regex + Anti-dup]
        F --> BR[BR: remote-only filter]
        BR --> POST[POST n8n webhook]
    end

    subgraph N8N["n8n Workflows (Docker)"]
        J[careerops-jobs] --> GR[Guard Rails]
        GR --> IA[IA: OpenAI Score 0-100]
        IA --> R{Score ≥ 70?}
        D[careerops-digest] --> TG2[Telegram Daily]
        CU[careerops-curriculo] --> OB[Obsidian PT/EN]
    end

    Sched --> CLI
    CLI --> J
    R -->|Sim| SH[Google Sheets: Candidatar]
    R -->|Não| TG1[Telegram: Alerta Baixa]
```

### Diagrama Visual (Imagem)

📊 **[docs/careerops-flow.html](docs/careerops-flow.html)** — abra no navegador para ver o fluxo completo colorido (collector → guard rails → IA → Sheets/Telegram).

### Fases do Pipeline

| Fase | Componente | Função |
|------|-----------|--------|
| **A: Coleta** | `main.py` + `jobspy.py` | Busca LinkedIn/Indeed, 200 vagas/país, query OR |
| **B: Filtragem** | `filters.py` | Blocklist regex (título+descrição), anti-dup (`vagas_processadas.txt`), BR remoto-only |
| **C: Envio** | `run_careerops.sh` | POST webhook n8n, rate-limit 2.5s, digest flag |
| **D: Orquestração** | n8n (`careerops-jobs`) | Guard Rails → IA (OpenAI) → Score → Sheets/Telegram |
| **E: Digest** | n8n (`careerops-digest`) | Consolida dia → 1 msg Telegram (21h BR) |
| **F: Currículo** | n8n (`careerops-curriculo`) | Google Docs PT/EN → IA → Obsidian → Telegram |

---

## 🚀 Quick Start

```bash
# 1. Clone
git clone git@github.com:SEU_USUARIO/CareerOps.git
cd CareerOps

# 2. Configure credenciais (copie e edite)
cp .env.example .env
# Edite .env com suas chaves: OPENAI_API_KEY, TELEGRAM_BOT_TOKEN, etc.

# 3. Google Service Account
# Coloque o JSON em credentials/google-sa.json (não versionado)

# 4. Suba o stack
docker compose up -d

# 5. Teste uma busca
docker exec careerops-collector uv run src/careerops/main.py --country Brazil --list

# 6. Instale agendamento automático (opcional)
./scripts/install_cron_jobs.sh install
```

---

## ⚙️ Configuração Essencial

### 1. Variáveis de Ambiente (`.env`)

```bash
# IA (OpenAI ou OpenRouter)
OPENAI_API_KEY=sk-...
# ou
OPENROUTER_API_KEY=sk-or-...

# Telegram (bot + chat onde recebe alertas/digest)
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_CHAT_ID=123456789

# n8n (para gestão remota de workflows)
N8N_API_KEY=eyJhbGciOi...
```

> **Nunca commite `.env`** — está no `.gitignore`.

### 2. Google Service Account

1. Crie Service Account no GCP
2. Dê permissão `Editor` no Sheets/Docs alvo
3. Salve JSON em `credentials/google-sa.json`
4. No n8n: Credentials → Google API → Service Account → cole o JSON

### 3. Seu Currículo (para IA comparar)

O n8n lê dois Google Docs (PT + EN). Configure os IDs no workflow `careerops-curriculo`:
- Doc PT: `1ABC...` (seu currículo em português)
- Doc EN: `1XYZ...` (versão English ATS)

---

## ⚙️ Configuração Externa (`config.json`)

**Todo o "extra" (países, modalidade, query de busca, blocklist de títulos) vive num arquivo `config.json` separado do código.** Assim ninguém precisa fuçar no `main.py` para personalizar.

### Como funciona
- Se `config.json` **existir** → o pipeline usa países, modalidades, query e blocklist definidos nele.
- Se `config.json` **não existir** → cai num fallback seguro: busca **`worldwide` (remoto-only)** apenas. Nunca quebra.
- O arquivo está no `.gitignore` (não sobe pro repo) — cada usuário mantém o seu.

### Estrutura (`config.json`)
```json
{
  "_comment": "All keys optional. English canonical country names.",
  "search_query": "\"Site Reliability Engineer\" OR \"SRE\" OR \"DevOps\"",
  "results_per_country": 200,
  "targets": [
    { "country": "Brazil", "work_mode": "remote" },
    { "country": "Netherlands", "work_mode": "any" },
    { "country": "Germany", "work_mode": "onsite" },
    { "country": "worldwide", "work_mode": "remote" }
  ],
  "title_blocklist": ["junior", "intern", "manager"],
  "brazil_remote_only": true
}
```

### `work_mode` (modalidade de trabalho)
| Valor | Comportamento |
|-------|---------------|
| `remote` | Apenas vagas remotas (`is_remote=True`) |
| `onsite` | Apenas presenciais (`is_remote=False`) |
| `any` | Ambos (presencial + remoto) — gera 2 entradas por país |
| `hybrid` | Tratado como `any` (presencial + remoto) |

### `title_blocklist`
Lista de termos (case-insensitive) que descartam a vaga pelo **título**. Se vazia/ausente, usa a `BASE_TITLE_BLOCKLIST` interna (safety net). Para usar **sua** lista, preencha este array.

### `brazil_remote_only`
- `true` (padrão): descarta vagas do Brasil presenciais/híbridas (filtro extra por regex em location+título).
- `false`: mantém todas as modalidades do Brasil.

### Exemplo prático
Quer só **Brasil remoto** + **Portugal remoto** + **worldwide remoto**?
```json
{
  "targets": [
    { "country": "Brazil", "work_mode": "remote" },
    { "country": "Portugal", "work_mode": "remote" },
    { "country": "worldwide", "work_mode": "remote" }
  ],
  "brazil_remote_only": true
}
```
Pronto — sem tocar no código.

---

## 🌍 Personalizando Localidades (Países/Regiões)

### Opção A: Via CLI (execução única)
```bash
# Qualquer país suportado pelo JobSpy
docker exec careerops-collector uv run src/careerops/main.py --country Brazil
docker exec careerops-collector uv run src/careerops/main.py --country "United States"
docker exec careerops-collector uv run src/careerops/main.py --country Germany --digest
```

**Aliases PT suportados**: `brasil`, `holanda`, `alemanha`, `reino unido`/`uk`/`inglaterra`, `irlanda`, `polonia`, `suica`, `dinamarca`, `noruega`, `suecia`, `austria`, `finlandia`, `franca`, `canada`, `mundo`/`global`.

### Opção B: Agendamento Automático (sem países hardcoded)

O cron **não** decide países — ele só marca *quando* rodar. Os países vêm do `config.json`. Edite `scripts/install_cron_jobs.sh` → array `SCHEDULES` (apenas horários):

```bash
readonly SCHEDULES=(
    "0 6 * * *"          # varre config.json
    "0 11 * * *"
    "0 16 * * *"
    "0 21 * * * digest"  # última do dia dispara digest
    "0 8 * * *"
    "0 9 * * *"
    "0 23 * * *"
)
```

**Formato**: `"minuto hora dia mês semana [digest]"`
- `digest` = dispara resumo Telegram ao final (use em **apenas 1** job/dia)
- Sem país: o wrapper lê `config.json` e varre os `targets` definidos lá

Para mudar *quais* países/modalidades → edite `config.json`, não o cron. Depois reinstale:
```bash
./scripts/install_cron_jobs.sh install
```

### Opção C: Modo Remoto vs Presencial

No **`config.json`** (não no código), cada `target` aceita `work_mode`:

| `work_mode` | Gera |
|------------|------|
| `remote` | 1 alvo remoto (`is_remote=True`) |
| `onsite` | 1 alvo presencial (`is_remote=False`) |
| `any` | 2 alvos (presencial + remoto) |
| `hybrid` | igual a `any` |

```json
"targets": [
  { "country": "Brazil", "work_mode": "remote" },
  { "country": "Germany", "work_mode": "onsite" },
  { "country": "Netherlands", "work_mode": "any" }
]
```

- **Brasil**: além do `work_mode`, há o flag `brazil_remote_only: true` que descarta presencial/híbrido por regex (location + título).

---

## 💼 Personalizando Tipo de Vaga (Query de Busca)

### Query Principal (em `config.json`)

```json
"search_query": "\"Site Reliability Engineer\" OR \"SRE\" OR \"Infrastructure Engineer\" OR \"Cloud Infrastructure\" OR \"DevOps\""
```

**Para alterar**: edite `search_query` no seu `config.json` (não mexa no código).

**Exemplos de perfis:**

| Perfil | Query Sugerida |
|--------|----------------|
| **Backend/Go** | `"Golang" OR "Go Developer" OR "Backend Engineer" OR "API Developer"` |
| **Data Engineer** | `"Data Engineer" OR "Data Platform" OR "ETL" OR "Airflow" OR "dbt"` |
| **Kubernetes/Platform** | `"Platform Engineer" OR "Kubernetes" OR "K8s" OR "DevOps Platform"` |
| **Cloud Architect** | `"Cloud Architect" OR "Solutions Architect" OR "AWS Architect"` |
| **Full Stack** | `"Full Stack" OR "Fullstack" OR "React" AND "Node"` |

### Resultados por País (parâmetro `results_per_country` em `config.json`)

```json
"results_per_country": 200
```

---

## 🛡 Personalizando Filtros (Blocklist)

### Títulos (remove cargos fora do escopo)
```python
# src/careerops/main.py → palavras_proibidas_titulo
palavras_proibidas_titulo = [
    "estágio", "junior", "jr", "pleno", "mid-level",
    "qa", "tester", "test automation", "quality engineer",
    "suporte", "service desk", "support", "helpdesk",
    "backend", "frontend", "fullstack", "software engineer",
    "data scientist", "mlops", "ai engineer",
    "sap", "sales", "manager", "director",
    # adicione seus termos
]
```

### Descrições (barreiras: visto, idioma, clearance)
```python
palavras_proibidas_descricao = [
    "no visa sponsorship", "must have right to work",
    "fluent in german", "b2 deutsch", "c1 deutsch",
    "fluent in french", "courant français",
    "fluent in dutch", "vloeiend nederlands",
    "security clearance", "clearance",
    "werkstudent", "alternance", "internship",
    # adicione seus termos
]
```

> **Dica**: Mantenha listas em arquivo separado (`config/blocklist.yaml`) e carregue no `main.py` para versionar mudanças.

---

## 📊 Outputs: Onde as Vagas Vão

### Google Sheets (Score ≥ 70)
Planilha: **"CareerOps - Candidaturas"**
| Coluna | Exemplo |
|--------|---------|
| Data/Hora | 2026-08-08 14:30 |
| Empresa | Nubank |
| Vaga | Senior SRE |
| Link | linkedin.com/jobs/123 |
| Score | 87 |
| Skills Match | AWS, Terraform, K8s |
| Missing | GCP, ArgoCD |
| Status | Pronto para Candidatar |
| Ações | (manual) |

### Telegram (Score < 70 + Digest)
- **Alerta individual**: Vaga com score baixo + gaps principais
- **Digest diário**: 1 msg consolidada (configurado para 21h BR)

---

## 🔧 Estrutura do Projeto

```
CareerOps/
├── .env.example           # Template de variáveis
├── .gitignore
├── docker-compose.yml     # n8n + collector
├── pyproject.toml         # Deps Python (uv)
├── scripts/
│   ├── install_cron_jobs.sh  # Gerenciador cron (EDITAR SCHEDULES)
│   ├── run_careerops.sh      # Wrapper por país (log por país/data)
│   └── run_digest.sh         # Disparo manual digest
├── credentials/           # NÃO VERSIONAR (google-sa.json)
├── config.json             # NÃO VERSIONAR (config por usuário: países, query, blocklist)
├── logs/                  # NÃO VERSIONAR
├── docs/
│   └── careerops-flow.html  # Diagrama visual do fluxo (abrir no navegador)
├── src/
│   └── careerops/
│       ├── main.py        # CLI entry (--country, --list, --digest)
│       └── collector/
│           ├── jobspy.py      # Wrapper JobSpy
│           ├── filters.py     # Blocklist + anti-dup
│           └── parser.py      # Normalização payload
└── README.md
```

---

## 🐳 Docker Compose (Serviços)

```yaml
# docker-compose.yml
services:
  n8n:
    image: docker.n8n.io/n8nio/n8n:latest
    ports: ["5678:5678"]
    volumes: ["./n8n-data:/home/node/.n8n"]
    environment:
      - N8N_BASIC_AUTH_ACTIVE=true
      - N8N_BASIC_AUTH_USER=admin
      - N8N_BASIC_AUTH_PASSWORD=${N8N_PASSWORD}

  collector:
    build: .
    command: tail -f /dev/null  # container vivo para exec
    volumes: [".:/app"]
    environment:
      - LINKEDIN_COOKIES=${LINKEDIN_COOKIES}
```

---

## 📅 Agendamento: Design Resiliente

| Problema | Solução |
|----------|---------|
| Computador desligado no horário | Wrapper idempotente → rode manualmente quando ligar |
| Execução duplicada | `vagas_processadas.txt` evita duplicatas |
| Múltiplos digests/dia | Flag `digest` apenas em 1 job/dia (último) |
| Rastreabilidade | Logs por dia: `logs/careerops-YYYYMMDD.log` |
| Quais países varrer | Vem do `config.json` (não do cron) — edite o JSON, não o agendador |

**Comandos de cron:**
```bash
./scripts/install_cron_jobs.sh install   # instala/atualiza
./scripts/install_cron_jobs.sh list      # mostra ativos
./scripts/install_cron_jobs.sh remove    # remove todos
./scripts/install_cron_jobs.sh test      # testa 1 execução (lê config.json)
```

---

## 🔐 Segurança (Obrigatório)

| Arquivo | No .gitignore? | Ação |
|---------|----------------|------|
| `.env` | ✅ | Nunca commite |
| `credentials/*.json` | ✅ | Nunca commite |
| `vagas_processadas.txt` | ✅ | Estado local |
| `logs/` | ✅ | Apenas local |
| `.venv/`, `uv.lock` | ✅ | Local only |

---

## 🛠 Desenvolvimento

```bash
# Instalar deps
uv sync

# Rodar testes
uv run pytest

# Lint
uv run ruff check src/
uv run mypy src/

# Build container
docker compose build collector
```

---

## 📝 Checklist para Novo Usuário

- [ ] Clone repo
- [ ] Copie `.env.example` → `.env` e preencha chaves
- [ ] Crie Google Service Account → `credentials/google-sa.json`
- [ ] Configure n8n com credenciais Google + OpenAI + Telegram
- [ ] Importe workflows n8n (JSONs em `n8n-workflows/`)
- [ ] Edite `src/careerops/main.py`: `query` + `alvos` + `blocklist`
- [ ] Edite `scripts/install_cron_jobs.sh`: `SCHEDULES` para seus países/horários
- [ ] `docker compose up -d`
- [ ] Teste: `docker exec careerops-collector uv run src/careerops/main.py --country Brazil --list`
- [ ] `./scripts/install_cron_jobs.sh install`

---

## 🤝 Contribuindo

1. Fork → branch `feature/xyz`
2. Adicione testes para novos filtros/países
3. `uv run pytest` + `uv run ruff check`
4. PR com descrição do que muda e por quê

---

## 📄 Licença

MIT — Use, adapte, compartilhe.