"""Shared base settings and path constants for GlobalID V2 configuration."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PROJECT_ENV_FILE = PROJECT_ROOT / ".env"


class _BaseEnvSettings(BaseSettings):
    """Shared base for all sub-settings that read from .env."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


class AppSettingsConfig(BaseSettings):
    """应用基础配置"""

    base_dir: Path = Field(default=Path("."), description="应用根目录")
    output_dir: Path = Field(default=Path("exports"), description="输出目录")


__all__ = [
    "PROJECT_ROOT",
    "PROJECT_ENV_FILE",
    "_BaseEnvSettings",
    "AppSettingsConfig",
]
