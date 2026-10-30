"""Router settings, read from environment variables or router/.env.

Every limit the plan names is a number here, so it can be changed without touching code.
"""
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_DIR = Path(__file__).resolve().parent
ROUTER_DIR = APP_DIR.parent

# Put provider keys (OPENAI_API_KEY, ...) into the environment so LiteLLM can find them.
load_dotenv(ROUTER_DIR / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROUTER_DIR / ".env", extra="ignore")

    # Model pool and routing
    models_file: str = str(APP_DIR / "models.yaml")
    routing_policy: str = "rules"   # rules (rules_policy.yaml) | static (default_chain for everything)
    rules_policy_file: str = str(APP_DIR / "rules_policy.yaml")
    default_chain: str = "gpt-4.1-mini,gpt-5-mini-low,gpt-4.1"  # used by the static policy
    reference_model: str = "gpt-5-medium"  # "strongest model" used for savings comparisons
    reference_search_model: str = "gpt-5-search"  # the reference for live-data questions (gpt-5-medium has no web)

    # Execution limits (plan: Routing logic > Hard execution limits)
    per_call_timeout_s: float = 20.0    # default; models.yaml can set timeout_s per option
    request_deadline_s: float = 110.0   # one slow reasoning call (75 s) plus a quick fallback
    max_model_calls: int = 4
    max_fallback_hops: int = 2
    max_retries_per_call: int = 1
    retry_backoff_s: float = 0.5
    breaker_failure_threshold: int = 5
    breaker_cooldown_s: float = 60.0

    # Request limits (plan: Security)
    max_request_bytes: int = 8_192
    default_max_tokens: int = 800
    max_tokens_cap: int = 1_000
    max_cost_per_request_usd: float = 0.05
    daily_budget_usd: float = 2.00
    rate_limit_per_minute: int = 10
    per_client_daily_limit: int = 50

    # Demo mode: scripted answers instead of real model calls (free; for trying the frontend)
    demo_mode: bool = False
    cors_origins: str = "http://localhost:3000"

    # Access control
    require_api_key: bool = False
    client_api_keys: str = ""   # comma-separated
    admin_api_keys: str = ""    # comma-separated; only admins may force a model
    trust_proxy_headers: bool = False

    # Logging (plan: metadata only by default)
    log_db_path: str = str(ROUTER_DIR / "router_logs.db")
    debug_log_text: bool = False   # local development only; text is redacted and kept 7 days
    debug_retention_days: int = 7

    @staticmethod
    def _split(value: str) -> list[str]:
        return [v.strip() for v in value.split(",") if v.strip()]

    @property
    def default_chain_ids(self) -> list[str]:
        return self._split(self.default_chain)

    @property
    def cors_origin_list(self) -> list[str]:
        return self._split(self.cors_origins)

    @property
    def client_keys(self) -> set[str]:
        return set(self._split(self.client_api_keys))

    @property
    def admin_keys(self) -> set[str]:
        return set(self._split(self.admin_api_keys))


@lru_cache
def get_settings() -> Settings:
    return Settings()
