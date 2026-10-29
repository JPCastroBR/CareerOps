import os
import time
import json
import re
import argparse
import pandas as pd
import requests
from careerops.collector.jobspy import fetch_recent_jobs
from careerops.collector.banks import fetch_bank_jobs
from careerops.collector.filters import is_brazil_onsite, modality_allowed, normalize_modes, _block_pattern
from careerops.collector.dedup import dedupe_jobs, dedupe_jobs_dropped
from careerops.collector.inhire import fetch_inhire_jobs
from careerops.collector.prefilter import build_rules, apply_prefilter
from careerops.collector.scoring import build_scorer
from careerops.profile import load_profile, build_profile_context, ProfileError
from careerops import telemetry as tel

ARQUIVO_MEMORIA = "vagas_processadas.txt"
ARQUIVO_CONFIG = "config.json"
ARQUIVO_PERFIL = "profile.yaml"
ARQUIVO_INHIRE = "data/inhire_companies.json"

# ---------------------------------------------------------------------------
# Aliases: user thinks in Portuguese, JobSpy uses English names.
# Any key (lowercase) resolves to the canonical name used in `targets`.
# ---------------------------------------------------------------------------
ALIASES = {
    "brazil": "Brazil", "brasil": "Brazil",
    "netherlands": "Netherlands", "holanda": "Netherlands",
    "united kingdom": "United Kingdom", "uk": "United Kingdom",
    "reino unido": "United Kingdom", "inglaterra": "United Kingdom",
    "ireland": "Ireland", "irlanda": "Ireland",
    "poland": "Poland", "polonia": "Poland", "polônia": "Poland",
    "germany": "Germany", "alemanha": "Germany",
    "switzerland": "Switzerland", "suica": "Switzerland", "suíça": "Switzerland", "swiss": "Switzerland",
    "luxembourg": "Luxembourg", "luxemburgo": "Luxembourg",
    "denmark": "Denmark", "dinamarca": "Denmark",
    "norway": "Norway", "noruega": "Norway",
    "canada": "Canada",
    "sweden": "Sweden", "suécia": "Sweden", "suecia": "Sweden",
    "austria": "Austria", "áustria": "Austria", "austria ": "Austria",
    "finland": "Finland", "finlândia": "Finland", "finlandia": "Finland",
    "france": "France", "frança": "France", "franca": "France",
    "worldwide": "worldwide", "mundo": "worldwide", "global": "worldwide",
}

# Safe fallback when config.json is absent: search worldwide, remote-only.
DEFAULT_CONFIG = {
    "search_query": '"Site Reliability Engineer" OR "SRE" OR "Infrastructure Engineer" OR "Cloud Infrastructure" OR "DevOps"',
    "results_per_country": 600,
    "hours_old": 36,
    "targets": [{"country": "worldwide", "work_mode": "remote"}],
    "title_blocklist": [],
    "company_blocklist": [
        "jobgether", "quik hire staffing", "lensa", "grabjobs", "jobrapido",
    ],
    "brazil_remote_only": False,
    "work_modes": ["remote", "hybrid", "local"],
    # Portais de carreira de bancos brasileiros (fonte Gupy e/ou Workday).
    "banks": [],
    "bank_results_per_bank": 100,
    "bank_include_keywords": [],
    # Portais InHire (vagas diretas). enabled=false => não entra no 'all'.
    "inhire": {
        "enabled": False,
        "companies": [],
        "companies_file": "data/inhire_companies.json",
        "results_per_company": 120,
        "include_keywords": [],
    },
}


