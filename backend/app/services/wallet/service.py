from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.i18n import I18nError
from app.models.transaction import Transaction, TransactionType
from app.models.wallet import Wallet, WalletType
from app.schemas.wallet import NetWorthResponse, WalletCreate, WalletNetWorthItem, WalletResponse
from app.services.audit.service import AuditService
from app.services.exchange.service import ExchangeService


DEFAULT_WALLETS = [
    ("Nakit", WalletType.CASH),
    ("Banka", WalletType.BANK),
    ("Kredi Kartı", WalletType.CREDIT_CARD),
    ("Altın", WalletType.INVESTMENT_GOLD),
    ("Döviz", WalletType.INVESTMENT_FOREX),
]

WALLET_ALIASES: dict[str, str] = {
    "cash": "Nakit",
    "nakit": "Nakit",
    "bank": "Banka",
    "banka": "Banka",
    "credit card": "Kredi Kartı",
    "credit": "Kredi Kartı",
    "card": "Kredi Kartı",
    "gold": "Altın",
    "forex": "Döviz",
    "döviz": "Döviz",
    "doviz": "Döviz",
}


def _is_credit_card(wallet: Wallet) -> bool:
    return wallet.wallet_type == WalletType.CREDIT_CARD


def _apply_outflow(wallet: Wallet, amount: Decimal) -> None:
    """Expense or transfer out: credit card balance rises (more debt)."""
    if _is_credit_card(wallet):
        wallet.balance += amount
    else:
        wallet.balance -= amount


def _ensure_can_spend(wallet: Wallet, amount: Decimal) -> None:
    if _is_credit_card(wallet):
        return
    if wallet.balance < amount:
        raise I18nError("wallet.insufficient_funds")


def _ensure_positive_amount(amount: Decimal) -> None:
    if amount <= 0:
        raise I18nError("wallet.invalid_amount")
    # Para ust siniri: 1e500 gibi degerler JSON'da inf olarak sizar
    from app.utils.validation import MAX_MONEY
    if amount > MAX_MONEY:
        raise I18nError("wallet.invalid_amount")


def _apply_inflow(wallet: Wallet, amount: Decimal) -> None:
    """Income or transfer in: credit card payment reduces debt."""
    if _is_credit_card(wallet):
        wallet.balance = max(Decimal("0"), wallet.balance - amount)
    else:
        wallet.balance += amount


