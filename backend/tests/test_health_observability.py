"""Health endpoint observability fields."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_health_public_is_minimal(monkeypatch):
    """Public /health operasyonel detay SIZDIRMAZ."""
    monkeypatch.setattr("app.main.settings.debug", True)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code in (200, 503)
    body = resp.json()
    assert resp.headers.get("X-Request-ID")
    assert resp.headers.get("X-Response-Time-Ms")
    # Hassas alanlar public yanitta YOK
    assert "observability" not in body
    assert "launch_readiness" not in body
    assert "premium_unlocked" not in body.get("features", {})
    assert "stt_provider" not in body.get("features", {})


@pytest.mark.asyncio
async def test_health_detailed_rejected_when_not_debug(monkeypatch):
    """DEBUG=false iken /health/detailed secret'siz 404 dondurur."""
    monkeypatch.setattr("app.main.settings.debug", False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health/detailed")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_health_detailed_in_debug_mode(monkeypatch):
    """DEBUG=true iken detayli health aciktir ve operasyonel alanlari icerir."""
    monkeypatch.setattr("app.main.settings.debug", True)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health/detailed")
    assert resp.status_code in (200, 503)
    body = resp.json()
    assert "observability" in body
    obs = body["observability"]
    assert "version" in obs
    assert "uptime_seconds" in obs
    assert "region" in obs

    readiness = body["launch_readiness"]
    assert "apple_configured" in readiness


@pytest.mark.asyncio
async def test_health_detailed_secret_accepted_in_prod(monkeypatch):
    """DEBUG=false + dogru internal secret -> detayli health ERISILEBILIR (404 degil)."""
    monkeypatch.setattr("app.main.settings.debug", False)
    monkeypatch.setattr("app.main.settings.internal_upgrade_secret", "strong-secret-value")
    monkeypatch.setattr("app.main.settings.s3_enabled", False)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/health/detailed",
            headers={"x-internal-upgrade-secret": "strong-secret-value"},
        )
    # Durum kodu ortamdaki DB/Redis'e baglidir; onemli olan secret'in kabul edilmesi.
    assert resp.status_code in (200, 503)
    assert "observability" in resp.json()
