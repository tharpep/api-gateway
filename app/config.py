"""Application configuration via pydantic-settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    debug: bool = False
    allowed_origins: list[str] = ["http://localhost:3000", "http://localhost:3001"]
    # API key (optional): if set, required on all routes except /health and /docs
    api_key: str = ""

    # Pushover
    pushover_user_key: str = ""
    pushover_api_token: str = ""

    # Google OAuth
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = ""
    google_refresh_token: str = ""

    # AI
    anthropic_api_key: str = ""
    openrouter_api_key: str = ""
    default_ai_model: str = "claude-haiku-5-5"

    # KB service (MY-AI)
    kb_service_url: str = ""
    kb_service_key: str = ""  # X-API-Key for KB service, if auth is enabled

    # Tavily (web search)
    tavily_api_key: str = ""

    # Finance DB (same Neon DB as Sazed — set DATABASE_URL here)
    database_url: str = ""

    # GitHub
    github_token: str = ""
    github_owner: str = "tharpep"  # Default owner for repo-scoped requests

    # Google Maps Platform (Places API)
    google_maps_api_key: str = ""

    # Multi-platform search
    gnews_api_key: str = ""

    # Proxmox (homelab router). Tokens are "user@realm!tokenid=secret". The read token is
    # PVEAuditor-only; the power token holds VM.PowerMgmt on allowed containers, so Proxmox ACLs
    # (not this service) decide which containers can be restarted.
    proxmox_host: str = ""
    proxmox_node: str = "pve"
    proxmox_ca_cert: str = ""  # path to pve-root-ca.pem; empty = system trust store
    proxmox_read_token: str = ""
    proxmox_power_token: str = ""

    # Service registry + health poller. The poller starts only where PROXMOX_HOST is set (the
    # homelab), so other deployments of this code don't poll a LAN they can't reach.
    registry_path: str = ""  # empty = registry/services.yaml in the repo
    health_poll_seconds: int = 60
    # Deterministic Pushover alerts from the health poller (homelab only: the poller needs
    # PROXMOX_HOST). See app/alerts.py.
    alerts_enabled: bool = True
    alert_disk_percent: int = 85  # alert at or above; re-arms below this minus 5
    alert_deploy_poll_seconds: int = 300
    console_url: str = "https://sazed.tail77c590.ts.net"

    # Observability — error tracking (Sentry). Empty DSN = fully disabled, no-op;
    # nothing to configure unless/until a Sentry project exists.
    sentry_dsn: str = ""
    sentry_traces_sample_rate: float = 0.1


settings = Settings()
