import logging
import secrets
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.nlp.stt import stt_available
from app.database import get_db
from app.i18n import SUPPORTED_LOCALES, locale_from_request, maybe_translate, resolve_error, t
from app.routers import agenda, ai, analytics, auth, billing, billing_google, budgets, demo, execute, export, feedback, geofence, input, insights, legal, micro_savings, notifications, ocr, podcast, roadmap, shopping, social, sync, transactions, wallets, workspaces, ws
from app.services.social.ws_bridge import start_redis_ws_bridge, stop_redis_ws_bridge
from app.observability import capture_exception_with_request, log_slow_request, uptime_seconds
from app.startup import validate_production_settings
from app.tasks.scheduler import start_scheduler, stop_scheduler
from app.utils.redis_client import get_redis
from app.utils.scheduler_lock import acquire_scheduler_leader

logging.basicConfig(level=logging.INFO)

if settings.sentry_dsn:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration

        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.sentry_environment,
            traces_sample_rate=settings.sentry_traces_sample_rate,
            integrations=[FastApiIntegration(), LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)],
            send_default_pii=False,
        )
        logging.info("Sentry enabled for environment=%s", settings.sentry_environment)
    except Exception:
        logging.exception("Failed to initialize Sentry")


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_production_settings()
    if not settings.debug and settings.secret_key in ("change-me-in-production", "dev-secret-key-local"):
        logging.warning("SECRET_KEY is using a default value — set a strong secret in production")
    scheduler_started = False
    if settings.scheduler_enabled and await acquire_scheduler_leader():
        start_scheduler()
        scheduler_started = True
    elif settings.scheduler_enabled:
        logging.info("Scheduler skipped — another instance holds the leader lock")
    start_redis_ws_bridge()
    yield
    if scheduler_started:
        stop_scheduler()
    await stop_redis_ws_bridge()


def _cors_origins() -> list[str]:
    raw = settings.allowed_origins.strip()
    if raw == "*":
        return ["*"]
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


app = FastAPI(
    title=settings.app_name,
    description="TalkCash — Sesli komut ve AI destekli kişisel finans yönetimi",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=settings.allowed_origins.strip() != "*",
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
    request.state.request_id = request_id
    started = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - started) * 1000
    log_slow_request(request.url.path, request.method, duration_ms, request_id)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Response-Time-Ms"] = f"{duration_ms:.1f}"
    return response


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    lang = locale_from_request(request)
    detail = exc.detail
    if isinstance(detail, str):
        detail = maybe_translate(detail, lang)
    elif isinstance(detail, list):
        detail = [
            {**item, "msg": maybe_translate(item.get("msg", ""), lang)} if isinstance(item, dict) else item
            for item in detail
        ]
    return JSONResponse(status_code=exc.status_code, content=jsonable_encoder({"detail": detail}))


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    lang = locale_from_request(request)
    detail = []
    for err in exc.errors():
        if isinstance(err, dict) and "msg" in err:
            detail.append({**err, "msg": maybe_translate(str(err["msg"]), lang)})
        else:
            detail.append(err)
    return JSONResponse(status_code=422, content=jsonable_encoder({"detail": detail}))


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    lang = locale_from_request(request)
    return JSONResponse(status_code=400, content={"detail": resolve_error(exc, lang)})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", "")
    logging.exception("Unhandled error on %s request_id=%s", request.url.path, request_id)
    capture_exception_with_request(exc, request_id=request_id, path=request.url.path)
    lang = locale_from_request(request)
    return JSONResponse(status_code=500, content={"detail": t("error.internal", lang)})


app.include_router(auth.router, prefix="/api/v1")
app.include_router(billing.router, prefix="/api/v1")
app.include_router(billing_google.router, prefix="/api/v1")
app.include_router(legal.router)
app.include_router(analytics.router, prefix="/api/v1")
app.include_router(feedback.router, prefix="/api/v1")
app.include_router(input.router, prefix="/api/v1")
app.include_router(insights.router, prefix="/api/v1")
app.include_router(micro_savings.router, prefix="/api/v1")
app.include_router(wallets.router, prefix="/api/v1")
app.include_router(workspaces.router, prefix="/api/v1")
app.include_router(agenda.router, prefix="/api/v1")
app.include_router(shopping.router, prefix="/api/v1")
app.include_router(ocr.router, prefix="/api/v1")
app.include_router(ai.router, prefix="/api/v1")
app.include_router(podcast.router, prefix="/api/v1")
app.include_router(social.router, prefix="/api/v1")
app.include_router(execute.router, prefix="/api/v1")
app.include_router(budgets.router, prefix="/api/v1")
app.include_router(transactions.router, prefix="/api/v1")
app.include_router(notifications.router, prefix="/api/v1")
app.include_router(export.router, prefix="/api/v1")
app.include_router(geofence.router, prefix="/api/v1")
app.include_router(sync.router, prefix="/api/v1")
app.include_router(demo.router, prefix="/api/v1")
app.include_router(roadmap.router, prefix="/api/v1")
app.include_router(ws.router, prefix="/api/v1")


