import secrets
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.billing import Subscription, SubscriptionStatus
from app.services.billing.service import BillingService
from app.utils.rate_limit import check_rate_limit
from app.services.billing_scheduler import notify_subscription_grace_period

router = APIRouter(prefix="/billing/google", tags=["Billing Google"])
billing_service = BillingService()

GRACE_TYPES = {
    "SUBSCRIPTION_IN_GRACE_PERIOD",
    "SUBSCRIPTION_ON_HOLD",
}
EXPIRE_TYPES = {
    "SUBSCRIPTION_EXPIRED",
    "SUBSCRIPTION_REVOKED",
    "SUBSCRIPTION_CANCELED",
}
ACTIVE_TYPES = {
    "SUBSCRIPTION_RENEWED",
    "SUBSCRIPTION_RECOVERED",
    "SUBSCRIPTION_PURCHASED",
    "SUBSCRIPTION_RESTARTED",
}


class GoogleRtdnPayload(BaseModel):
    purchase_token: str = Field(min_length=10, max_length=512)
    product_id: str = Field(min_length=3, max_length=120)
    notification_type: str = Field(min_length=3, max_length=80)
    package_name: str | None = None


def _verify_rtdn_secret(request: Request) -> None:
    # Timing-safe karsilastirma + IP rate limit (brute-force korumasi)
    check_rate_limit(request, "internal", settings.auth_rate_limit, strict=True)
    secret = settings.google_rtdn_webhook_secret.strip()
    if not secret:
        if settings.debug:
            return
        raise HTTPException(status_code=404, detail="Not found")
    header = request.headers.get("x-rtdn-secret", "")
    if not secrets.compare_digest(header, secret):
        raise HTTPException(status_code=403, detail="Forbidden")


@router.post("/rtdn")
async def google_rtdn_webhook(
    request: Request,
    data: GoogleRtdnPayload,
    db: AsyncSession = Depends(get_db),
):
    """Google Play Real-Time Developer Notifications (simplified HTTP adapter)."""
    _verify_rtdn_secret(request)
    if data.package_name and data.package_name != settings.google_play_package_name:
        raise HTTPException(status_code=400, detail="Invalid package")

    result = await db.execute(
        select(Subscription).where(Subscription.purchase_token == data.purchase_token)
    )
    subscription = result.scalars().first()
    if not subscription:
        raise HTTPException(status_code=404, detail="Subscription not found")

    ntype = data.notification_type.upper()
    if ntype in GRACE_TYPES:
        subscription.status = SubscriptionStatus.GRACE_PERIOD
        subscription.updated_at = datetime.utcnow()
        await db.commit()
        await notify_subscription_grace_period(db, subscription.user_id)
        return {
            "status": "updated",
            "user_id": str(subscription.user_id),
            "subscription_status": subscription.status.value,
        }
    elif ntype in EXPIRE_TYPES:
        await billing_service._expire_subscription(db, subscription)
        return {"status": "expired", "user_id": str(subscription.user_id)}
    elif ntype in ACTIVE_TYPES:
        subscription.status = SubscriptionStatus.ACTIVE
        if settings.google_play_verify_mock:
            subscription.expire_date = datetime.utcnow().replace(day=28)
        else:
            verified = billing_service.google_play.verify_subscription(
                data.product_id, data.purchase_token
            )
            subscription.expire_date = verified.expires_at
            subscription.google_product_id = verified.product_id
    else:
        return {"status": "ignored", "notification_type": ntype}

    subscription.updated_at = datetime.utcnow()
    await db.commit()
    return {
        "status": "updated",
        "user_id": str(subscription.user_id),
        "subscription_status": subscription.status.value,
    }
