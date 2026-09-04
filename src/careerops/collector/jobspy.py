from jobspy import scrape_jobs
import pandas as pd

def fetch_recent_jobs(search_term: str, location: str, is_remote: bool = True, results_wanted: int = 20) -> pd.DataFrame:
    """
    Coleta vagas recentes usando a biblioteca JobSpy.
    """
    print(f"🔍 Buscando vagas para: {search_term} em {location} (Remoto: {is_remote})...")
    
    jobs_df = scrape_jobs(
        site_name=["linkedin", "indeed"],
        search_term=search_term,
        location=location,
        results_wanted=results_wanted,
        hours_old=4,
        is_remote=is_remote,
        country_linkedin=location,
        country_indeed=location,
        linkedin_fetch_description=True
    )
    
    return jobs_df