from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    app_name: str = "LeadIntel"
    debug: bool = False

    # API Keys
    openai_api_key: str = ""
    serper_api_key: str = ""
    jina_api_key: str = ""
    apollo_api_key: str = ""
    apify_api_token: str = ""

    # Stripe
    stripe_secret_key: str = ""

    # Email (Resend)
    resend_api_key: str = ""

    # Supabase
    supabase_url: str = ""
    supabase_service_key: str = ""

    # Pipeline defaults
    cache_max_age_days: int = 30
    openai_model: str = "gpt-4.1-mini"
    openai_temperature: float = 0.3

    # Serper
    serper_results_per_query: int = 5

    # Jina
    max_jina_extractions: int = 7
    jina_max_content_length: int = 5000

    # Pipeline
    pipeline_max_concurrency: int = 5

    # Apify / LinkedIn
    apify_max_poll_seconds: int = 120
    apify_posts_limit: int = 5  
    linkedin_max_skills_for_ai: int = 10
    linkedin_max_posts_for_ai: int = 5

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
