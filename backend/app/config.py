from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.bootstrap_env import normalize_production_env

normalize_production_env()


def _normalize_database_url(url: str) -> str:
    """Render/Heroku provide postgresql:// — SQLAlchemy async needs +asyncpg."""
    if url.startswith("postgresql://") and "+asyncpg" not in url:
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://") and "+asyncpg" not in url:
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url


class Settings(BaseSettings):
    # extra=ignore: bilinmeyen env degiskenleri (Render/compose enjeksiyonlari)
    # uygulamayi cokertmesin.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "TalkCash API"
    # GUVENLIK: default False. Yerel gelistirmede .env icine DEBUG=true yazin.
    debug: bool = False

    database_url: str = "postgresql+asyncpg://talkcash:talkcash@localhost:5432/talkcash"
    redis_url: str = "redis://localhost:6379/0"

    secret_key: str = "change-me-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 30

    allowed_origins: str = "*"
    rate_limit_enabled: bool = True
    auth_rate_limit: int = 30
    input_rate_limit: int = 60
    voice_rate_limit: int = 20
    ocr_rate_limit: int = 15
    ocr_max_upload_bytes: int = 10 * 1024 * 1024

    sync_rate_limit: int = 30
    execute_rate_limit: int = 60
    export_rate_limit: int = 10
    ai_rate_limit: int = 30
    geofence_rate_limit: int = 20
    micro_savings_rate_limit: int = 40
    sync_max_batch: int = 50
    max_export_rows: int = 2000

    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    whisper_model: str = "whisper-1"
    groq_api_key: str = ""
    groq_whisper_model: str = "whisper-large-v3-turbo"

    exchange_rate_api: str = "https://api.exchangerate-api.com/v4/latest/TRY"

    # S3 / MinIO
    s3_enabled: bool = False
    s3_endpoint: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket: str = "talkcash"
    s3_region: str = "us-east-1"
    s3_public_url: str = ""

    scheduler_enabled: bool = True

    overpass_enabled: bool = True
    overpass_url: str = "https://overpass-api.de/api/interpreter"
    geofence_cache_ttl: int = 3600

    app_timezone: str = "Europe/Istanbul"

    internal_upgrade_secret: str = ""
    billing_premium_unlocked: bool = False
    google_play_package_name: str = "io.talkcash.app"
    google_play_service_account_json: str = ""
    google_play_verify_mock: bool = False
    apple_verify_mock: bool = False
    apple_shared_secret: str = ""
    google_rtdn_webhook_secret: str = ""
    password_reset_ttl_seconds: int = 3600
    password_reset_url: str = "talkcash://reset-password"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "noreply@talkcash.io"
    smtp_use_tls: bool = True

    sentry_dsn: str = ""
    sentry_environment: str = "development"
    sentry_traces_sample_rate: float = 0.1

    app_version: str = "1.3.0"
    deploy_region: str = "frankfurt"

    # OCR: tesseract | google | auto (tesseract first, Vision fallback)
    ocr_provider: str = "auto"
    google_vision_api_key: str = ""

    broker_midas_url: str = "https://www.getmidas.com/"
    broker_papara_url: str = "https://www.papara.com/"
    broker_revolut_url: str = "https://www.revolut.com/"
    broker_trading212_url: str = "https://www.trading212.com/"

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_db_url(cls, value: str) -> str:
        return _normalize_database_url(value)

    @field_validator(
        "billing_premium_unlocked",
        "google_play_verify_mock",
        "apple_verify_mock",
        "s3_enabled",
        "debug",
        "scheduler_enabled",
        "rate_limit_enabled",
        "smtp_use_tls",
        mode="before",
    )
    @classmethod
    def parse_bool_env(cls, value):
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in ("true", "1", "yes", "on"):
                return True
            if lowered in ("false", "0", "no", "off", ""):
                return False
        return value


settings = Settings()