DEFAULT_SYSTEM_PROMPT = """Você é um Tech Recruiter Sênior especializado em Infra, Cloud, DevOps, SRE e Observabilidade.

Avalie o FIT entre candidato e vaga e reescreva o RESUMO PROFISSIONAL para maximizar o match ATS sem inventar experiência.

REGRA ABSOLUTA:
O currículo é a única fonte de verdade sobre as competências do candidato.
A descrição da vaga NÃO é evidência de que o candidato possui uma tecnologia.

CLASSIFIQUE CADA REQUISITO:

hands_on = uso profissional comprovado no currículo.
relacionadas = experiência prática em tecnologia/conceito diferente, mas relevante para o requisito.
conhecimento = curso, certificação, estudo ou conhecimento sem experiência profissional comprovada.
ausentes = não há evidência.

Nunca inferir hands_on pela simples presença de uma tecnologia na lista de skills, certificação ou descrição da vaga.

Exemplo:
AWS aparece no perfil + AWS Cloud Practitioner, mas não há atuação profissional descrita com AWS:
AWS = conhecimento.

OCI aparece associado a migração/administração/operação:
OCI = hands_on.

Se houver dúvida, NÃO classifique como hands_on.

TRANSFERIBILIDADE:
Quando a vaga exigir uma tecnologia que o candidato não domina profissionalmente, destaque uma experiência relacionada REAL.

AWS → OCI/Cloud
Datadog → Grafana/Zabbix/New Relic/Elastic APM
CloudWatch → experiência real em observabilidade/monitoramento
Azure → OCI/Cloud
Prometheus → Grafana/Zabbix/observabilidade
Python/Go → automação de infraestrutura, NÃO desenvolvimento de software

Nunca substituir uma tecnologia por outra ou afirmar experiência na tecnologia exigida.

PERFIL:
O perfil real do candidato (currículo, skills por nível, experiências) é injetado no
payload como profile_context — use-o como única fonte de verdade, não este texto.

REJEITAR / SCORE 0:
sem descrição; QA/Tester; Backend/Frontend/Full Stack puro; Helpdesk/Service Desk; Scrum/Product; gestão; presencial no Brasil; internacional sem possibilidade de contratação no Brasil ou com exigência de direito de trabalho/cidadania local.

LOCAL:
Preferência: remoto BR > remoto internacional aceitando Brasil > relocation > híbrido BR.
No híbrido considerar cidade, frequência e salário informado.

RESUMO:
200-400 palavras.
Priorize o que mais combina com a vaga.
Use keywords da vaga quando forem verdadeiras.
Use experiências relacionadas para demonstrar transferibilidade.
Nunca transforme conhecimento em hands-on.
O texto precisa ser defensável em entrevista.
score menor que 69 colocar na CORRECAO_RESUMO_ATS e SKILLS_DA_VAGA como "N/A"

FORMATO DOS ARRAYS DE SKILLS:
- Liste APENAS o nome da tecnologia/conceito, sem explicações
- NÃO use parênteses, notas ou comentários dentro dos arrays
- "hands_on": só tecnologias com uso profissional comprovado no currículo
- "relacionadas": tecnologias diferentes mas com experiência real transferível
- "conhecimento": cursos, certificações, estudos sem prática profissional
- "ausentes": tecnologias exigidas na vaga mas sem evidência no currículo

EXEMPLO:
✅ CORRETO:
"hands_on": ["Docker", "Grafana"]
"relacionadas": ["New Relic (para vaga que exige Datadog)"]  ← se transferível
"conhecimento": ["Kubernetes", "AWS"]
"ausentes": ["Helm", "Service Mesh"]

❌ ERRADO:
"hands_on": ["Docker (uso profissional)"]
"conhecimento": ["AWS (certificação)"]
"ausentes": ["Helm (não tem)"]"""