class WalletService:
    def __init__(self):
        self.exchange = ExchangeService()
        self.audit = AuditService()

    async def create_defaults(self, db: AsyncSession, user_id: UUID, *, commit: bool = True) -> list[Wallet]:
        wallets = []
        for name, wtype in DEFAULT_WALLETS:
            wallet = Wallet(user_id=user_id, name=name, wallet_type=wtype)
            db.add(wallet)
            wallets.append(wallet)
        if commit:
            await db.commit()
        else:
            await db.flush()
        return wallets

    async def list_wallets(self, db: AsyncSession, user_id: UUID) -> list[WalletResponse]:
        result = await db.execute(select(Wallet).where(Wallet.user_id == user_id, Wallet.is_active == True))
        return [WalletResponse.model_validate(w) for w in result.scalars().all()]

    async def create_wallet(self, db: AsyncSession, user_id: UUID, data: WalletCreate) -> WalletResponse:
        wallet = Wallet(user_id=user_id, **data.model_dump())
        db.add(wallet)
        await db.commit()
        await db.refresh(wallet)
        return WalletResponse.model_validate(wallet)

    async def get_owned_wallet(self, db: AsyncSession, user_id: UUID, wallet_id: UUID) -> Wallet:
        wallet = await db.get(Wallet, wallet_id)
        if not wallet or wallet.user_id != user_id or not wallet.is_active:
            raise I18nError("wallet.not_found")
        return wallet

    async def get_net_worth(self, db: AsyncSession, user_id: UUID) -> NetWorthResponse:
        result = await db.execute(select(Wallet).where(Wallet.user_id == user_id, Wallet.is_active == True))
        raw_wallets = list(result.scalars().all())
        total = Decimal("0")
        wallet_responses = []
        for w in raw_wallets:
            try_value = await self.exchange.convert_to_try(db, w.balance, w.currency, w.wallet_type.value)
            if _is_credit_card(w):
                total -= try_value
            else:
                total += try_value
            base = WalletResponse.model_validate(w)
            wallet_responses.append(WalletNetWorthItem(**base.model_dump(), balance_try=try_value))
        return NetWorthResponse(total_try=total, wallets=wallet_responses)

    async def transfer(
        self,
        db: AsyncSession,
        user_id: UUID,
        from_id: UUID,
        to_id: UUID,
        amount: Decimal,
        description: str = "",
        *,
        input_method: str = "voice",
    ) -> tuple[Wallet, Wallet]:
        _ensure_positive_amount(amount)
        if from_id == to_id:
            raise I18nError("wallet.invalid_transfer")
        from_wallet = await self.get_owned_wallet(db, user_id, from_id)
        to_wallet = await self.get_owned_wallet(db, user_id, to_id)

        _ensure_can_spend(from_wallet, amount)
        _apply_outflow(from_wallet, amount)
        _apply_inflow(to_wallet, amount)

        tx = Transaction(
            user_id=user_id, wallet_id=from_id, target_wallet_id=to_id,
            transaction_type=TransactionType.TRANSFER, amount=amount,
            description=description, input_method=input_method,
        )
        db.add(tx)
        await db.flush()
        await self.audit.log(
            db,
            actor_user_id=user_id,
            action="wallet.transfer",
            resource_type="transaction",
            resource_id=str(tx.id),
            metadata={"from_wallet_id": str(from_id), "to_wallet_id": str(to_id), "amount": str(amount)},
        )
        await db.commit()
        return from_wallet, to_wallet

    async def add_income(
        self, db: AsyncSession, user_id: UUID, wallet_id: UUID,
        amount: Decimal, description: str = "", input_method: str = "voice",
        currency: str = "TRY",
    ) -> Transaction:
        _ensure_positive_amount(amount)
        wallet = await self.get_owned_wallet(db, user_id, wallet_id)
        _apply_inflow(wallet, amount)
        tx = Transaction(
            user_id=user_id, wallet_id=wallet_id,
            transaction_type=TransactionType.INCOME, amount=amount,
            description=description, input_method=input_method,
            currency=currency,
        )
        db.add(tx)
        await db.flush()
        await self.audit.log(
            db,
            actor_user_id=user_id,
            action="wallet.income",
            resource_type="transaction",
            resource_id=str(tx.id),
            metadata={"wallet_id": str(wallet_id), "amount": str(amount), "input_method": input_method},
        )
        await db.commit()
        await db.refresh(tx)
        return tx

    async def add_expense(
        self, db: AsyncSession, user_id: UUID, wallet_id: UUID,
        amount: Decimal, category: str, description: str = "", place: str = "",
        store_name: str = "", input_method: str = "voice", receipt_id: UUID | None = None,
        is_recurring: bool = False, next_billing_date: date | None = None,
        subscription_name: str | None = None,
        transaction_at: datetime | None = None,
        currency: str = "TRY",
        notes: str | None = None,
        original_amount: Decimal | None = None,
        original_currency: str | None = None,
        fx_rate: Decimal | None = None,
    ) -> Transaction:
        _ensure_positive_amount(amount)
        wallet = await self.get_owned_wallet(db, user_id, wallet_id)
        _ensure_can_spend(wallet, amount)
        _apply_outflow(wallet, amount)
        resolved_store = (store_name or place or "").strip() or "Genel"
        tx = Transaction(
            user_id=user_id, wallet_id=wallet_id,
            transaction_type=TransactionType.EXPENSE, amount=amount,
            category=category, description=description,
            place=place or resolved_store,
            store_name=resolved_store,
            input_method=input_method,
            receipt_id=receipt_id,
            is_recurring=is_recurring,
            next_billing_date=next_billing_date,
            subscription_name=subscription_name,
            currency=currency,
            notes=notes,
            original_amount=original_amount,
            original_currency=original_currency,
            fx_rate=fx_rate,
        )
        if transaction_at is not None:
            tx.created_at = transaction_at
        db.add(tx)
        await db.flush()
        await self.audit.log(
            db,
            actor_user_id=user_id,
            action="wallet.expense",
            resource_type="transaction",
            resource_id=str(tx.id),
            metadata={"wallet_id": str(wallet_id), "amount": str(amount), "category": category, "input_method": input_method},
        )
        await db.commit()
        await db.refresh(tx)
        return tx

    async def update_wallet(
        self, db: AsyncSession, user_id: UUID, wallet_id: UUID, data,
    ) -> WalletResponse:
        wallet = await self.get_owned_wallet(db, user_id, wallet_id)
        if data.name is not None:
            wallet.name = data.name
        if data.wallet_type is not None:
            wallet.wallet_type = data.wallet_type
        if data.currency is not None:
            wallet.currency = data.currency
        await db.commit()
        await db.refresh(wallet)
        return WalletResponse.model_validate(wallet)

    async def deactivate_wallet(self, db: AsyncSession, user_id: UUID, wallet_id: UUID) -> None:
        wallet = await self.get_owned_wallet(db, user_id, wallet_id)
        if wallet.balance != Decimal("0"):
            raise I18nError("wallet.non_zero_balance")
        wallet.is_active = False
        await db.commit()

    async def find_by_name(self, db: AsyncSession, user_id: UUID, name: str) -> Wallet | None:
        key = name.lower().strip()
        resolved = WALLET_ALIASES.get(key, name)
        result = await db.execute(
            select(Wallet).where(
                Wallet.user_id == user_id,
                Wallet.is_active == True,
                Wallet.name.ilike(f"%{resolved}%"),
            )
        )
        wallet = result.scalars().first()
        if wallet or resolved == name:
            return wallet
        result = await db.execute(
            select(Wallet).where(
                Wallet.user_id == user_id,
                Wallet.is_active == True,
                Wallet.name.ilike(f"%{name}%"),
            )
        )
        return result.scalars().first()

    async def monthly_summary(self, db: AsyncSession, user_id: UUID) -> dict:
        from datetime import datetime

        from sqlalchemy import extract, func

        from app.models.budget import BudgetLimit

        now = datetime.utcnow()
        month_filter = (
            Transaction.user_id == user_id,
            extract("month", Transaction.created_at) == now.month,
            extract("year", Transaction.created_at) == now.year,
        )
        totals_result = await db.execute(
            select(Transaction.transaction_type, func.coalesce(func.sum(Transaction.amount), 0))
            .where(*month_filter)
            .group_by(Transaction.transaction_type)
        )
        totals = {row[0].value: float(row[1]) for row in totals_result.all()}

        category_result = await db.execute(
            select(Transaction.category, func.coalesce(func.sum(Transaction.amount), 0))
            .where(*month_filter, Transaction.transaction_type == TransactionType.EXPENSE)
            .group_by(Transaction.category)
            .order_by(func.sum(Transaction.amount).desc())
            .limit(8)
        )
        top_categories = [
            {"category": row[0] or "Genel", "amount": float(row[1])}
            for row in category_result.all()
        ]

        budgets_result = await db.execute(select(BudgetLimit).where(BudgetLimit.user_id == user_id))
        budget_health = []
        for budget in budgets_result.scalars().all():
            spent_result = await db.execute(
                select(func.coalesce(func.sum(Transaction.amount), 0)).where(
                    *month_filter,
                    Transaction.transaction_type == TransactionType.EXPENSE,
                    Transaction.category == budget.category,
                )
            )
            spent = float(spent_result.scalar() or 0)
            limit = float(budget.monthly_limit)
            percent = 0 if limit <= 0 else round((spent / limit) * 100, 1)
            budget_health.append({
                "category": budget.category,
                "spent": spent,
                "limit": limit,
                "percent": percent,
                "status": "danger" if percent >= 100 else "warning" if percent >= 80 else "ok",
            })

        nw = await self.get_net_worth(db, user_id)
        income = totals.get(TransactionType.INCOME.value, 0)
        expense = totals.get(TransactionType.EXPENSE.value, 0)
        savings_rate = round(((income - expense) / income) * 100, 1) if income > 0 else None

        prev_month = now.month - 1 if now.month > 1 else 12
        prev_year = now.year if now.month > 1 else now.year - 1
        prev_filter = (
            Transaction.user_id == user_id,
            extract("month", Transaction.created_at) == prev_month,
            extract("year", Transaction.created_at) == prev_year,
            Transaction.transaction_type == TransactionType.EXPENSE,
        )
        prev_result = await db.execute(
            select(Transaction.category, func.coalesce(func.sum(Transaction.amount), 0))
            .where(*prev_filter)
            .group_by(Transaction.category)
        )
        prev_by_cat = {row[0] or "Genel": float(row[1]) for row in prev_result.all()}

        trends = []
        narratives = []
        for row in top_categories:
            cat = row["category"]
            current = row["amount"]
            previous = prev_by_cat.get(cat, 0)
            if current <= 0 and previous <= 0:
                continue
            change_pct = round(((current - previous) / previous) * 100, 1) if previous > 0 else 100.0
            trends.append({
                "category": cat,
                "this_month": current,
                "last_month": previous,
                "change_pct": change_pct,
            })
            if change_pct <= -10:
                narratives.append({
                    "tone": "success",
                    "text": f"Bu ay geçen aya göre {cat} harcamalarını %{abs(change_pct):.0f} azaltmışsın, harika gidiyorsun!",
                })
            elif change_pct >= 15:
                narratives.append({
                    "tone": "warning",
                    "text": f"{cat} harcamaların geçen aya göre %{change_pct:.0f} arttı, dikkat et.",
                })
        for budget in budget_health:
            if budget["percent"] >= 80:
                narratives.append({
                    "tone": "danger" if budget["percent"] >= 100 else "warning",
                    "text": f"{budget['category']} harcamaların bütçeni aşmak üzere, dikkat et.",
                })

        return {
            "month": now.month,
            "year": now.year,
            "income": income,
            "expense": expense,
            "savings": round(income - expense, 2),
            "savings_rate": savings_rate,
            "net_worth": float(nw.total_try),
            "top_categories": top_categories,
            "budget_health": budget_health,
            "trends": trends,
            "narratives": narratives,
            "wallets": [
                {"name": w.name, "balance": float(w.balance_try), "currency": w.currency}
                for w in nw.wallets[:8]
            ],
        }
