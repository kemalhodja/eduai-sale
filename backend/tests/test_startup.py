"""Production startup validation (Phase 1 launch blockers)."""

import pytest

from app.config import Settings
from app.startup import validate_production_settings


def _prod_settings(**overrides) -> Settings:
    base = {
        "debug": False,
        "secret_key": "x" * 48,
        "allowed_origins": "https://example.com",
        "billing_premium_unlocked": False,
        "google_play_verify_mock": False,
        # conftest tum test surecinde APPLE_VERIFY_MOCK=true set eder;
        # gecerli bir production konfigurasyonunda bu kapali olmalidir.
        "apple_verify_mock": False,
        "s3_enabled": True,
        "s3_endpoint": "https://r2.example.com",
        "s3_access_key": "key",
        "s3_secret_key": "secret",
        "internal_upgrade_secret": "rotated-production-secret-value",
    }
    base.update(overrides)
    return Settings(**base)


def test_production_settings_pass(monkeypatch):
    s = _prod_settings()
    monkeypatch.setattr("app.startup.settings", s)
    validate_production_settings()


def test_production_rejects_premium_unlocked(monkeypatch):
    s = _prod_settings(billing_premium_unlocked=True)
    monkeypatch.setattr("app.startup.settings", s)
    with pytest.raises(RuntimeError, match="BILLING_PREMIUM_UNLOCKED"):
        validate_production_settings()


def test_production_rejects_billing_mock(monkeypatch):
    s = _prod_settings(google_play_verify_mock=True)
    monkeypatch.setattr("app.startup.settings", s)
    with pytest.raises(RuntimeError, match="GOOGLE_PLAY_VERIFY_MOCK"):
        validate_production_settings()


def test_production_rejects_apple_mock(monkeypatch):
    s = _prod_settings(apple_verify_mock=True)
    monkeypatch.setattr("app.startup.settings", s)
    with pytest.raises(RuntimeError, match="APPLE_VERIFY_MOCK"):
        validate_production_settings()


def test_production_rejects_s3_without_credentials(monkeypatch):
    s = _prod_settings(s3_endpoint="")
    monkeypatch.setattr("app.startup.settings", s)
    with pytest.raises(RuntimeError, match="S3_ENABLED"):
        validate_production_settings()


def test_debug_skips_validation(monkeypatch):
    s = _prod_settings(debug=True, billing_premium_unlocked=True, google_play_verify_mock=True)
    monkeypatch.setattr("app.startup.settings", s)
    validate_production_settings()
