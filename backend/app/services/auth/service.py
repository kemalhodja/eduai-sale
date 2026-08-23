import json
import logging
import uuid
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.i18n import I18nError
from app.models.agenda import AgendaItem
from app.models.budget import BudgetLimit
from app.models.notification import Notification
from app.models.receipt import Receipt
from app.models.chat_message import ChatMessage
from app.models.refresh_token import RefreshToken
from app.models.shopping import ShoppingItem
from app.models.social import DebtRecord, PriceWatchItem, SharedWallet, SharedWalletEntry, SplitBill
from app.models.sync_operation import SyncOperationRecord
from app.models.transaction import Transaction
from app.models.user import User
from app.models.wallet import Wallet
from app.services.storage.service import StorageService
from app.services.wallet.service import WalletService
from app.services.email.service import EmailService
from app.utils.password_reset import consume_reset_token, create_reset_token, store_reset_token
from app.utils.security import (
    create_access_token,
    create_refresh_token_value,
    hash_password,
    hash_refresh_token,
    verify_password,
)

logger = logging.getLogger(__name__)


class AuthService:
    def __init__(self):
        self.wallet_service = WalletService()
        self.storage_service = StorageService()
        self.email_service = EmailService()

    async def _issue_tokens(
        self, db: AsyncSession, user: User, family_id: UUID | None = None
    ) -> tuple[str, str]:
        access = create_access_token(user.id)
        raw_refresh = create_refresh_token_value()
        db.add(RefreshToken(
            user_id=user.id,
            token_hash=hash_refresh_token(raw_refresh),
            expires_at=datetime.utcnow() + timedelta(days=settings.refresh_token_expire_days),
            family_id=family_id or uuid.uuid4(),
        ))
        await db.commit()
        return access, raw_refresh

    async def register(self, db: AsyncSession, email: str, password: str, full_name: str = "") -> tuple[User, str, str]:
        email = email.strip().lower()
        full_name = full_name.strip()
        existing = await db.execute(select(User).where(User.email == email))
        if existing.scalars().first():
            raise I18nError("auth.email_exists")

        user = User(email=email, hashed_password=hash_password(password), full_name=full_name)
        try:
            db.add(user)
            await db.flush()
            await self.wallet_service.create_defaults(db, user.id, commit=False)
            access, refresh = await self._issue_tokens(db, user)
        except IntegrityError as exc:
            await db.rollback()
            raise I18nError("auth.email_exists") from exc
        await db.refresh(user)
        return user, access, refresh

    async def login(self, db: AsyncSession, email: str, password: str) -> tuple[User, str, str]:
        email = email.strip().lower()
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalars().first()
        if not user or not verify_password(password, user.hashed_password):
            raise I18nError("auth.invalid_credentials")
        access, refresh = await self._issue_tokens(db, user)
        return user, access, refresh

    async def refresh(self, db: AsyncSession, refresh_token: str) -> tuple[User, str, str]:
        token_hash = hash_refresh_token(refresh_token)
        # FOR UPDATE: paralel cift refresh'te yalnizca biri kazanir (row lock)
        result = await db.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash).with_for_update()
        )
        record = result.scalars().first()

        now = datetime.utcnow()

        # REUSE DETECTION: revokenmis/expired token tekrar sunulduysa bu bir hirsizlik
        # isaretidir -> ayni ailedeki tum tokenlari iptal et.
        if record and record.revoked_at is not None:
            if record.family_id is not None:
                await db.execute(
                    update(RefreshToken)
                    .where(
                        RefreshToken.user_id == record.user_id,
                        RefreshToken.family_id == record.family_id,
                        RefreshToken.revoked_at.is_(None),
                    )
                    .values(revoked_at=now)
                )
            else:
                # Legacy kayit (family yok): muhafazakar davran, kullanicinin
                # tum aktif refresh tokenlarini iptal et.
                await db.execute(
                    update(RefreshToken)
                    .where(
                        RefreshToken.user_id == record.user_id,
                        RefreshToken.revoked_at.is_(None),
                    )
                    .values(revoked_at=now)
                )
            await db.commit()
            raise I18nError("auth.invalid_refresh")

        if not record or record.expires_at <= now:
            raise I18nError("auth.invalid_refresh")

        user = await db.get(User, record.user_id)
        if not user:
            raise I18nError("auth.user_not_found")

        record.revoked_at = now
        access, new_refresh = await self._issue_tokens(db, user, family_id=record.family_id)
        return user, access, new_refresh

    async def revoke_refresh_token(self, db: AsyncSession, refresh_token: str) -> None:
        token_hash = hash_refresh_token(refresh_token)
        await db.execute(
            update(RefreshToken)
            .where(RefreshToken.token_hash == token_hash, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.utcnow())
        )
        await db.commit()

    async def set_pin(self, db: AsyncSession, user_id: UUID, pin: str) -> None:
        user = await db.get(User, user_id)
        if not user:
            raise I18nError("auth.user_not_found")
        user.pin_code = hash_password(pin)
        await db.commit()

    async def change_pin(self, db: AsyncSession, user_id: UUID, current_pin: str, new_pin: str) -> None:
        if not await self.verify_pin(db, user_id, current_pin):
            raise I18nError("auth.pin_invalid")
        await self.set_pin(db, user_id, new_pin)

    async def remove_pin(self, db: AsyncSession, user_id: UUID, pin: str) -> None:
        user = await db.get(User, user_id)
        if not user or not user.pin_code:
            raise I18nError("auth.pin_not_set")
        if not await self.verify_pin(db, user_id, pin):
            raise I18nError("auth.pin_invalid")
        user.pin_code = None
        await db.commit()

    async def clear_pin_by_email(self, db: AsyncSession, email: str) -> bool:
        email = email.strip().lower()
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalars().first()
        if not user:
            return False
        user.pin_code = None
        await db.commit()
        return True

    async def verify_pin(self, db: AsyncSession, user_id: UUID, pin: str) -> bool:
        user = await db.get(User, user_id)
        if not user or not user.pin_code:
            return False
        return verify_password(pin, user.pin_code)

    async def change_password(self, db: AsyncSession, user_id: UUID, current: str, new: str) -> None:
        user = await db.get(User, user_id)
        if not user or not verify_password(current, user.hashed_password):
            raise I18nError("auth.invalid_credentials")
        user.hashed_password = hash_password(new)
        await db.execute(
            update(RefreshToken).where(RefreshToken.user_id == user_id).values(revoked_at=datetime.utcnow())
        )
        await db.commit()

    async def delete_account(self, db: AsyncSession, user_id: UUID, password: str) -> None:
        user = await db.get(User, user_id)
        if not user or not verify_password(password, user.hashed_password):
            raise I18nError("auth.invalid_credentials")

        receipts = await db.execute(select(Receipt).where(Receipt.user_id == user_id))
        for receipt in receipts.scalars().all():
            await self.storage_service.delete(receipt.image_url)

        shared = await db.execute(select(SharedWallet).where(SharedWallet.owner_id == user_id))
        for sw in shared.scalars().all():
            await db.execute(delete(SharedWalletEntry).where(SharedWalletEntry.wallet_id == sw.id))
            await db.delete(sw)

        member_wallets = await db.execute(select(SharedWallet))
        for sw in member_wallets.scalars().all():
            try:
                members = json.loads(sw.member_ids or "[]")
            except json.JSONDecodeError:
                members = []
            if str(user_id) in members:
                members = [m for m in members if m != str(user_id)]
                sw.member_ids = json.dumps(members)

        for model in (
            SyncOperationRecord, Notification, PriceWatchItem, SharedWalletEntry,
            DebtRecord, SplitBill, ShoppingItem, AgendaItem, BudgetLimit,
            Transaction, Receipt, Wallet, RefreshToken, ChatMessage,
        ):
            await db.execute(delete(model).where(model.user_id == user_id))

        await db.delete(user)
        await db.commit()

    async def toggle_biometric(self, db: AsyncSession, user_id: UUID, enabled: bool) -> None:
        user = await db.get(User, user_id)
        if not user:
            raise I18nError("auth.user_not_found")
        user.biometric_enabled = enabled
        await db.commit()

    async def set_locale(self, db: AsyncSession, user_id: UUID, locale: str) -> None:
        user = await db.get(User, user_id)
        if not user:
            raise I18nError("auth.user_not_found")
        user.locale = locale
        await db.commit()

    async def set_timezone(self, db: AsyncSession, user_id: UUID, timezone: str) -> None:
        user = await db.get(User, user_id)
        if not user:
            raise I18nError("auth.user_not_found")
        user.timezone = timezone
        await db.commit()

    async def request_password_reset(self, db: AsyncSession, email: str) -> tuple[str | None, bool]:
        email = email.strip().lower()
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalars().first()
        if not user:
            return None, False

        token = create_reset_token()
        await store_reset_token(user.id, token)
        reset_url = f"{settings.password_reset_url}?token={token}"
        email_sent = self.email_service.send_password_reset(user.email, reset_url, user.locale or "tr")
        # GUVENLIK: reset token YALNIZCA debug modunda API yanitinda doner.
        # Production'da SMTP arizasi olsa bile token asla expose edilmez
        # (yoksa SMTP kesintisi = hesap ele gecirme vektoru olur).
        if not settings.debug and not email_sent:
            logger.warning(
                "SMTP unavailable in production: password reset email for user %s was NOT sent. "
                "Configure SMTP_HOST/SMTP_USER/SMTP_PASSWORD.",
                email,
            )
        expose_token = bool(settings.debug)
        return (token if expose_token else None, email_sent)

    async def reset_password(self, db: AsyncSession, token: str, new_password: str) -> None:
        user_id = await consume_reset_token(token.strip())
        if not user_id:
            raise I18nError("auth.invalid_reset_token")

        user = await db.get(User, user_id)
        if not user:
            raise I18nError("auth.user_not_found")

        user.hashed_password = hash_password(new_password)
        await db.execute(
            update(RefreshToken).where(RefreshToken.user_id == user_id).values(revoked_at=datetime.utcnow())
        )
        await db.commit()

    async def set_push_token(self, db: AsyncSession, user_id: UUID, token: str) -> None:
        user = await db.get(User, user_id)
        if not user:
            raise I18nError("auth.user_not_found")
        user.push_token = token
        await db.commit()
