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

    # Postgres runs-store (docker-compose.yml). Default matches that compose
    # file's credentials and its 55432 host port (chosen to avoid colliding with
    # a native Postgres on the standard 5432). Override via .env for a different host/db.
    database_url: str = "postgresql://rs_spy:rs_spy@localhost:55432/rs_spy"

    def resolved_warehouse_path(self) -> Path:
        return self.warehouse_path or (self.data_dir / "warehouse.duckdb")


def get_settings() -> Settings:
    return Settings()
