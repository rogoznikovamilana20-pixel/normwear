from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_env: str = 'development'
    database_url: str = 'postgresql+asyncpg://normwear:normwear@postgres:5432/normwear'
    redis_url: str = 'redis://redis:6379/0'
    shop_bot_token: str
    admin_bot_token: str
    shop_channel_id: int = -1004387729213
    shop_channel_username: str = 'normwear_shop'
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
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')

    @property
    def admin_ids(self) -> set[int]:
        return {int(x.strip()) for x in self.admin_telegram_ids.split(',') if x.strip().isdigit()}

settings = Settings()
