from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    redis_url: str
    code_length: int = 7
    flush_interval_seconds: int = 5
    rate_limit_per_minute: int = 60

settings = Settings()
