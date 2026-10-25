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
    # Sazed (CT 106), for the MCP job tools (research, second opinions, code tasks).
    sazed_url: str = ""
    sazed_api_key: str = ""
    # GitHub App "sazed-worker-tharpep" (plans/07): bot identity for Sazed's issues and the
    # worker's PRs. The private key is the .pem, base64-encoded onto one line.
    github_app_id: str = ""
    github_app_key_b64: str = ""
    # Repos the bot never touches (Pryce's dormant repos with permissive workflow tokens).
    github_app_excluded_repos: list[str] = [
        "tharpep/Trusted-Model-Registry",
        "tharpep/gotenberg-fork",
    ]

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

    # Media storage view (GET /homelab/storage). Each source is optional; unset ones are left
    # out. qBittorrent has no key: its WebUI whitelists the gateway's IP instead.
    sonarr_url: str = ""
    sonarr_api_key: str = ""
    radarr_url: str = ""
    radarr_api_key: str = ""
    qbittorrent_url: str = ""
    media_vmid: int = 100
    media_mount: str = "/mnt/data"  # CT mount point holding the library and downloads

    # Service registry + health poller. The poller starts only where PROXMOX_HOST is set (the
    # homelab), so other deployments of this code don't poll a LAN they can't reach.
    registry_path: str = ""  # empty = registry/services.yaml in the repo
    health_poll_seconds: int = 60
    # Deterministic Pushover alerts from the health poller (homelab only: the poller needs
    # PROXMOX_HOST). See app/alerts.py.
    alerts_enabled: bool = True
    alert_disk_percent: int = 85  # alert at or above; re-arms below this minus 5
    # Storage pools alert earlier: a full thin pool fails writes in every container at once.
    alert_pool_percent: int = 80
    alert_deploy_poll_seconds: int = 300
    console_url: str = "https://sazed.tail77c590.ts.net"

    # Observability — error tracking (Sentry). Empty DSN = fully disabled, no-op;
    # nothing to configure unless/until a Sentry project exists.
    sentry_dsn: str = ""
    sentry_traces_sample_rate: float = 0.1


settings = Settings()
