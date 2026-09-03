from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_env: str = 'development'
    database_url: str = 'postgresql+asyncpg://normwear:normwear@postgres:5432/normwear'
    redis_url: str = 'redis://redis:6379/0'
    shop_bot_token: str
    admin_bot_token: str
    shop_channel_id: int = -1004387729213
    shop_channel_username: str = 'normwear_shop'
    shop_username: str = 'norm_shop_bot'
    supplier_channel_username: str = 'optobaza'
    telegram_api_id: int
    telegram_api_hash: str
    supplier_session_string: str = ''
    currency: str = 'RUB'
    default_margin_pct: float = 35
    price_review_threshold: float = 0.70
    miniapp_url_template: str = 'https://REPLACE_WITH_MINIAPP_DOMAIN/?product={product_id}'
    telegram_payment_provider_token: str = ''
    sbp_provider: str = 'manual'
    sbp_payment_url: str = ''
    admin_telegram_ids: str = ''
    auto_publish: bool = False
    webhook_url: str = ''
    admin_secret: str = ''
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')

    @property
    def admin_ids(self) -> set[int]:
        return {int(x.strip()) for x in self.admin_telegram_ids.split(',') if x.strip().isdigit()}

settings = Settings()

# Shared bot instances — reuse HTTP sessions instead of creating new Bot() per notification
_shop_bot = None
_admin_bot = None

def get_shop_bot():
    global _shop_bot
    if _shop_bot is None:
        from aiogram import Bot
        _shop_bot = Bot(settings.shop_bot_token)
    return _shop_bot

def get_admin_bot():
    global _admin_bot
    if _admin_bot is None:
        from aiogram import Bot
        _admin_bot = Bot(settings.admin_bot_token)
    return _admin_bot
