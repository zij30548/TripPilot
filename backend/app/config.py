from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Optional at startup: the existing health and Mock trip APIs need no key.
    amap_web_key: SecretStr = SecretStr("")
    amap_js_key: SecretStr = SecretStr("")
    amap_js_security_code: SecretStr = SecretStr("")


def get_settings() -> Settings:
    return Settings()
