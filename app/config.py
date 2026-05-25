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

    # Supabase
    supabase_url: str = ""
    supabase_service_key: str = ""

    # Pipeline defaults
    cache_max_age_days: int = 30
    max_search_queries: int = 3
    max_jina_extractions: int = 5
    openai_model: str = "gpt-4.1-mini"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
