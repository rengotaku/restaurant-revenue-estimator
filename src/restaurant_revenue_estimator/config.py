"""アプリケーション設定: pydantic-settings で env を解決する."""

import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_BASE_DIR = Path(__file__).parent.parent.parent


class Settings(BaseSettings):
    """環境変数から読み込まれるアプリ設定."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: str = Field(default="development")
    log_level: str = Field(default="INFO")
    log_json: bool = Field(default=False)

    # 自分の PC でだけ使う前提。0.0.0.0 では待ち受けない (ADR 0002)。
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=8000)

    database_url: str = Field(
        default=f"sqlite:///{_BASE_DIR / 'restaurant_revenue_estimator.db'}",
    )


_settings: Settings | None = None


def get_settings() -> Settings:
    """設定インスタンスをシングルトンで返す."""
    global _settings  # noqa: PLW0603
    if _settings is None:
        _settings = Settings()
    return _settings


def data_directory() -> Path:
    """入力 YAML の置き場所. テストでは RRE_DATA_DIR で差し替える."""
    return Path(os.environ.get("RRE_DATA_DIR", str(_BASE_DIR / "data")))


def reset_settings() -> None:
    """テスト用: 設定キャッシュをリセットする."""
    global _settings  # noqa: PLW0603
    _settings = None
