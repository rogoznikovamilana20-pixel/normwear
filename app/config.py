import os
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(BASE_DIR / ".env"), env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    database_url: str = "sqlite+aiosqlite:///normwear.db"
    redis_url: str | None = None

    shop_bot_token: str = ""
    admin_bot_token: str = ""
    shop_channel_id: int | None = None
    shop_channel_username: str = "normwear_shop"
    shop_username: str = "norm_shop_bot"
    supplier_channel_username: str = "optobaza"

    telegram_api_id: int | None = None
    telegram_api_hash: str | None = None
    supplier_session_string: str | None = None
    telegram_proxy_url: str | None = None

    currency: str = "RUB"
    default_margin_pct: float = 35.0
    price_review_threshold: float = 0.0
    miniapp_url_template: str | None = None

    telegram_payment_provider_token: str | None = None
    sbp_provider: str | None = None
    sbp_payment_url: str | None = None

    admin_telegram_ids: str = ""
    auto_publish: bool = False

    yandex_public_key: str = "https://disk.yandex.ru/d/e4YGRLhebhBoVA"
    run_bots: bool = True
    base_port: int = 8000
    # Render и другие PaaS прокидывают PORT
    port: int | None = None

    @field_validator("shop_channel_id", "telegram_api_id", "port", "base_port", mode="before")
    @classmethod
    def _empty_int_to_none(cls, v):
        if v in ("", None):
            return None
        return v

    @property
    def admin_ids(self) -> list[int]:
        ids = []
        for part in self.admin_telegram_ids.replace(" ", "").split(","):
            if part.strip().lstrip("-").isdigit():
                ids.append(int(part))
        return ids

    @property
    def effective_port(self) -> int:
        # приоритет: PORT (Render) -> port -> base_port
        if self.port:
            return self.port
        env_port = os.getenv("PORT")
        if env_port and env_port.isdigit():
            return int(env_port)
        return self.base_port

    @property
    def db_url(self) -> str:
        url = (self.database_url or "").strip()
        if not url:
            return "sqlite+aiosqlite:///normwear.db"
        # фиксим Windows-путь вида C:/... в контейнере -> делаем относительным
        if "C:/" in url or "C:\\" in url:
            # на линуксе такой путь невалиден, fallback на ./normwear.db
            if os.name != "nt":
                return "sqlite+aiosqlite:///./normwear.db"
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()