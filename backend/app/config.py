from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    youtube_api_key: str = ""
    twitter_bearer_token: str = ""
    database_url: str = "sqlite:///./otterpia.db"
    poll_interval_minutes: int = 15
    connector_fetch_timeout_seconds: float = 8.0
    connector_concurrency: int = 1
    poll_terms_per_run: int = 1
    notification_freshness_window_minutes: int = 1440
    orphaned_notification_grace_minutes: int = 60
    admin_api_token: str = ""
    allow_unauthenticated_admin: bool = False
    cors_allow_origins: str = ""
    apns_team_id: str = ""
    apns_key_id: str = ""
    apns_private_key: str = ""
    apns_private_key_path: str = ""
    apns_topic: str = "com.otterpia.oshireader.plus"
    apns_use_sandbox: bool = False  # set APNS_USE_SANDBOX=true only for Debug-config builds; TestFlight/App Store builds use the production APNs host
    backend_public_url: str = "https://oshireader.onrender.com"
    source_rss_proxy_url: str = "https://oshireader-feed-poller.oshireader-otterlymavis.workers.dev/rss-proxy"
    source_5ch_proxy_url: str = "https://oshireader-feed-poller.oshireader-otterlymavis.workers.dev/fivech-proxy"

    model_config = {"env_file": ".env"}

    @field_validator("database_url")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        if value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql://", 1)
        return value

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allow_origins.split(",") if origin.strip()]


settings = Settings()
