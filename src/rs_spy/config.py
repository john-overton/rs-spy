from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    alpaca_api_key_id: str = ""
    alpaca_api_secret_key: str = ""

    data_dir: Path = REPO_ROOT / "data"
    config_dir: Path = REPO_ROOT / "config"
    reports_dir: Path = REPO_ROOT / "reports"

    warehouse_path: Path | None = None

    def resolved_warehouse_path(self) -> Path:
        return self.warehouse_path or (self.data_dir / "warehouse.duckdb")


def get_settings() -> Settings:
    return Settings()
