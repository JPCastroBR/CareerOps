import os
import sys
import time
import json
import re
import argparse
import pandas as pd
import requests
from careerops.collector.jobspy import fetch_recent_jobs
from careerops.collector.banks import fetch_bank_jobs

ARQUIVO_MEMORIA = "vagas_processadas.txt"
ARQUIVO_CONFIG = "config.json"

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
    "results_per_country": 200,
    "targets": [{"country": "worldwide", "work_mode": "remote"}],
    "title_blocklist": [],
    "company_blocklist": [
        "jobgether", "quik hire staffing", "lensa", "grabjobs", "jobrapido",
    ],
    "brazil_remote_only": True,
    "inhire": {},
    # Portais de carreira de bancos brasileiros (fonte Gupy e/ou Workday).
    "banks": [],
    "bank_results_per_bank": 50,
    "bank_include_keywords": [],
}


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
        choices=["jobspy", "banks", "inhire", "all"],
        default="all",
        help="Fonte de vagas: 'jobspy' (LinkedIn+Indeed, padrão antigo), "
             "'banks' (portais de bancos BR via Gupy) ou 'all' (ambos). "
             "'inhire' habilita o adapter de tenants InHire (portal de carreira "
             "de empresas). Padrão: all.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Coleta + filtra e mostra o resultado SEM enviar nada ao n8n "
             "(não grava memória, não dispara digest).",
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


