from functools import lru_cache

from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


class Settings(BaseSettings):
    """Read from a .env file in the working directory, then from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Matches the postgres service in docker-compose.yml
    database_url: str = "postgresql+psycopg://revenueops:revenueops@localhost:5433/revenueops"

    # Which store the agents work on, and how to reach it
    store_adapter: str = "storeforge"
    storeforge_api_url: str = "http://localhost:3000/api"
    storeforge_api_key: str = ""

    # The model. LLM_PROVIDER picks the API format:
    #   anthropic (default; same variables as claude-agent-cli)
    #     Claude directly:  ANTHROPIC_API_KEY
    #     OpenRouter:       ANTHROPIC_BASE_URL=https://openrouter.ai/api + ANTHROPIC_AUTH_TOKEN
    #   openai: any OpenAI-compatible endpoint, e.g. CodeCraft
    #     OPENAI_BASE_URL=https://codecraftapi.com/v1 + OPENAI_API_KEY
    llm_provider: str = "anthropic"
    openai_base_url: str = ""
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    anthropic_auth_token: str = ""
    anthropic_base_url: str = ""
    agent_model: str = "claude-sonnet-5-5"

    # Required by every API call that changes something (approve, reject, complete, roll back).
    # Unset means those endpoints answer 503 rather than run unprotected.
    admin_api_key: str = ""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # .env wins over the shell. Tools such as Claude Code export ANTHROPIC_BASE_URL, which
        # would otherwise silently send the agents' requests to the wrong host.
        return init_settings, dotenv_settings, env_settings, file_secret_settings


@lru_cache
def get_settings() -> Settings:
    return Settings()
