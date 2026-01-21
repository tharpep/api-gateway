"""Application configuration via pydantic-settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    debug: bool = False
    allowed_origins: list[str] = ["http://localhost:3000", "http://localhost:3001"]

    # Pushover
    pushover_user_key: str = ""
    pushover_api_token: str = ""

    # Google OAuth
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = ""

    # AI
    anthropic_api_key: str = ""
    openrouter_api_key: str = ""
    default_ai_model: str = "claude-haiku-4-5-20251001"


settings = Settings()