def carregar_config() -> dict:
    """Load external config.json if present, else return safe DEFAULT_CONFIG."""
    if not os.path.exists(ARQUIVO_CONFIG):
        print("⚙️  config.json ausente — usando fallback seguro (worldwide, remote-only).")
        return dict(DEFAULT_CONFIG)
    try:
        with open(ARQUIVO_CONFIG, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        # Merge with defaults for any missing keys
        merged = dict(DEFAULT_CONFIG)
        merged.update({k: v for k, v in cfg.items() if v is not None})
        print(f"⚙️  config.json carregado ({len(merged.get('targets', []))} alvo(s) configurado(s)).")
        return merged
    except Exception as e:
        print(f"⚠️  Falha ao ler config.json ({e}) — usando fallback seguro.")
        return dict(DEFAULT_CONFIG)


def carregar_perfil():
    """Load profile.yaml for validation/logging only.

    Does NOT change any pipeline decision yet: a missing or invalid profile is
    non-fatal and simply logs a warning (fail-safe, same as config.json).
    """
    try:
        perfil = load_profile(ARQUIVO_PERFIL)
    except ProfileError as e:
        print(f"👤 profile.yaml indisponível ({e}) — seguindo sem perfil estruturado.")
        return None

    seniority = perfil.get("seniority", {}) or {}
    primary = (perfil.get("target_roles", {}) or {}).get("primary", []) or []
    print(f"👤 Perfil carregado: alvo={seniority.get('target', '?')} | "
          f"cargos-alvo={', '.join(primary) if primary else '?'}")
    return perfil


def carregar_inhire_companies(path=ARQUIVO_INHIRE):
    """Load InHire tenant slugs from the discovered-companies file."""
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            companies = data.get("companies", []) or []
            disabled = set(data.get("disabled", []) or [])
            return [c for c in companies if c not in disabled]
        return list(data) if isinstance(data, list) else []
    except Exception as e:
        print(f"⚠️ Falha ao ler {path}: {e}")
        return []


def parse_args():
    parser = argparse.ArgumentParser(
        description="CareerOps - scraper de vagas SRE/DevOps/Cloud."
    )
    parser.add_argument(
        "--country", "-c",
        help="Pais para varredura isolada (ex: Brazil, holanda, uk, worldwide). "
             "Sem esta flag, varre a lista global completa.",
    )
    parser.add_argument(
        "--countries",
        help="Lista de países separados por vírgula para UMA execução única "
             "(ex: 'Brazil,Netherlands,Poland'). Filtra a lista completa do código.",
    )
    parser.add_argument(
        "--all", "-a", action="store_true",
        help="Roda TODOS os alvos da lista do código numa execução só "
             "(equivalente a MODO GLOBAL, mas explícito).",
    )
    parser.add_argument(
        "--list", "-l", action="store_true",
        help="Apenas lista os alvos que SERIAM varridos e sai (sem rede).",
    )
    parser.add_argument(
        "--digest", dest="digest", action="store_true", default=None,
        help="Dispara o digest diario (n8n -> Telegram) ao terminar.",
    )
    parser.add_argument(
        "--no-digest", dest="digest", action="store_false",
        help="NAO dispara o digest ao terminar.",
    )
    parser.add_argument(
        "--source",
        choices=["jobspy", "linkedin", "indeed", "banks", "gupy", "workday", "inhire", "all"],
        default=None,
        help="Fonte de vagas: 'jobspy' (LinkedIn+Indeed), 'banks' (Gupy/Workday BR), "
             "'inhire' (portais InHire) ou 'all' (jobspy + banks + InHire se habilitado). "
             "Sem a flag, usa config.json → scan_sources (padrão: all).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Coleta + filtra e mostra o resultado SEM enviar nada ao n8n "
             "(não grava memória, não dispara digest).",
    )
    parser.add_argument(
        "--sources",
        help="Fontes separadas por vírgula para ESTA execução (ex: linkedin,indeed "
             "ou gupy,workday,inhire). Sobrepõe config.json → scan_sources.",
    )
    args = parser.parse_args()

    # Resolução de país isolado (--country)
    if args.country:
        key = args.country.strip().lower()
        args.country_resolved = ALIASES.get(key, args.country)
    else:
        args.country_resolved = None

    # Resolução de lista (--countries) -> lista de nomes canônicos
    if args.countries:
        args.countries_resolved = []
        for c in args.countries.split(","):
            c = c.strip()
            if not c:
                continue
            args.countries_resolved.append(ALIASES.get(c.lower(), c))
    else:
        args.countries_resolved = None

    return args


def carregar_memoria():
    if not os.path.exists(ARQUIVO_MEMORIA):
        return set()
    with open(ARQUIVO_MEMORIA, "r", encoding="utf-8") as f:
        return set(f.read().splitlines())


def salvar_na_memoria(url):
    with open(ARQUIVO_MEMORIA, "a", encoding="utf-8") as f:
        f.write(f"{url}\n")


def _rescue_pattern(perfil) -> str:
    """Word-boundary regex of the profile's target-role keywords.

    A title matching these ("palavras que eu busco": SRE, DevOps, Cloud, Infra…)
    is rescued from the title blocklist — target keywords have priority.
    """
    if not perfil:
        return ""
    matching = perfil.get("title_matching") or {}
    kws: list[str] = []
    for entry in matching.get("target", []) or []:
        for k in entry.get("keywords", []) or []:
            if str(k).strip():
                kws.append(str(k).strip())
    return _block_pattern(kws)


def main():
    args = parse_args()
    cfg = carregar_config()
    perfil = carregar_perfil()  # valida/loga o perfil; usado pelo pré-filtro (9.1)
    profile_context = build_profile_context(perfil) if perfil else ""
    scorer = build_scorer(perfil) if perfil else None
    tel.start_run("scan", source=args.source, country=args.country_resolved, dry_run=args.dry_run)

    query = cfg.get("search_query", DEFAULT_CONFIG["search_query"])
    results_per_country = cfg.get("results_per_country", DEFAULT_CONFIG["results_per_country"])
    results_per_source = cfg.get("results_per_source")
    if results_per_source not in (None, "", 0):
        results_per_country = int(results_per_source)
    hours_old = int(cfg.get("hours_old", DEFAULT_CONFIG.get("hours_old", 36)))

    # Targets from config.json → alvos list; work_mode maps to is_remote:
    #   "remote"  → is_remote=True only
    #   "onsite"  → is_remote=False only
    #   "any"     → both True and False (two entries)
    #   "hybrid"  → treated as onsite+remote (both)
    work_modes = cfg.get("work_modes")
    raw_targets = cfg.get("targets", DEFAULT_CONFIG["targets"])
    alvos = []
    for t in raw_targets:
        pais = t.get("country")
        modo = (t.get("work_mode") or "any").lower()
        if modo == "remote":
            alvos.append({"pais": pais, "remoto": True})
        elif modo == "onsite":
            alvos.append({"pais": pais, "remoto": False})
        else:  # "any" or "hybrid" → both modes
            alvos.append({"pais": pais, "remoto": False})
            alvos.append({"pais": pais, "remoto": True})
    if work_modes is not None:
        _modes = normalize_modes(work_modes)
        _paises: list = []
        for _t in raw_targets:
            _p = _t.get("country")
            if _p and _p not in _paises:
                _paises.append(_p)
        alvos = []
        for _p in _paises:
            if _modes == {"remote"}:
                alvos.append({"pais": _p, "remoto": True})
            elif _modes == {"local"}:
                alvos.append({"pais": _p, "remoto": False})
            else:
                alvos.append({"pais": _p, "remoto": False})
                alvos.append({"pais": _p, "remoto": True})
    tel.set_state(targets=[f"{a['pais']}:{'remote' if a['remoto'] else 'onsite'}" for a in alvos])

    brazil_remote_only = cfg.get("brazil_remote_only", False)
    # Quando work_modes existe, as modalidades (remote/hybrid/local) governam.
    if work_modes is not None:
        brazil_remote_only = False
    title_blocklist_cfg = cfg.get("title_blocklist", [])
    company_blocklist_cfg = cfg.get("company_blocklist", DEFAULT_CONFIG["company_blocklist"]) or []
    banks_all = [
        b for b in (cfg.get("banks", DEFAULT_CONFIG["banks"]) or [])
        if b.get("enabled", True) is not False
    ]
    bank_results = cfg.get("bank_results_per_bank", DEFAULT_CONFIG["bank_results_per_bank"])
    if results_per_source not in (None, "", 0):
        bank_results = int(results_per_source)
    bank_include = cfg.get("bank_include_keywords", DEFAULT_CONFIG["bank_include_keywords"]) or []

    # Portais InHire (vagas diretas). Lista de empresas vem do config ou do
    # arquivo descoberto por `careerops.collector.inhire_discovery`.
    inhire_cfg = cfg.get("inhire", DEFAULT_CONFIG["inhire"]) or {}
    inhire_companies = inhire_cfg.get("companies") or carregar_inhire_companies(
        inhire_cfg.get("companies_file", ARQUIVO_INHIRE)
    )
    inhire_include = inhire_cfg.get("include_keywords") or []
    inhire_results = inhire_cfg.get("results_per_company", 30)
    if results_per_source not in (None, "", 0):
        inhire_results = int(results_per_source)
    inhire_enabled = bool(inhire_cfg.get("enabled", False))

    # Bancos são brasileiros: só fazem sentido numa varredura Brasil/global.
    eh_brasil = (
        (args.country_resolved and args.country_resolved.lower() == "brazil")
        or (args.countries_resolved and any(c.lower() == "brazil" for c in args.countries_resolved))
        or (not args.country_resolved and not args.countries_resolved)
    )
    scan_sources = cfg.get("scan_sources")
    if args.sources:
        scan_sources = [s.strip().lower() for s in args.sources.split(",") if s.strip()]
    if args.source is not None:
        source = args.source
        if source in ("jobspy", "all"):
            jobspy_sites = {"linkedin", "indeed"}
        elif source in ("linkedin", "indeed"):
            jobspy_sites = {source}
        else:
            jobspy_sites = set()
        platforms = {"gupy", "workday"} if source in ("banks", "all") else ({source} if source in ("gupy", "workday") else set())
        run_inhire = (
            source in ("inhire", "all") and inhire_enabled
            and bool(inhire_companies) and eh_brasil
        )
    elif scan_sources:
        source = "config"
        selected = {str(s).strip().lower() for s in scan_sources}
        if "banks" in selected:
            selected |= {"gupy", "workday"}
        if "jobspy" in selected:
            selected |= {"linkedin", "indeed"}
        jobspy_sites = selected & {"linkedin", "indeed"}
        platforms = selected & {"gupy", "workday"}
        run_inhire = "inhire" in selected and bool(inhire_companies) and eh_brasil
        print(f"🧭 Fontes (config.scan_sources): {', '.join(sorted(selected)) or '(nenhuma)'}")
        if "other" in selected:
            print("⚠️ Outros (em testes): nenhuma fonte implementada ainda — ignorado.")
    else:
        source = "all"
        jobspy_sites = {"linkedin", "indeed"}
        platforms = {"gupy", "workday"}
        run_inhire = inhire_enabled and bool(inhire_companies) and eh_brasil
    run_jobspy = bool(jobspy_sites)

    banks_cfg = [b for b in banks_all if (b.get("source") or "gupy") in platforms]
    run_banks = bool(banks_cfg) and eh_brasil

    # ----------------------------------------------------------------------
    # Filtro por país / países / all (modos de varredura)
    # ----------------------------------------------------------------------
    if args.country_resolved:
        # Modo país isolado (--country)
        alvos = [a for a in alvos if a["pais"].lower() == args.country_resolved.lower()]
        if not alvos:
            validos = sorted({a["pais"] for a in alvos_ordem_completa()})
            print(f"❌ País '{args.country}' (resolvido: '{args.country_resolved}') "
                  f"não encontrado na lista de alvos.")
            print("Paises válidos (e aliases PT):")
            for nome in validos:
                aliases_do = [k for k, v in ALIASES.items() if v == nome]
                print(f"   - {nome}" + (f"  (alias: {', '.join(aliases_do)})" if aliases_do else ""))
            return

    elif args.countries_resolved:
        # Modo lista explícita (--countries "Brazil,Netherlands,...")
        alvos = [a for a in alvos if a["pais"] in args.countries_resolved]
        if not alvos:
            validos = sorted({a["pais"] for a in alvos_ordem_completa()})
            print(f"❌ Nenhum dos países '{args.countries}' encontrado na lista de alvos.")
            print("Paises válidos (e aliases PT):")
            for nome in validos:
                aliases_do = [k for k, v in ALIASES.items() if v == nome]
                print(f"   - {nome}" + (f"  (alias: {', '.join(aliases_do)})" if aliases_do else ""))
            return

    # Modo de execução (rótulo)
    if args.country_resolved:
        print(f"🌍 MODO PAÍS: varredura restrita a '{args.country_resolved}' "
              f"({len(alvos)} alvo(s)).")
    elif args.countries_resolved:
        print(f"🌍 MODO LISTA: varredura de {len(args.countries_resolved)} país(es) "
              f"({len(alvos)} alvo(s)).")
    else:
        print(f"🌎 MODO GLOBAL: varredura completa ({len(alvos)} alvos).")


    # ----------------------------------------------------------------------
    # --list: apenas mostra o que seria varrido e sai (sem tocar na rede)
    # ----------------------------------------------------------------------
    if args.list:
        print("\nAlvos que seriam varridos:")
        for a in alvos:
            print(f"   - {a['pais']} | remoto={a['remoto']}")
        print(f"\nFontes: {source}")
        if run_banks:
            print("Empresas (Gupy/Workday):")
            for b in banks_cfg:
                print(f"   - {b.get('name', b.get('subdomain'))} ({b.get('subdomain')})")
        if run_inhire:
            print(f"InHire ({len(inhire_companies)} portal(is)):")
            for slug in inhire_companies:
                print(f"   - {slug}")
        return

    # Decisão do digest: country run => off por padrão (evita spam);
    # full run => on por padrão (mantém comportamento anterior).
    fire_digest = args.digest if args.digest is not None else (args.country_resolved is None)
    print(f"📲 Digest ao final: {'SIM' if fire_digest else 'NÃO'}")

    todas_vagas = []

    # 1. Coleta os dados de todos os países, Busca N vagas por país (do config)
    if run_jobspy:
        tel.source_start("jobspy")
        n_jobspy = 0
        for alvo in alvos:
            try:
                jobs_df = fetch_recent_jobs(
                    search_term=query,
                    location=alvo['pais'],
                    is_remote=alvo['remoto'],
                    results_wanted=results_per_country,
                    hours_old=hours_old,
                    sites=sorted(jobspy_sites),
                )
                if not jobs_df.empty:
                    todas_vagas.append(jobs_df)
                    n_jobspy += len(jobs_df)
                    for _, _r in jobs_df.head(200).iterrows():
                        tel.job_seen(_r, "jobspy")
            except Exception as e:
                print(f"❌ Erro ao buscar no(a) {alvo['pais']}: {e}")
                tel.event("source_error", source="jobspy", error=str(e))
        tel.source_end("jobspy", n_jobspy)

    # 1b. Portais de carreira dos bancos brasileiros (Gupy + Workday).
    #     Já saem filtrados por título relevante + remoto (regra Brasil).
    if run_banks:
        print(f"\n🏢 Varrendo {len(banks_cfg)} empresa(s) via Gupy/Workday...")
        tel.source_start("banks")
        banks_df = pd.DataFrame()
        try:
            banks_df = fetch_bank_jobs(
                banks=banks_cfg,
                include_keywords=bank_include,
                results_wanted=bank_results,
                remote_only_br=brazil_remote_only,
            )
            if not banks_df.empty:
                todas_vagas.append(banks_df)
                for _, _r in banks_df.head(300).iterrows():
                    tel.job_seen(_r, "banks")
        except Exception as e:
            print(f"❌ Erro na varredura de bancos: {e}")
            tel.event("source_error", source="banks", error=str(e))
        tel.source_end("banks", len(banks_df))

    # 1c. Portais InHire (vagas diretas). Já saem com workplaceType mapeado.
    if run_inhire:
        print(f"\n🏢 Varrendo {len(inhire_companies)} portal(is) InHire...")
        tel.source_start("inhire")
        inhire_df = pd.DataFrame()
        try:
            inhire_df = fetch_inhire_jobs(
                companies=inhire_companies,
                include_keywords=inhire_include,
                results_per_company=inhire_results,
            )
            if not inhire_df.empty:
                todas_vagas.append(inhire_df)
                for _, _r in inhire_df.head(400).iterrows():
                    tel.job_seen(_r, "inhire")
        except Exception as e:
            print(f"❌ Erro na varredura InHire: {e}")
            tel.event("source_error", source="inhire", error=str(e))
        tel.source_end("inhire", len(inhire_df))

    if not todas_vagas:
        print("📭 Nenhuma vaga encontrada na varredura de hoje.")
        tel.finish("finished", reason="no_jobs")
        return

    df_final = pd.concat(todas_vagas, ignore_index=True)
    df_final = df_final.fillna("").astype(str)

    # =========================================================================
    # 2. Blocklist (Interceptor de Lixo Inteligente - SEPARADO)
    # =========================================================================

    # 🔴 APLICADO APENAS AO TÍTULO DA VAGA (Remove cargos fora do seu escopo)
    # Fonte: config.json "title_blocklist" se presente; senão usa BASE_TITLE_BLOCKLIST.
    BASE_TITLE_BLOCKLIST = [
        # Níveis e Estágios
        "estágio", "junior", "jr", "pleno", "mid-level",

        # Testes e Qualidade
        "qa", "tester", "test automation", "quality engineer", "test engineer",

        # Suporte e Redes Clássicas
        "suporte", "service desk", "support", "helpdesk", "network engineer",
        "netwerkbeheerder", "lan", "wan", "telecom",

        # Gestão e Negócios
        "manager", "head of", "director", "delivery manager", "business analyst",
        "analista de negócios", "scrum", "agile", "product", "analyst", "analista",

        # Desenvolvedores e Engenharia de Software
        "backend", "front-end", "frontend", "fullstack", "full stack",
        "software engineer", "developer", "desenvolvedor", "java", "c#", ".net",
        "rust", "golang", "go ", "php", "flutter", "ios", "android", "mobile",
        "c++", "angular", "react", "unreal", "softwareentwickler", "ontwikkelaar", "utvikler",

        # Dados, IA e Bancos de Dados
        "data engineer", "data platform", "engenheiro de dados", "cientista de dados",
        "data scientist", "machine learning", "mlops", "ai engineer", "ia generativa",
        "genai", "artificial intelligence", "databricks", "snowflake", "big data",
        "power bi", "power platform", "dba", "database administrator", "administrador de dados",

        # Sistemas Locais e ERPs
        "sap", "protheus", "dynamics", "murex", "systeembeheerder", "systemadministrator:in",
        "systemingenieur", "plattformingeniør", "sales", "m/w/d", "m/f/d", "w/m/d", "w/m/x", "all genders"
    ]
    palavras_proibidas_titulo = list(title_blocklist_cfg) if title_blocklist_cfg else BASE_TITLE_BLOCKLIST

    # 🔴 APLICADO APENAS À DESCRIÇÃO (Restrições imigratórias e contratuais)
    palavras_proibidas_descricao = [
        # Vistos e Cidadania
        "no visa sponsorship", "no sponsorship", "without sponsorship",
        "must have right to work", "valid eu work permit", "eu citizen",
        "uk citizen", "must be a citizen", "right to work in the uk",
        "right to work in canada", "eligibility to work in", "uk resident",

        # Clearances Militares/Governamentais
        "clearance", "security clearance", "sc clearance", "dv clearance",
        "nato clearance", "secret clearance", "ts/sci",

        # Idiomas: Alemão (Filtra barreiras em DACH - Alemanha, Áustria, Suíça)
        "sehr gute deutsch", "gute deutsch", "deutschkenntnisse",
        "deutsch in wort", "fließend deutsch", "fluent in german",
        "german language", "b1 deutsch", "b2 deutsch", "c1 deutsch",
        "c2 deutsch", "muttersprache deutsch",

        # Idiomas: Francês (Filtra barreiras na França, Suíça e Quebec)
        "courant français", "francais courant", "fluent in french",
        "french language", "maîtrise du français", "maitrise du francais",
        "connaissance du français", "french fluent", "parler français", "bilingue",

        # Idiomas: Holandês e Nórdicos (Filtra barreiras na Holanda e Escandinávia)
        "vloeiend nederlands", "fluent in dutch", "dutch language",
        "nederlandse taal", "goed nederlands", "beheersing van de nederlandse",
        "fluent in norwegian", "flytende norsk", "fluent in swedish",
        "flytanda svenska", "fluent in danish", "dansk",

        # Modalidades de subemprego/estudante europeu
        "werkstudent", "alternance", "stage", "apprenticeship", "internship"
    ]

    # Aplica o filtro pesado apenas nos títulos
    total_antes_txt = len(df_final)
    padrao_titulo = _block_pattern(palavras_proibidas_titulo)
    padrao_descricao = _block_pattern(palavras_proibidas_descricao)
    padrao_resgate = _rescue_pattern(perfil)

    if not df_final.empty:
        mask_titulo = df_final['title'].str.contains(padrao_titulo, case=False, na=False)
        mask_descricao = df_final['description'].str.contains(padrao_descricao, case=False, na=False)
        # Prioridade: título com palavra-alvo (SRE/DevOps/Cloud/Infra) sobrevive ao
        # blocklist de título ("Software Engineer (SRE)" é liberado pelo SRE).
        mask_resgate = (
            df_final['title'].str.contains(padrao_resgate, case=False, na=False)
            if padrao_resgate else pd.Series(False, index=df_final.index)
        )
        mask = (mask_titulo & ~mask_resgate) | mask_descricao
        for _, r in df_final[mask].iterrows():
            descricao = str(r.get('description') or '')
            motivo = ("description_blocklist" if re.search(padrao_descricao, descricao, re.IGNORECASE)
                      else "title_blocklist")
            tel.discarded_job("title_or_description", motivo, r)
        df_final = df_final[~mask]
    tel.filtered("title_or_description", total_antes_txt - len(df_final))

    # 2b. Blocklist de EMPRESAS (aggregators/spam que não são empregadores reais)
    if company_blocklist_cfg and not df_final.empty:
        palavras_proibidas_empresa = [p.lower() for p in company_blocklist_cfg]
        padrao_empresa = _block_pattern(palavras_proibidas_empresa)
        total_antes_emp = len(df_final)
        mask_empresa = df_final['company'].astype(str).str.lower().str.contains(padrao_empresa, na=False)
        for _, r in df_final[mask_empresa].iterrows():
            tel.discarded_job("company_blocklist", "empresa bloqueada", r)
        df_final = df_final[~mask_empresa]
        vagas_empresa = total_antes_emp - len(df_final)
        if vagas_empresa > 0:
            print(f"🚫 Descartadas {vagas_empresa} vagas de empresas bloqueadas (company_blocklist).")
            tel.filtered("company_blocklist", vagas_empresa)

    # 3. Anti-Duplicidade
    vagas_antigas = carregar_memoria()
    total_antes = len(df_final)
    if not df_final.empty:
        mask_antigas = df_final['job_url'].isin(vagas_antigas)
        for _, r in df_final[mask_antigas].iterrows():
            tel.discarded_job("already_processed", "já processada (memória)", r)
        df_final = df_final[~mask_antigas]
    vagas_repetidas = total_antes - len(df_final)

    # 3.1. Dedup do lote: mesma URL e/ou mesma vaga entre fontes
    #      (empresa + título + localização normalizados).
    registros = dedupe_jobs(df_final.to_dict('records'))
    vagas_duplicadas = len(df_final) - len(registros)
    if vagas_duplicadas > 0:
        for r in dedupe_jobs_dropped(df_final.to_dict('records')):
            tel.discarded_job("dedupe_batch", "duplicata no lote", r)
    df_final = pd.DataFrame(registros)

    if vagas_repetidas > 0:
        print(f"♻️  Descartadas {vagas_repetidas} vagas antigas já processadas.")
        tel.filtered("already_processed", vagas_repetidas)
    if vagas_duplicadas > 0:
        print(f"🧬 Descartadas {vagas_duplicadas} duplicata(s) no lote "
              f"(mesma URL ou empresa+título+localização).")
        tel.filtered("dedupe_batch", vagas_duplicadas)

    total_vagas = len(df_final)
    if total_vagas == 0:
        print("🛑 Todas as vagas já foram processadas anteriormente. Nada novo no front!")
        tel.finish("finished", reason="all_processed")
        return

    # =========================================================================
    # 3b. Filtro BRASIL: aceita remoto e híbrido, descarta presencial
    # =========================================================================
    antes_br = len(df_final)
    if brazil_remote_only:
        mask_br = df_final.apply(is_brazil_onsite, axis=1)
        for _, r in df_final[mask_br].iterrows():
            tel.discarded_job("brazil_onsite", "presencial no Brasil", r)
        df_final = df_final[~mask_br]
    descartadas_br = antes_br - len(df_final)
    if descartadas_br > 0:
        print(f"🇧🇷 Descartadas {descartadas_br} vaga(s) presenciais do Brasil "
              f"(remoto e híbrido são aceitos).")
        tel.filtered("brazil_onsite", descartadas_br)

    if work_modes is not None:
        _modes = normalize_modes(work_modes)
        if _modes and len(_modes) < 3:
            antes_mod = len(df_final)
            mask_mod = df_final.apply(lambda r: modality_allowed(r, _modes), axis=1)
            for _, r in df_final[~mask_mod].iterrows():
                tel.discarded_job("work_modes", "modalidade não habilitada", r)
            df_final = df_final[mask_mod]
            disc_mod = antes_mod - len(df_final)
            if disc_mod > 0:
                print(f"🧭 Descartadas {disc_mod} vaga(s) por modalidade "
                      f"(habilitadas: {', '.join(sorted(_modes))}).")
                tel.filtered("work_modes", disc_mod)

    total_vagas = len(df_final)
    if total_vagas == 0:
        print("🛑 Nenhuma vaga remoto/híbrido relevante inédita restante.")
        tel.finish("finished", reason="no_relevant_after_br")
        return

    # =========================================================================
    # 3c. Pré-filtro determinístico por título (Fase 9.1)
    #     Regras em profile.yaml (title_matching). Conservador: só descarta
    #     função claramente fora do perfil; títulos ambíguos são preservados.
    # =========================================================================
    if perfil is not None:
        rules = build_rules(perfil)
        df_final, pre_stats = apply_prefilter(df_final, rules)
        print(f"\n🎯 Pré-filtro por título: mantidas={pre_stats['kept']} | "
              f"descartadas={pre_stats['discarded']} | ambíguas={pre_stats['ambiguous']}")
        for reason, count in sorted(pre_stats["by_reason"].items()):
            print(f"   - {reason}: {count}")
        for detail in pre_stats.get("dropped_details", []):
            tel.discarded_job("prefilter_title", detail.get("reason", "fora do perfil"), detail)
        tel.filtered("prefilter_title", pre_stats["discarded"], ambiguous=pre_stats["ambiguous"])
    else:
        print("\n🎯 Pré-filtro por título: ignorado (profile.yaml indisponível).")

    total_vagas = len(df_final)
    if total_vagas == 0:
        print("🛑 Nenhuma vaga relevante restante após o pré-filtro.")
        tel.finish("finished", reason="no_relevant_after_prefilter")
        return

    print(f"\n✅ Sobraram {total_vagas} vagas INÉDITAS (remoto/híbrido).\n")
    print("-" * 60)

    # --dry-run: mostra o que seria enviado e encerra (sem n8n/memória/digest)
    if args.dry_run:
        for index, row in df_final.iterrows():
            print(f"📦 [{row.get('location', '')}] {row.get('company', '')} - {row.get('title', '')}")
            tel.job_analyzed(row, status="dry_run")
        print("-" * 60)
        print("🧪 MODO DRY-RUN: nada foi enviado ao n8n, memória não alterada, digest não disparado.")
        tel.finish("finished", reason="dry_run", total=total_vagas)
        return

    sucesso = 0
    # 4. Envio para Orquestração no n8n
    for index, row in df_final.iterrows():
        payload = row.to_dict()
        # Contexto compacto do perfil (Fase 9.2) — complementa o currículo.
        payload["profile_context"] = profile_context
        # System prompt editável no dashboard (sem republicar o n8n).
        payload["system_prompt"] = str(cfg.get("prompt") or DEFAULT_SYSTEM_PROMPT)
        # Corte da descrição para economizar tokens na IA.
        max_desc = int(cfg.get("max_description_chars") or 4000)
        desc = str(payload.get("description") or "")
        if len(desc) > max_desc:
            payload["description"] = desc[:max_desc]
        # Score determinístico por perfil (Fase 9.3) — enviado como complemento.
        if scorer is not None:
            det = scorer.score(row)
            payload["det_score"] = det.score
            payload["det_rationale"] = det.rationale
        titulo = row.get('title', 'Desconhecido')
        empresa = row.get('company', 'Desconhecida')
        local = row.get('location', 'Global')
        url_vaga = row.get('job_url', '')

        print(f"📦 [{local}] {empresa} - {titulo}")

        try:
            response = requests.post("http://n8n:5678/webhook/careerops-jobs", json=payload)
            if response.status_code == 200:
                print("   ↳ ✅ Recebida pelo n8n")
                sucesso += 1
                if url_vaga:
                    salvar_na_memoria(url_vaga)
                tel.job_analyzed(row, det_score=payload.get("det_score"), status="sent")
                tel.record_sent(payload)
            else:
                print(f"   ↳ ❌ Erro HTTP {response.status_code}")
                tel.event("send_error", company=empresa, title=titulo, status_code=response.status_code)

        except Exception as e:
            print(f"   ↳ ❌ Falha de Conexão: {e}")
            tel.event("send_error", company=empresa, title=titulo, error=str(e))

        # ◄ FREIO ABS ATIVADO (Protege o Google Sheets contra o Erro 429)
        time.sleep(2.5)

    print("-" * 60)
    print(f"🏁 Orquestração Finalizada: {sucesso}/{total_vagas} vagas enviadas de forma segura!")

    # =========================================================================
    # 5. Disparo do fluxo de DIGEST (resumo diário via n8n -> Telegram)
    #    Controlado por --digest/--no-digest. Por padrão:
    #      - full run  => dispara
    #      - país run  => NÃO dispara (evita resumo repetido várias vezes/dia)
    # =========================================================================
    if fire_digest:
        try:
            resp = requests.post(
                "http://n8n:5678/webhook/careerops-digest",
                json={"trigger": "main_finished", "enviadas": sucesso, "total": total_vagas},
                timeout=10,
            )
            if resp.status_code == 200:
                print("📲 Digest diário disparado com sucesso (n8n -> Telegram).")
            else:
                print(f"⚠️  Digest retornou HTTP {resp.status_code} (não bloqueante).")
        except Exception as e:
            print(f"⚠️  Falha ao disparar o Digest (não bloqueante): {e}")
    else:
        print("📲 Digest não disparado nesta execução (use --digest para forçar).")

    tel.finish("finished", sent=sucesso, total=total_vagas)


def alvos_ordem_completa():
    """Retorna a lista completa de alvos (usado na mensagem de erro de país)."""
    return [
        {"pais": "Brazil"}, {"pais": "Netherlands"}, {"pais": "United Kingdom"},
        {"pais": "Ireland"}, {"pais": "Poland"}, {"pais": "Switzerland"},
        {"pais": "Luxembourg"}, {"pais": "Denmark"}, {"pais": "Norway"},
        {"pais": "Canada"}, {"pais": "Sweden"}, {"pais": "Austria"},
        {"pais": "Finland"}, {"pais": "France"}, {"pais": "worldwide"},
    ]


if __name__ == "__main__":
    main()
