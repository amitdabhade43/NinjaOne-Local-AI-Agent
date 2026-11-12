from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
    foundry_base_url: str = "http://127.0.0.1:5273/v1"
    foundry_api_key: str = "not-needed"
    foundry_model: str = ""
    host: str = "127.0.0.1"
    port: int = 8080
    log_level: str = "INFO"
    ninjaone_mcp_url: str = "http://127.0.0.1:8000/mcp"

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
