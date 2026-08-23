import logging

from app.config import settings

logger = logging.getLogger(__name__)

_WEAK_SECRETS = frozenset({"change-me-in-production", "dev-secret-key-local", "test-secret-key"})
_KNOWN_WEAK_INTERNAL = frozenset({"talkcash-internal-test-2026"})


def _s3_configured() -> bool:
    return bool(settings.s3_endpoint.strip() and settings.s3_access_key.strip() and settings.s3_secret_key.strip())


def validate_production_settings() -> None:
    """Fail fast when production-like settings are insecure."""
    if settings.debug:
        return

    errors: list[str] = []
    if settings.secret_key in _WEAK_SECRETS or len(settings.secret_key) < 32:
        errors.append("SECRET_KEY must be a strong random string (32+ chars) when DEBUG=false")
    if settings.allowed_origins.strip() == "*":
        errors.append("ALLOWED_ORIGINS must list explicit domains when DEBUG=false")

    # --- Phase 1 launch blockers (hard fail) ---
    if settings.billing_premium_unlocked:
        errors.append("BILLING_PREMIUM_UNLOCKED must be false in production")
    if settings.google_play_verify_mock:
        errors.append("GOOGLE_PLAY_VERIFY_MOCK must be false in production")
    if settings.apple_verify_mock:
        # Mock modda her 8+ karakterlik string gecerli fis sayilir -> premium ucretli
        # ozellik sahte fisle acilabilir. Production'da kapatilmalidir.
        errors.append("APPLE_VERIFY_MOCK must be false in production")
    if settings.s3_enabled and not _s3_configured():
        errors.append("S3_ENABLED=true but S3_ENDPOINT/S3_ACCESS_KEY/S3_SECRET_KEY are not all set")
    if settings.internal_upgrade_secret in _KNOWN_WEAK_INTERNAL:
        errors.append("INTERNAL_UPGRADE_SECRET is a known weak default — rotate in Render dashboard")

    # --- Warnings (non-blocking; set secrets in Render) ---
    if not settings.s3_enabled:
        logger.warning("S3_ENABLED=false — receipt images will not persist across restarts")
    if not settings.smtp_host.strip():
        logger.warning("SMTP not configured — password reset emails will not be sent (in-app fallback remains)")
    if not settings.sentry_dsn:
        logger.warning("SENTRY_DSN not set — crash reports will not be collected")
    if not settings.google_play_service_account_json.strip():
        logger.warning("GOOGLE_PLAY_SERVICE_ACCOUNT_JSON not set — Play subscription verify will fail until configured")
    if not settings.apple_shared_secret.strip() and not settings.apple_verify_mock:
        logger.warning("APPLE_SHARED_SECRET not set — App Store verify will fail until configured")

    if errors:
        hint = (
            "\n\nFix in Render Dashboard → talkcash-api-prod → Environment:\n"
            "  BILLING_PREMIUM_UNLOCKED = false\n"
            "  GOOGLE_PLAY_VERIFY_MOCK = false\n"
            "  INTERNAL_UPGRADE_SECRET = <Generate new random string, not talkcash-internal-test-2026>\n"
            "Or: Blueprint → Sync (render.yaml already sets these). Then Manual Deploy."
        )
        raise RuntimeError("Invalid production configuration:\n- " + "\n- ".join(errors) + hint)