def main():
    # Subcomando InHire (Fase 11): `python -m careerops.main inhire <acao>`
    # Não interfere no parser principal quando não invocado.
    if len(sys.argv) > 1 and sys.argv[1] == "inhire":
        return inhire_cli(sys.argv[2:])

    args = parse_args()
    cfg = carregar_config()

    query = cfg.get("search_query", DEFAULT_CONFIG["search_query"])
    results_per_country = cfg.get("results_per_country", DEFAULT_CONFIG["results_per_country"])

    # Targets from config.json → alvos list; work_mode maps to is_remote:
    #   "remote"  → is_remote=True only
    #   "onsite"  → is_remote=False only
    #   "any"     → both True and False (two entries)
    #   "hybrid"  → treated as onsite+remote (both)
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

    brazil_remote_only = cfg.get("brazil_remote_only", True)
    title_blocklist_cfg = cfg.get("title_blocklist", [])
    company_blocklist_cfg = cfg.get("company_blocklist", DEFAULT_CONFIG["company_blocklist"]) or []
    banks_cfg = cfg.get("banks", DEFAULT_CONFIG["banks"]) or []
    bank_results = cfg.get("bank_results_per_bank", DEFAULT_CONFIG["bank_results_per_bank"])
    bank_include = cfg.get("bank_include_keywords", DEFAULT_CONFIG["bank_include_keywords"]) or []

    # Bancos são brasileiros: só fazem sentido numa varredura Brasil/global.
    eh_brasil = (
        (args.country_resolved and args.country_resolved.lower() == "brazil")
        or (args.countries_resolved and any(c.lower() == "brazil" for c in args.countries_resolved))
        or (not args.country_resolved and not args.countries_resolved)
    )
    source = args.source
    run_jobspy = source in ("jobspy", "all")
    run_banks = source in ("banks", "all") and bool(banks_cfg) and eh_brasil
    inhire_cfg = cfg.get("inhire", DEFAULT_CONFIG.get("inhire")) or {}
    run_inhire = (source == "inhire") or (source == "all" and bool(inhire_cfg))

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
            print("Bancos (Gupy/Workday):")
            for b in banks_cfg:
                print(f"   - {b.get('name', b.get('subdomain'))} ({b.get('subdomain')})")
        return

    # Decisão do digest: country run => off por padrão (evita spam);
    # full run => on por padrão (mantém comportamento anterior).
    fire_digest = args.digest if args.digest is not None else (args.country_resolved is None)
    print(f"📲 Digest ao final: {'SIM' if fire_digest else 'NÃO'}")

    todas_vagas = []

    # 1. Coleta os dados de todos os países, Busca N vagas por país (do config)
    if run_jobspy:
        for alvo in alvos:
            try:
                jobs_df = fetch_recent_jobs(
                    search_term=query,
                    location=alvo['pais'],
                    is_remote=alvo['remoto'],
                    results_wanted=results_per_country
                )
                if not jobs_df.empty:
                    todas_vagas.append(jobs_df)
            except Exception as e:
                print(f"❌ Erro ao buscar no(a) {alvo['pais']}: {e}")

    # 1b. Portais de carreira dos bancos brasileiros (Gupy + Workday).
    #     Já saem filtrados por título relevante + remoto (regra Brasil).
    if run_banks:
        print(f"\n🏦 Varrendo portais de {len(banks_cfg)} banco(s) brasileiro(s)...")
        try:
            banks_df = fetch_bank_jobs(
                banks=banks_cfg,
                include_keywords=bank_include,
                results_wanted=bank_results,
                remote_only_br=brazil_remote_only,
            )
            if not banks_df.empty:
                todas_vagas.append(banks_df)
        except Exception as e:
            print(f"❌ Erro na varredura de bancos: {e}")

    # 1c. Adapter InHire (tenants de empresas via portal de carreira próprio).
    #     Descobre → valida → coleta → faz matching local e deduplica.
    if run_inhire:
        print(f"\n🟣 Varrendo tenants InHire (descoberta + validação + coleta)...")
        try:
            from careerops.collector.inhire import fetch_inhire_jobs
            from careerops.collector.inhire.config import load_config as load_inhire_config
            inhire_df = fetch_inhire_jobs(cfg=load_inhire_config())
            if not inhire_df.empty:
                todas_vagas.append(inhire_df)
        except Exception as e:
            print(f"❌ Erro na varredura InHire: {e}")

    if not todas_vagas:
        print("📭 Nenhuma vaga encontrada na varredura de hoje.")
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
    padrao_titulo = '|'.join(re.escape(p) for p in palavras_proibidas_titulo)
    df_final = df_final[~df_final['title'].str.contains(padrao_titulo, case=False, na=False)]

    # Aplica o filtro de vistos apenas nas descrições
    padrao_descricao = '|'.join(re.escape(p) for p in palavras_proibidas_descricao)
    df_final = df_final[~df_final['description'].str.contains(padrao_descricao, case=False, na=False)]

    # 2b. Blocklist de EMPRESAS (aggregators/spam que não são empregadores reais)
    if company_blocklist_cfg and not df_final.empty:
        palavras_proibidas_empresa = [p.lower() for p in company_blocklist_cfg]
        padrao_empresa = '|'.join(re.escape(p) for p in palavras_proibidas_empresa)
        total_antes_emp = len(df_final)
        df_final = df_final[~df_final['company'].astype(str).str.lower().str.contains(padrao_empresa, na=False)]
        vagas_empresa = total_antes_emp - len(df_final)
        if vagas_empresa > 0:
            print(f"🚫 Descartadas {vagas_empresa} vagas de empresas bloqueadas (company_blocklist).")

    # 3. Anti-Duplicidade
    vagas_antigas = carregar_memoria()
    total_antes = len(df_final)
    df_final = df_final[~df_final['job_url'].isin(vagas_antigas)]
    vagas_repetidas = total_antes - len(df_final)

    if vagas_repetidas > 0:
        print(f"♻️  Descartadas {vagas_repetidas} vagas antigas já processadas.")

    total_vagas = len(df_final)
    if total_vagas == 0:
        print("🛑 Todas as vagas já foram processadas anteriormente. Nada novo no front!")
        return

    # =========================================================================
    # 3b. Filtro BRASIL: remoto + híbrido (presencial puro é descartado)
    # =========================================================================
    def eh_brasil_presencial(row):
        loc = str(row.get('location', '') or '')
        eh_br = bool(re.search(r'brasil|brazil', loc, re.IGNORECASE))
        if not eh_br:
            return False
        is_remote = row.get('is_remote', False)
        try:
            is_remote = bool(is_remote)
        except Exception:
            is_remote = False
        texto = ' '.join(str(row.get(c, '') or '') for c in ('title', 'description', 'job_type', 'job_level')).lower()
        # Híbrido é aceito; só descarta se for explicitamente presencial/onsite
        indica_presencial_puro = bool(re.search(r'\b(presencial|onsite|on-site|on site)\b', texto))
        # Se a vaga diz "híbrido" no texto, NÃO é presencial puro (é aceitável)
        indica_hibrido = bool(re.search(r'\b(h[íi]brido|hybrid)\b', texto))
        # Presencial puro = não remoto E (indica presencial E NÃO indica híbrido)
        return (not is_remote) and indica_presencial_puro and not indica_hibrido

    antes_br = len(df_final)
    if brazil_remote_only:
        df_final = df_final[~df_final.apply(eh_brasil_presencial, axis=1)]
    descartadas_br = antes_br - len(df_final)
    if descartadas_br > 0:
        print(f"🇧🇷 Descartadas {descartadas_br} vaga(s) do Brasil presencial pura (remoto + híbrido mantidos).")

    total_vagas = len(df_final)
    if total_vagas == 0:
        print("🛑 Nenhuma vaga remoto relevante inédita restante.")
        return

    print(f"\n✅ Sobraram {total_vagas} vagas INÉDITAS (remoto).\n")
    print("-" * 60)

    # --dry-run: mostra o que seria enviado e encerra (sem n8n/memória/digest)
    if args.dry_run:
        for index, row in df_final.iterrows():
            print(f"📦 [{row.get('location', '')}] {row.get('company', '')} - {row.get('title', '')}")
        print("-" * 60)
        print("🧪 MODO DRY-RUN: nada foi enviado ao n8n, memória não alterada, digest não disparado.")
        return

    sucesso = 0
    # 4. Envio para Orquestração no n8n
    for index, row in df_final.iterrows():
        payload = row.to_dict()
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
            else:
                print(f"   ↳ ❌ Erro HTTP {response.status_code}")

        except Exception as e:
            print(f"   ↳ ❌ Falha de Conexão: {e}")

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


