from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    youtube_api_key: str = ""
    twitter_bearer_token: str = ""
    database_url: str = "sqlite:///./otterpia.db"
    poll_interval_minutes: int = 15
    connector_fetch_timeout_seconds: float = 25.0
    connector_concurrency: int = 4
    poll_terms_per_run: int = 5
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

    model_config = {"env_file": ".env"}

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allow_origins.split(",") if origin.strip()]


settings = Settings()
