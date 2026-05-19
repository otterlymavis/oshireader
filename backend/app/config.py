from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    youtube_api_key: str = ""
    database_url: str = "sqlite:///./otterpia.db"
    poll_interval_minutes: int = 15

    model_config = {"env_file": ".env"}


settings = Settings()
