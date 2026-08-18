from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "devops-fastapi-lab"
    app_version: str = "0.1.0"
    environment: str = "development"
    database_path: Path = Path("data/tasks.db")

    model_config = SettingsConfigDict(env_prefix="APP_", env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