@app.get("/health")
async def health(request: Request, db: AsyncSession = Depends(get_db)):
    lang = locale_from_request(request)
    checks: dict[str, bool] = {"database": False, "redis": False}
    if settings.s3_enabled:
        checks["storage"] = False

    try:
        await db.execute(text("SELECT 1"))
        checks["database"] = True
    except Exception:
        pass

    try:
        redis = await get_redis()
        await redis.ping()
        checks["redis"] = True
    except Exception:
        pass

    if settings.s3_enabled:
        try:
            from app.services.storage.service import StorageService
            storage = StorageService()
            if storage._s3:
                import asyncio
                await asyncio.to_thread(storage._s3.head_bucket, Bucket=settings.s3_bucket)
                checks["storage"] = True
        except Exception:
            pass

    healthy = checks["database"]
    all_ok = all(checks.values())
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "ok" if all_ok else ("degraded" if healthy else "down"),
            "app": settings.app_name,
            "message": t("health.ok", lang),
            "checks": checks,
            # Kamuya acik pazarlama feature flag'leri. Hassas operasyonel detaylar
            # (LLM/STT saglayicisi, billing posture, launch_readiness) yalnizca
            # /health/detailed icinde, internal secret arkasinda sunulur.
            "features": {
                "micro_savings": True,
                "live_rates": True,
                "portfolio_coach": True,
                "offline_sync": True,
                "voice_commands": stt_available(),
            },
        },
    )


@app.get("/health/detailed")
async def health_detailed(request: Request, db: AsyncSession = Depends(get_db)):
    """Operasyonel tanilama: internal secret veya debug modu gerektirir."""
    if not settings.debug:
        secret = settings.internal_upgrade_secret.strip()
        header = request.headers.get("x-internal-upgrade-secret", "")
        if not secret or not secrets.compare_digest(header, secret):
            raise HTTPException(status_code=404, detail="Not found")

    checks: dict[str, bool] = {"database": False, "redis": False}
    if settings.s3_enabled:
        checks["storage"] = False

    try:
        await db.execute(text("SELECT 1"))
        checks["database"] = True
    except Exception:
        pass

    try:
        redis = await get_redis()
        await redis.ping()
        checks["redis"] = True
    except Exception:
        pass

    if settings.s3_enabled:
        try:
            from app.services.storage.service import StorageService
            storage = StorageService()
            if storage._s3:
                import asyncio
                await asyncio.to_thread(storage._s3.head_bucket, Bucket=settings.s3_bucket)
                checks["storage"] = True
        except Exception:
            pass

    healthy = checks["database"]
    all_ok = all(checks.values())
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "ok" if all_ok else ("degraded" if healthy else "down"),
            "app": settings.app_name,
            "locales": SUPPORTED_LOCALES,
            "checks": checks,
            "features": {
                "micro_savings": True,
                "live_rates": True,
                "portfolio_coach": True,
                "offline_sync": True,
                "voice_commands": stt_available(),
                "llm": bool(settings.openai_api_key or settings.groq_api_key),
                "premium_unlocked": settings.billing_premium_unlocked,
                "stt_provider": (
                    "groq" if settings.groq_api_key
                    else "openai" if settings.openai_api_key
                    else None
                ),
            },
            "launch_readiness": {
                "billing_production": not settings.billing_premium_unlocked and not settings.google_play_verify_mock,
                "smtp_configured": bool(settings.smtp_host.strip()),
                "s3_configured": settings.s3_enabled and bool(settings.s3_endpoint.strip()),
                "sentry_configured": bool(settings.sentry_dsn),
                "google_play_configured": bool(settings.google_play_service_account_json.strip()),
                "apple_configured": bool(settings.apple_shared_secret.strip()) or settings.apple_verify_mock,
            },
            "observability": {
                "version": settings.app_version,
                "region": settings.deploy_region,
                "uptime_seconds": uptime_seconds(),
                "request_id": getattr(request.state, "request_id", None),
                "rate_limit_enabled": settings.rate_limit_enabled,
                "scheduler_enabled": settings.scheduler_enabled,
            },
        },
    )


@app.get("/api/v1/i18n/{lang}")
async def get_translations(lang: str):
    from pathlib import Path
    import json
    path = Path(__file__).parent / "i18n" / "locales" / f"{lang}.json"
    if not path.exists():
        path = Path(__file__).parent / "i18n" / "locales" / "tr.json"
    return json.loads(path.read_text(encoding="utf-8"))
