from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from dateutil.relativedelta import relativedelta
from sqlalchemy import and_, extract, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.i18n import I18nError
from app.models.agenda import AgendaItem, AgendaItemType, AgendaStatus
from app.services.wallet.service import WalletService


class AgendaService:
    def __init__(self):
        self.wallet_service = WalletService()

    async def add_bill(
        self, db: AsyncSession, user_id: UUID, title: str, amount: Decimal,
        due_date: datetime, is_recurring: bool = False, force: bool = False,
    ) -> AgendaItem:
        if not force:
            duplicate = await self._check_duplicate(db, user_id, title)
            if duplicate:
                raise I18nError("agenda.duplicate_bill", title=title)

        item = AgendaItem(
            user_id=user_id, title=title, amount=amount,
            due_date=due_date, is_recurring=is_recurring,
            item_type=AgendaItemType.BILL.value,
        )
        db.add(item)
        await db.commit()
        await db.refresh(item)
        return item

    async def add_task(
        self,
        db: AsyncSession,
        user_id: UUID,
        title: str,
        due_date: datetime,
        notes: str | None = None,
    ) -> AgendaItem:
        item = AgendaItem(
            user_id=user_id,
            title=title.strip(),
            due_date=due_date,
            item_type=AgendaItemType.TASK.value,
            amount=None,
            notes=(notes or "").strip() or None,
        )
        db.add(item)
        await db.commit()
        await db.refresh(item)
        return item

    async def create_installments(
        self, db: AsyncSession, user_id: UUID, title: str,
        total: Decimal, count: int, start_date: datetime | None = None,
    ) -> list[AgendaItem]:
        start = start_date or datetime.utcnow()
        per_installment = total / count
        items = []
        parent = AgendaItem(
            user_id=user_id, title=title, amount=per_installment,
            due_date=start, installment_total=count, installment_current=1,
            item_type=AgendaItemType.BILL.value,
        )
        db.add(parent)
        await db.flush()
        items.append(parent)

        for i in range(1, count):
            item = AgendaItem(
                user_id=user_id, title=f"{title} ({i + 1}/{count})",
                amount=per_installment, due_date=start + relativedelta(months=i),
                installment_total=count, installment_current=i + 1,
                parent_id=parent.id, item_type=AgendaItemType.BILL.value,
            )
            db.add(item)
            items.append(item)

        await db.commit()
        return items

    async def _resolve_wallet(self, db: AsyncSession, user_id: UUID, wallet_id: UUID | None) -> UUID | None:
        if wallet_id:
            wallet = await self.wallet_service.get_owned_wallet(db, user_id, wallet_id)
            return wallet.id
        wallet = await self.wallet_service.find_by_name(db, user_id, "Banka")
        if not wallet:
            wallets = await self.wallet_service.list_wallets(db, user_id)
            if wallets:
                return wallets[0].id
        return wallet.id if wallet else None

    async def mark_paid(
        self, db: AsyncSession, user_id: UUID, title: str,
        wallet_id: UUID | None = None, deduct_wallet: bool = True,
    ) -> AgendaItem:
        result = await db.execute(
            select(AgendaItem).where(
                AgendaItem.user_id == user_id,
                AgendaItem.title.ilike(f"%{title}%"),
                AgendaItem.status.in_([AgendaStatus.PENDING, AgendaStatus.OVERDUE]),
            ).order_by(AgendaItem.due_date)
        )
        item = result.scalars().first()
        if not item:
            raise I18nError("agenda.not_found_title", title=title)

        if item.item_type == AgendaItemType.TASK.value:
            deduct_wallet = False

        resolved_wallet = await self._resolve_wallet(db, user_id, wallet_id) if deduct_wallet else None
        item.status = AgendaStatus.PAID
        item.paid_at = datetime.utcnow()
        if resolved_wallet:
            item.wallet_id = resolved_wallet
        if deduct_wallet and resolved_wallet:
            await self.wallet_service.add_expense(
                db, user_id, resolved_wallet, item.amount or Decimal("0"),
                category="Fatura", description=item.title, input_method="voice",
            )

        if item.is_recurring and item.item_type == AgendaItemType.BILL.value:
            next_due = item.due_date + relativedelta(months=1)
            db.add(AgendaItem(
                user_id=user_id, title=item.title, amount=item.amount,
                due_date=next_due, is_recurring=True, item_type=AgendaItemType.BILL.value,
            ))

        await db.commit()
        return item

    async def mark_complete(self, db: AsyncSession, user_id: UUID, item_id: UUID) -> AgendaItem:
        item = await db.get(AgendaItem, item_id)
        if not item or item.user_id != user_id:
            raise I18nError("agenda.not_found")
        if item.status == AgendaStatus.PAID:
            raise I18nError("agenda.already_done")
        item.status = AgendaStatus.PAID
        item.paid_at = datetime.utcnow()
        await db.commit()
        await db.refresh(item)
        return item

    async def spawn_recurring_bills(self, db: AsyncSession) -> int:
        """Create next month's recurring bills for paid recurring items missing a future entry."""
        now = datetime.utcnow()
        result = await db.execute(
            select(AgendaItem).where(
                AgendaItem.is_recurring == True,
                AgendaItem.status == AgendaStatus.PAID,
            )
        )
        created = 0
        for paid in result.scalars().all():
            next_due = paid.due_date + relativedelta(months=1)
            if next_due <= now:
                next_due = now + relativedelta(months=1)
            existing = await db.execute(
                select(AgendaItem).where(
                    AgendaItem.user_id == paid.user_id,
                    AgendaItem.title == paid.title,
                    AgendaItem.status == AgendaStatus.PENDING,
                    extract("month", AgendaItem.due_date) == next_due.month,
                    extract("year", AgendaItem.due_date) == next_due.year,
                )
            )
            if not existing.scalars().first():
                db.add(AgendaItem(
                    user_id=paid.user_id, title=paid.title, amount=paid.amount,
                    due_date=next_due, is_recurring=True, item_type=AgendaItemType.BILL.value,
                ))
                created += 1
        if created:
            await db.commit()
        return created

    async def list_upcoming(
        self,
        db: AsyncSession,
        user_id: UUID,
        days: int = 30,
        item_type: AgendaItemType | None = None,
    ) -> list[AgendaItem]:
        # days parametresini sinirla (days=999999 -> tum tablo taramasi)
        days = max(1, min(days, 365))
        cutoff = datetime.utcnow() + timedelta(days=days)
        filters = [
            AgendaItem.user_id == user_id,
            AgendaItem.status.in_([AgendaStatus.PENDING, AgendaStatus.OVERDUE]),
            or_(
                AgendaItem.status == AgendaStatus.OVERDUE,
                AgendaItem.due_date <= cutoff,
            ),
        ]
        if item_type is not None:
            filters.append(AgendaItem.item_type == item_type.value)
        result = await db.execute(
            select(AgendaItem).where(*filters).order_by(AgendaItem.due_date).limit(200)
        )
        return list(result.scalars().all())

    async def mark_overdue_bills(self, db: AsyncSession) -> int:
        now = datetime.utcnow()
        result = await db.execute(
            select(AgendaItem).where(
                AgendaItem.status == AgendaStatus.PENDING,
                AgendaItem.due_date < now,
            )
        )
        items = list(result.scalars().all())
        for item in items:
            item.status = AgendaStatus.OVERDUE
        if items:
            await db.commit()
        return len(items)

    async def list_paid(
        self,
        db: AsyncSession,
        user_id: UUID,
        limit: int = 50,
        item_type: AgendaItemType | None = None,
    ) -> list[AgendaItem]:
        filters = [
            AgendaItem.user_id == user_id,
            AgendaItem.status == AgendaStatus.PAID,
        ]
        if item_type is not None:
            filters.append(AgendaItem.item_type == item_type.value)
        result = await db.execute(
            select(AgendaItem).where(*filters).order_by(AgendaItem.paid_at.desc()).limit(limit)
        )
        return list(result.scalars().all())

    async def update_item(
        self, db: AsyncSession, user_id: UUID, item_id: UUID,
        title: str | None = None, amount: Decimal | None = None,
        due_date: datetime | None = None, is_recurring: bool | None = None,
        notes: str | None = None,
    ) -> AgendaItem:
        item = await db.get(AgendaItem, item_id)
        if not item or item.user_id != user_id:
            raise I18nError("agenda.not_found")
        if item.status == AgendaStatus.PAID:
            raise I18nError("agenda.cannot_edit_paid")
        if title is not None:
            item.title = title
        if amount is not None and item.item_type == AgendaItemType.BILL.value:
            item.amount = amount
        if due_date is not None:
            item.due_date = due_date
        if is_recurring is not None and item.item_type == AgendaItemType.BILL.value:
            item.is_recurring = is_recurring
        if notes is not None:
            item.notes = notes.strip() or None
        await db.commit()
        await db.refresh(item)
        return item

    async def delete_item(self, db: AsyncSession, user_id: UUID, item_id: UUID) -> None:
        item = await db.get(AgendaItem, item_id)
        if not item or item.user_id != user_id:
            raise I18nError("agenda.not_found")
        if item.status == AgendaStatus.PAID:
            raise I18nError("agenda.cannot_delete_paid")
        await db.delete(item)
        await db.commit()

    async def _check_duplicate(self, db: AsyncSession, user_id: UUID, title: str) -> bool:
        now = datetime.utcnow()
        result = await db.execute(
            select(AgendaItem).where(
                and_(
                    AgendaItem.user_id == user_id,
                    AgendaItem.title.ilike(f"%{title}%"),
                    extract("month", AgendaItem.created_at) == now.month,
                    extract("year", AgendaItem.created_at) == now.year,
                )
            )
        )
        return result.scalars().first() is not None
