from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


# Expected layout: both repositories are sibling folders under C:\\project.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
GATEWAY_ROOT = PROJECT_ROOT / "NinjaRMM-API-Gateway-MCP"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Foundry Local assigns a dynamic port. The agent discovers it from
    # the Foundry CLI; this optional URL is retained as a fallback.
    foundry_base_url: str = ""
    foundry_api_key: str = "not-needed"
    foundry_model: str = ""

    host: str = "127.0.0.1"
    port: int = 8080
    log_level: str = "INFO"

    # Local NinjaOne MCP gateway launched as a STDIO subprocess.
    ninjaone_gateway_dir: Path = GATEWAY_ROOT
    ninjaone_gateway_python: Path = (
        GATEWAY_ROOT / ".venv" / "Scripts" / "python.exe"
    )
    ninjaone_gateway_env_file: Path = GATEWAY_ROOT / ".env"

    max_agent_steps: int = 6
    request_timeout_seconds: int = 90
    approval_required_for_writes: bool = True
    allow_delete_operations: bool = False


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
