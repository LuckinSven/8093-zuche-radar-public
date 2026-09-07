from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用唯一配置入口。"""

    app_port: int = 8093
    database_url: str = "postgresql+psycopg://zuche:zuche@localhost:5432/zuche_radar"
    raw_retention_days: int = 60
    zuche_base_url: str = "https://m.zuche.com"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
