from jobspy import scrape_jobs
import pandas as pd

DEFAULT_HOURS_OLD = 36


def fetch_recent_jobs(
    search_term: str,
    location: str,
    is_remote: bool = True,
    results_wanted: int = 20,
    hours_old: int = DEFAULT_HOURS_OLD,
    sites: list[str] | None = None,
) -> pd.DataFrame:
    """Coleta vagas recentes usando a biblioteca JobSpy.

    ``hours_old`` controla a janela de busca (24h, 36h, 48h…). É configurável
    em ``config.json`` (``hours_old``) para ajustar ao perfil de cada pessoa.
    """
    site_names = [s for s in (sites or ["linkedin", "indeed"]) if s in {"linkedin", "indeed"}] or ["linkedin", "indeed"]
    print(f"🔍 Buscando vagas para: {search_term} em {location} "
          f"(Remoto: {is_remote}, horas: {hours_old}, sites: {', '.join(site_names)})...")

    jobs_df = scrape_jobs(
        site_name=site_names,
        search_term=search_term,
        location=location,
        results_wanted=results_wanted,
        hours_old=hours_old,
        is_remote=is_remote,
        country_linkedin=location,
        country_indeed=location,
        linkedin_fetch_description=True,
    )

    return jobs_df