def alvos_ordem_completa():
    """Retorna a lista completa de alvos (usado na mensagem de erro de país)."""
    return [
        {"pais": "Brazil"}, {"pais": "Netherlands"}, {"pais": "United Kingdom"},
        {"pais": "Ireland"}, {"pais": "Poland"}, {"pais": "Switzerland"},
        {"pais": "Luxembourg"}, {"pais": "Denmark"}, {"pais": "Norway"},
        {"pais": "Canada"}, {"pais": "Sweden"}, {"pais": "Austria"},
        {"pais": "Finland"}, {"pais": "France"}, {"pais": "worldwide"},
    ]


def inhire_cli(argv: list[str]) -> None:
    """Subcomando InHire (Fase 11).

    Uso:
        python -m careerops.main inhire discover   [--force] [--source seed|wayback|urlscan]
        python -m careerops.main inhire validate   [--force] [--limit N]
        python -m careerops.main inhire jobs       [--tenant T] [--min-score N] [--limit N]
        python -m careerops.main inhire run        [--force] [--min-score N]

    Flags opcionais: --force (revalida tudo), --limit, --tenant, --min-score.
    """
    import argparse

    from careerops.collector.inhire import (
        build_inhire_dataframe,
        collect_all_tenants,
        discover_tenants,
        validate_tenants,
    )
    from careerops.collector.inhire.config import InhireConfig, load_config
    from careerops.collector.inhire.http import InhireHttp
    from careerops.collector.inhire.repository import JobRepository, TenantRepository

    p = argparse.ArgumentParser(prog="careerops inhire", description="InHire tenant/job adapter")
    sub = p.add_subparsers(dest="acao", required=True)

    pd_ = sub.add_parser("discover", help="Descobre candidatos a tenant (sem validar)")
    pd_.add_argument("--force", action="store_true")
    pd_.add_argument("--source", help="seed|wayback|urlscan (sobrescreve config)")

    pv = sub.add_parser("validate", help="Valida tenants conhecidos via API pública")
    pv.add_argument("--force", action="store_true")
    pv.add_argument("--limit", type=int, default=0)

    pj = sub.add_parser("jobs", help="Coleta vagas dos tenants válidos")
    pj.add_argument("--tenant", help="slug específico")
    pj.add_argument("--min-score", type=int, default=0)
    pj.add_argument("--limit", type=int, default=0)

    pr = sub.add_parser("run", help="Pipeline completo: discover→validate→collect→dedupe")
    pr.add_argument("--force", action="store_true")
    pr.add_argument("--min-score", type=int, default=0)

    a = p.parse_args(argv)
    cfg = load_config()

    if a.acao == "discover":
        if a.source:
            cfg.sources = [s.strip() for s in a.source.split(",") if s.strip()]
        with InhireHttp(cfg.http) as http:
            found = discover_tenants(cfg, http=http)
        print(f"🔎 {len(found)} candidato(s) descoberto(s):")
        for slug, src in sorted(found.items()):
            print(f"   - {slug}  (source: {src})")
        if not found:
            print("💡 Dica: adicione slugs em data/inhire_seed.txt ou config.inhire.seed_tenants")

    elif a.acao == "validate":
        repo = TenantRepository()
        known = {t.slug for t in repo.all() if t.active}
        seed = set(load_seed_slugs_safe(cfg))
        slugs = sorted(known | seed)
        if a.limit:
            slugs = slugs[: a.limit]
        with InhireHttp(cfg.http) as http:
            recs = validate_tenants(slugs, cfg, repo=repo, http=http)
        ativos = [r for r in recs if r.active]
        print(f"✅ {len(ativos)}/{len(recs)} tenant(s) ativo(s).")
        for r in ativos:
            print(f"   - {r.slug}  jobs={r.jobs_count}")

    elif a.acao == "jobs":
        repo = TenantRepository()
        tenants = [t for t in repo.all() if t.active]
        if a.tenant:
            tenants = [t for t in tenants if t.slug == a.tenant.lower()]
        if not tenants:
            print("⚠️  Nenhum tenant ativo. Rode `inhire validate` primeiro.")
            return
        df, stats = collect_all_tenants(
            tenants, cfg=cfg, include_keywords=cfg.include_keywords, min_score=a.min_score
        )
        if a.min_score:
            df = df[df["match_score"].astype(float) >= a.min_score].reset_index(drop=True)
        print(f"📦 {len(df)} vaga(s) coletada(s). stats={stats}")
        for _, row in df.iterrows():
            print(f"   - [{row.get('match_score')}] {row.get('company')} | {row.get('title')} | {row.get('location')}")

    elif a.acao == "run":
        from careerops.collector.inhire import fetch_inhire_jobs
        df = fetch_inhire_jobs(cfg=cfg, min_score=a.min_score)
        if df.empty:
            print("📭 Nenhuma vaga InHire.")
            return
        if a.min_score:
            df = df[df["match_score"].astype(float) >= a.min_score].reset_index(drop=True)
        print(f"🟣 InHire run: {len(df)} vaga(s) após matching/dedupe.")
        for _, row in df.iterrows():
            print(f"   - [{row.get('match_score')}] {row.get('company')} | {row.get('title')} | {row.get('location')}")


def load_seed_slugs_safe(cfg):
    from careerops.collector.inhire.config import load_seed_slugs
    return load_seed_slugs(cfg)


if __name__ == "__main__":
    main()
