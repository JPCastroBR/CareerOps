import os
import time
import json
import argparse
import pandas as pd
import requests
from careerops.collector.jobspy import fetch_recent_jobs

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
    "brazil_remote_only": True,
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
        return

    # Decisão do digest: country run => off por padrão (evita spam);
    # full run => on por padrão (mantém comportamento anterior).
    fire_digest = args.digest if args.digest is not None else (args.country_resolved is None)
    print(f"📲 Digest ao final: {'SIM' if fire_digest else 'NÃO'}")

    todas_vagas = []

    # 1. Coleta os dados de todos os países, Busca N vagas por país (do config)
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
    padrao_titulo = '|'.join(palavras_proibidas_titulo)
    df_final = df_final[~df_final['title'].str.contains(padrao_titulo, case=False, na=False)]

    # Aplica o filtro de vistos apenas nas descrições
    padrao_descricao = '|'.join(palavras_proibidas_descricao)
    df_final = df_final[~df_final['description'].str.contains(padrao_descricao, case=False, na=False)]

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
    # 3b. Filtro BRASIL somente REMOTO (regra de ouro do candidato)
    # =========================================================================
    import re
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
        indica_presencial = bool(re.search(r'presencial|onsite|on-site|on site|híbrido|hibrido|hybrid', texto))
        return (not is_remote) or indica_presencial

    antes_br = len(df_final)
    if brazil_remote_only:
        df_final = df_final[~df_final.apply(eh_brasil_presencial, axis=1)]
    descartadas_br = antes_br - len(df_final)
    if descartadas_br > 0:
        print(f"🇧🇷 Descartadas {descartadas_br} vaga(s) do Brasil presencial/híbrida (fora do escopo remoto).")

    total_vagas = len(df_final)
    if total_vagas == 0:
        print("🛑 Nenhuma vaga remoto relevante inédita restante.")
        return

    print(f"\n✅ Sobraram {total_vagas} vagas INÉDITAS (remoto). Disparando para a Inteligência Artificial...\n")
    print("-" * 60)

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


if __name__ == "__main__":
    main()
