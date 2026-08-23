"""Normalize stale Render/dev env vars before Settings() loads in production."""

import os
import secrets

_WEAK_INTERNAL = frozenset({"", "talkcash-internal-test-2026", "dev-upgrade-secret"})


def normalize_production_env() -> dict[str, str]:
    """Apply production-safe overrides. Returns keys that were changed."""
    # NOT: DEBUG eksikse "true" kabul edilir (yerel gelistirme ergonomisi;
    # render.yaml production'da DEBUG=false'u acikca set eder).
    debug = os.environ.get("DEBUG", "true").strip().lower()
    if debug not in ("false", "0", "no", "off"):
        return {}

    applied: dict[str, str] = {}

    for key in ("BILLING_PREMIUM_UNLOCKED", "GOOGLE_PLAY_VERIFY_MOCK", "APPLE_VERIFY_MOCK"):
        if os.environ.get(key, "").strip().lower() not in ("false", "0", "no", "off"):
            applied[key] = "false"
        os.environ[key] = "false"

    internal = os.environ.get("INTERNAL_UPGRADE_SECRET", "")
    if internal in _WEAK_INTERNAL:
        # GUVENLIK: SECRET_KEY'den turetme YAPMA — internal header secret'i sizan
        # biri JWT imzalama anahtarini da ele gecirmesin. Her zaman bagimsiz rastgele.
        generated = secrets.token_urlsafe(48)
        applied["INTERNAL_UPGRADE_SECRET"] = generated
        os.environ["INTERNAL_UPGRADE_SECRET"] = generated

    return applied


def main() -> None:
    applied = normalize_production_env()
    for key in applied:
        if key == "INTERNAL_UPGRADE_SECRET":
            print("INFO: INTERNAL_UPGRADE_SECRET normalized (weak default replaced)")
        else:
            print(f"INFO: {key}=false (production override)")


if __name__ == "__main__":
    main()
