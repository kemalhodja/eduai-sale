from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user, user_locale
from app.i18n import resolve_error, t
from app.models.user import User
from app.services.billing.service import BillingService, EntitlementError
from app.services.ai_mentor.service import AIMentorService
from app.services.ai_mentor.chat_service import ChatMentorService
from app.services.insights.service import InsightService
from app.services.price_watch.service import PriceWatchService
from app.utils.rate_limit import check_rate_limit

router = APIRouter(prefix="/ai", tags=["AI Mentor"])
ai_service = AIMentorService()
chat_service = ChatMentorService()
watch_service = PriceWatchService()
billing_service = BillingService()
insight_service = InsightService()


class WatchlistAdd(BaseModel):
    product_name: str
    threshold_percent: float = 5.0


class ChatRequest(BaseModel):
    message: str


class ChatMessageResponse(BaseModel):
    id: str
    role: str
    content: str
    created_at: str | None = None


class InsightResponse(BaseModel):
    id: str
    type: str
    title: str
    summary: str
    severity: str
    created_at: str | None = None


@router.get("/budget-alerts")
async def budget_alerts(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await check_rate_limit(request, "ai", settings.ai_rate_limit, identifier=str(user.id), strict=True)
    try:
        await billing_service.consume_usage(db, user.id, "ai_coach")
    except EntitlementError:
        raise HTTPException(status_code=402, detail={"code": "premium_required", "entitlement": "ai_coach"})
    return await ai_service.check_budget_alerts(db, user.id, user_locale(user))


@router.get("/forecast")
async def month_end_forecast(
    request: Request,
    current_balance: float,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await check_rate_limit(request, "ai", settings.ai_rate_limit, identifier=str(user.id), strict=True)
    try:
        await billing_service.consume_usage(db, user.id, "ai_coach")
    except EntitlementError:
        raise HTTPException(status_code=402, detail={"code": "premium_required", "entitlement": "ai_coach"})
    return await ai_service.predict_month_end(db, user.id, Decimal(str(current_balance)), user_locale(user))


@router.get("/price-tracker")
async def price_tracker(
    request: Request,
    product: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await check_rate_limit(request, "ai", settings.ai_rate_limit, identifier=str(user.id), strict=True)
    try:
        await billing_service.consume_usage(db, user.id, "price_watch")
    except EntitlementError:
        raise HTTPException(status_code=402, detail={"code": "premium_required", "entitlement": "price_watch"})
    locale = user_locale(user)
    report = await ai_service.price_change_report(db, user.id, product, locale)
    if report:
        return {**report, "has_data": True}
    return {"message": t("ai.insufficient_data", locale), "has_data": False}


@router.get("/watchlist")
async def list_watchlist(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    items = await watch_service.list_items(db, user.id)
    return [
        {
            "id": str(i.id), "product_name": i.product_name,
            "threshold_percent": float(i.threshold_percent),
            "last_avg_price": float(i.last_avg_price) if i.last_avg_price else None,
        }
        for i in items
    ]


@router.post("/watchlist")
async def add_watchlist_item(
    data: WatchlistAdd, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    try:
        item = await watch_service.add_item(
            db, user.id, data.product_name, Decimal(str(data.threshold_percent)),
        )
        return {"id": str(item.id), "product_name": item.product_name}
    except Exception as e:
        raise HTTPException(status_code=400, detail=resolve_error(e, user_locale(user)))


@router.delete("/watchlist/{item_id}")
async def remove_watchlist_item(
    item_id: UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    try:
        await watch_service.remove_item(db, user.id, item_id)
        return {"status": "removed"}
    except Exception as e:
        raise HTTPException(status_code=404, detail=resolve_error(e, user_locale(user)))


@router.get("/chat/history")
async def chat_history(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = 30,
):
    limit = max(1, min(limit, 100))  # sinirsiz .limit() -> kullanici bazli DoS onlemi
    items = await chat_service.list_history(db, user.id, limit=limit)
    return [
        ChatMessageResponse(
            id=str(m.id), role=m.role, content=m.content,
            created_at=m.created_at.isoformat() if m.created_at else None,
        )
        for m in items
    ]


@router.get("/insights", response_model=list[InsightResponse])
async def ai_insights(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await check_rate_limit(request, "ai", settings.ai_rate_limit, identifier=str(user.id), strict=True)
    try:
        await billing_service.consume_usage(db, user.id, "ai_coach")
    except EntitlementError:
        raise HTTPException(status_code=402, detail={"code": "premium_required", "entitlement": "ai_coach"})
    items = await insight_service.list_recent(db, user.id, limit=10)
    if not items:
        items = await insight_service.generate_weekly(db, user.id, user_locale(user))
    return [
        InsightResponse(
            id=str(item.id),
            type=item.insight_type.value,
            title=item.title,
            summary=item.summary,
            severity=item.severity,
            created_at=item.created_at.isoformat() if item.created_at else None,
        )
        for item in items
    ]


@router.post("/chat", response_model=ChatMessageResponse)
async def chat_mentor(
    data: ChatRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await check_rate_limit(request, "ai", settings.ai_rate_limit, identifier=str(user.id), strict=True)
    try:
        await billing_service.consume_usage(db, user.id, "ai_coach")
    except EntitlementError:
        raise HTTPException(status_code=402, detail={"code": "premium_required", "entitlement": "ai_coach"})
    locale = user_locale(user)
    try:
        msg = await chat_service.chat(db, user.id, data.message, locale, user.assistant_persona or "default")
        return ChatMessageResponse(
            id=str(msg.id), role=msg.role, content=msg.content,
            created_at=msg.created_at.isoformat() if msg.created_at else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=503, detail=resolve_error(e, locale))
