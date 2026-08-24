import secrets
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.social import SharedWallet, SharedWalletEntry
from app.models.user import User
from app.models.workspace import Invitation, Organization, OrganizationMember, WorkspaceRole, WorkspaceType
from app.schemas.workspace import FamilyBudgetSummary, WorkspaceCreate, WorkspaceInvite, WorkspaceResponse
from app.services.audit.service import AuditService
from app.services.billing.service import BillingService
from app.services.social.shared_wallet_service import SharedWalletService


def workspace_invite_url(token: str) -> str:
    return f"talkcash://workspace-invite?token={token}"


class WorkspaceService:
    def __init__(self):
        self.audit = AuditService()
        self.billing = BillingService()
        self.shared_wallets = SharedWalletService()

    async def list_workspaces(self, db: AsyncSession, user_id: UUID) -> list[WorkspaceResponse]:
        result = await db.execute(
            select(Organization, OrganizationMember.role, func.count(OrganizationMember.id).over(partition_by=Organization.id))
            .join(OrganizationMember, OrganizationMember.organization_id == Organization.id)
            .where(OrganizationMember.user_id == user_id)
            .order_by(Organization.created_at.desc())
        )
        return [
            WorkspaceResponse(
                id=org.id,
                name=org.name,
                workspace_type=org.workspace_type,
                role=role,
                members_count=members_count,
                shared_wallet_id=org.shared_wallet_id,
            )
            for org, role, members_count in result.all()
        ]

    async def create_workspace(self, db: AsyncSession, user_id: UUID, data: WorkspaceCreate) -> Organization:
        status = await self.billing.get_status(db, user_id)
        entitlement = status.entitlements.get("shared_workspace")
        existing = await self.list_workspaces(db, user_id)
        if not entitlement or not entitlement.enabled or (
            entitlement.limit is not None and len(existing) >= entitlement.limit
        ):
            raise ValueError("premium_required")

        org = Organization(owner_id=user_id, name=data.name.strip(), workspace_type=data.workspace_type)
        db.add(org)
        await db.flush()
        db.add(OrganizationMember(organization_id=org.id, user_id=user_id, role=WorkspaceRole.OWNER))

        if data.workspace_type == WorkspaceType.FAMILY:
            wallet_name = f"{org.name} · Ortak"
            shared = SharedWallet(
                name=wallet_name,
                owner_id=user_id,
                organization_id=org.id,
                member_ids=f'["{user_id}"]',
            )
            db.add(shared)
            await db.flush()
            org.shared_wallet_id = shared.id

        await self.audit.log(
            db,
            actor_user_id=user_id,
            action="workspace.created",
            resource_type="organization",
            resource_id=str(org.id),
            metadata={"workspace_type": data.workspace_type.value},
        )
        await db.commit()
        await db.refresh(org)
        return org

    async def invite_member(self, db: AsyncSession, user_id: UUID, org_id: UUID, data: WorkspaceInvite) -> Invitation:
        member = await self._require_admin(db, user_id, org_id)
        token = secrets.token_urlsafe(32)
        invitation = Invitation(
            organization_id=member.organization_id,
            email=str(data.email).strip().lower(),
            role=data.role,
            token=token,
            expires_at=datetime.utcnow() + timedelta(days=7),
        )
        db.add(invitation)
        await self.audit.log(
            db,
            actor_user_id=user_id,
            action="workspace.invite_created",
            resource_type="organization",
            resource_id=str(org_id),
            metadata={"email": invitation.email, "role": data.role.value},
        )
        await db.commit()
        await db.refresh(invitation)
        return invitation

    async def list_inbox(self, db: AsyncSession, user_id: UUID, email: str) -> list[dict]:
        normalized = email.strip().lower()
        result = await db.execute(
            select(Invitation, Organization.name)
            .join(Organization, Organization.id == Invitation.organization_id)
            .where(Invitation.email == normalized, Invitation.status == "pending")
            .order_by(Invitation.created_at.desc())
        )
        rows = []
        for invitation, org_name in result.all():
            member_check = await db.execute(
                select(OrganizationMember).where(
                    OrganizationMember.organization_id == invitation.organization_id,
                    OrganizationMember.user_id == user_id,
                )
            )
            if member_check.scalars().first():
                continue
            rows.append({
                "id": invitation.id,
                "organization_id": invitation.organization_id,
                "organization_name": org_name,
                "role": invitation.role,
                "token": invitation.token,
                "accept_url": workspace_invite_url(invitation.token),
                "created_at": invitation.created_at,
            })
        return rows

    async def accept_invitation(self, db: AsyncSession, user_id: UUID, email: str, token: str) -> Organization:
        normalized = email.strip().lower()
        result = await db.execute(
            select(Invitation, Organization)
            .join(Organization, Organization.id == Invitation.organization_id)
            .where(Invitation.token == token, Invitation.status == "pending")
        )
        row = result.first()
        if not row:
            raise ValueError("invitation.not_found")
        invitation, org = row

        # Suresi dolmus davet kabul edilmez ve kapatilir.
        if invitation.expires_at is not None and invitation.expires_at < datetime.utcnow():
            invitation.status = "expired"
            await db.commit()
            raise ValueError("invitation.not_found")

        if invitation.email != normalized:
            raise ValueError("invitation.email_mismatch")

        existing = await db.execute(
            select(OrganizationMember).where(
                OrganizationMember.organization_id == org.id,
                OrganizationMember.user_id == user_id,
            )
        )
        if existing.scalars().first():
            invitation.status = "accepted"
            await db.commit()
            return org

        db.add(OrganizationMember(organization_id=org.id, user_id=user_id, role=invitation.role))
        invitation.status = "accepted"
        if org.shared_wallet_id:
            wallet = await db.get(SharedWallet, org.shared_wallet_id)
            if wallet:
                import json
                members = json.loads(wallet.member_ids or "[]")
                uid = str(user_id)
                if uid not in members:
                    members.append(uid)
                    wallet.member_ids = json.dumps(members)
        await self.audit.log(
            db,
            actor_user_id=user_id,
            action="workspace.invite_accepted",
            resource_type="organization",
            resource_id=str(org.id),
            metadata={"email": normalized, "role": invitation.role.value},
        )
        await db.commit()
        await db.refresh(org)
        return org

    async def list_invitations(self, db: AsyncSession, user_id: UUID, org_id: UUID) -> list[Invitation]:
        await self._require_admin(db, user_id, org_id)
        result = await db.execute(
            select(Invitation)
            .where(Invitation.organization_id == org_id, Invitation.status == "pending")
            .order_by(Invitation.created_at.desc())
        )
        return list(result.scalars().all())

    async def cancel_invitation(self, db: AsyncSession, user_id: UUID, org_id: UUID, invitation_id: UUID) -> None:
        await self._require_admin(db, user_id, org_id)
        result = await db.execute(
            select(Invitation).where(
                Invitation.id == invitation_id,
                Invitation.organization_id == org_id,
                Invitation.status == "pending",
            )
        )
        invitation = result.scalars().first()
        if not invitation:
            raise ValueError("invitation.not_found")
        invitation.status = "cancelled"
        await db.commit()

    async def get_budget_summary(self, db: AsyncSession, user_id: UUID, org_id: UUID) -> FamilyBudgetSummary:
        await self._require_member(db, user_id, org_id)
        org = await db.get(Organization, org_id)
        if not org:
            raise ValueError("workspace.not_found")
        balance = 0.0
        currency = "TRY"
        recent: list[dict] = []
        monthly_total = 0.0
        if org.shared_wallet_id:
            wallet = await db.get(SharedWallet, org.shared_wallet_id)
            if wallet:
                balance = float(wallet.balance or 0)
                currency = wallet.currency or "TRY"
                from datetime import datetime
                month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                spent_r = await db.execute(
                    select(func.coalesce(func.sum(SharedWalletEntry.amount), 0)).where(
                        SharedWalletEntry.wallet_id == wallet.id,
                        SharedWalletEntry.entry_type == "expense",
                        SharedWalletEntry.created_at >= month_start,
                    )
                )
                monthly_total = float(spent_r.scalar() or 0)
                entries_r = await db.execute(
                    select(SharedWalletEntry, User.full_name, User.email)
                    .outerjoin(User, User.id == SharedWalletEntry.user_id)
                    .where(
                        SharedWalletEntry.wallet_id == wallet.id,
                        SharedWalletEntry.entry_type == "expense",
                    )
                    .order_by(SharedWalletEntry.created_at.desc())
                    .limit(8)
                )
                for entry, full_name, email in entries_r.all():
                    recent.append({
                        "amount": float(entry.amount),
                        "description": entry.description,
                        "by": full_name or email or "—",
                        "created_at": entry.created_at.isoformat() if entry.created_at else None,
                    })
        members_r = await db.execute(
            select(func.count(OrganizationMember.id)).where(OrganizationMember.organization_id == org.id)
        )
        return FamilyBudgetSummary(
            organization_id=org.id,
            organization_name=org.name,
            shared_wallet_id=org.shared_wallet_id,
            balance=balance,
            currency=currency,
            members_count=int(members_r.scalar() or 0),
            recent_expenses=recent,
            monthly_total=monthly_total,
        )

    async def _require_member(self, db: AsyncSession, user_id: UUID, org_id: UUID) -> OrganizationMember:
        result = await db.execute(
            select(OrganizationMember).where(
                OrganizationMember.organization_id == org_id,
                OrganizationMember.user_id == user_id,
            )
        )
        member = result.scalars().first()
        if not member:
            raise ValueError("workspace.forbidden")
        return member

    async def _require_admin(self, db: AsyncSession, user_id: UUID, org_id: UUID) -> OrganizationMember:
        result = await db.execute(
            select(OrganizationMember).where(
                OrganizationMember.organization_id == org_id,
                OrganizationMember.user_id == user_id,
            )
        )
        member = result.scalars().first()
        if not member or member.role not in (WorkspaceRole.OWNER, WorkspaceRole.ADMIN):
            raise ValueError("workspace.forbidden")
        return member
