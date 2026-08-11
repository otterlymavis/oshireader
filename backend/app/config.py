from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    youtube_api_key: str = ""
    twitter_bearer_token: str = ""
    database_url: str = "sqlite:///./otterpia.db"
    internal_scheduler_enabled: bool = False
    poll_interval_minutes: int = 60
    connector_fetch_timeout_seconds: float = 8.0
    connector_concurrency: int = 1
    poll_terms_per_run: int = 1
    refresh_free_interval_minutes: int = 1440
    refresh_standard_interval_minutes: int = 360
    refresh_premium_interval_minutes: int = 180
    inactive_poll_after_days: int = 30
    retention_days: int = 30
    notification_freshness_window_minutes: int = 120
    orphaned_notification_grace_minutes: int = 60
    pending_notification_stale_after_days: int = 7
    admin_api_token: str = ""
    allow_unauthenticated_admin: bool = False
    cors_allow_origins: str = ""
    apns_team_id: str = ""
    apns_key_id: str = ""
    apns_private_key: str = ""
    apns_private_key_path: str = ""
    apns_topic: str = "com.otterpia.oshireader.plus"
    apns_use_sandbox: bool = False  # set APNS_USE_SANDBOX=true only for Debug-config builds; TestFlight/App Store builds use the production APNs host
    apns_trust_registered_tokens: bool = False  # skip inline APNs validation on registration; tokens are trusted immediately and pruned on first delivery failure
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

    @property
    def refresh_intervals_minutes(self) -> dict[str, int]:
        return {
            "free": max(1, self.refresh_free_interval_minutes),
            "standard": max(1, self.refresh_standard_interval_minutes),
            "premium": max(1, self.refresh_premium_interval_minutes),
        }


settings = Settings()
