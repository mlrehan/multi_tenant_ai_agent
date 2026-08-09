"""SQLAlchemy implementations of the tenancy repository ports."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from iam_platform.domain.shared.value_objects import Email
from iam_platform.domain.tenancy.entities import (
    InvitationStatus,
    MembershipStatus,
    Tenant,
    TenantInvitation,
    TenantMembership,
    TenantStatus,
)
from iam_platform.infrastructure.db.models.tenancy import (
    TenantFeatureModel,
    TenantInvitationModel,
    TenantMembershipModel,
    TenantModel,
)


def _tenant_to_domain(m: TenantModel) -> Tenant:
    return Tenant(
        id=m.id,
        slug=m.slug,
        display_name=m.display_name,
        status=TenantStatus(m.status),
        owner_user_id=m.owner_user_id,
        region=m.region,
        created_at=m.created_at,
        updated_at=m.updated_at,
        suspended_at=m.suspended_at,
        suspended_reason=m.suspended_reason,
        deleted_at=m.deleted_at,
    )


class SqlTenantRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, tenant_id: UUID) -> Tenant | None:
        model = await self._session.get(TenantModel, tenant_id)
        return _tenant_to_domain(model) if model else None

    async def get_by_slug(self, slug: str) -> Tenant | None:
        stmt = select(TenantModel).where(TenantModel.slug == slug)
        model = (await self._session.execute(stmt)).scalars().first()
        return _tenant_to_domain(model) if model else None

    async def list_all(self) -> list[Tenant]:
        stmt = select(TenantModel).order_by(TenantModel.created_at.desc())
        models = (await self._session.execute(stmt)).scalars().all()
        return [_tenant_to_domain(m) for m in models]

    async def add(self, tenant: Tenant) -> None:
        self._session.add(
            TenantModel(
                id=tenant.id,
                slug=tenant.slug,
                display_name=tenant.display_name,
                status=tenant.status.value,
                owner_user_id=tenant.owner_user_id,
                region=tenant.region,
                suspended_at=tenant.suspended_at,
                suspended_reason=tenant.suspended_reason,
                deleted_at=tenant.deleted_at,
            )
        )
        await self._session.flush()

    async def save(self, tenant: Tenant) -> None:
        await self._session.execute(
            update(TenantModel)
            .where(TenantModel.id == tenant.id)
            .values(
                display_name=tenant.display_name,
                status=tenant.status.value,
                region=tenant.region,
                suspended_at=tenant.suspended_at,
                suspended_reason=tenant.suspended_reason,
                deleted_at=tenant.deleted_at,
            )
        )


def _membership_to_domain(m: TenantMembershipModel) -> TenantMembership:
    return TenantMembership(
        id=m.id,
        tenant_id=m.tenant_id,
        user_id=m.user_id,
        status=MembershipStatus(m.status),
        is_default=m.is_default,
        department_id=m.department_id,
        team_id=m.team_id,
        job_title=m.job_title,
        metadata=dict(m.metadata_),
        invited_by_user_id=m.invited_by_user_id,
        invited_at=m.invited_at,
        joined_at=m.joined_at,
        last_activity_at=m.last_activity_at,
        suspended_at=m.suspended_at,
        suspended_reason=m.suspended_reason,
        revoked_at=m.revoked_at,
        revoked_reason=m.revoked_reason,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


class SqlTenantMembershipRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, membership_id: UUID) -> TenantMembership | None:
        model = await self._session.get(TenantMembershipModel, membership_id)
        return _membership_to_domain(model) if model else None

    async def get_by_tenant_and_user(self, tenant_id: UUID, user_id: UUID) -> TenantMembership | None:
        stmt = select(TenantMembershipModel).where(
            TenantMembershipModel.tenant_id == tenant_id, TenantMembershipModel.user_id == user_id
        )
        model = (await self._session.execute(stmt)).scalars().first()
        return _membership_to_domain(model) if model else None

    async def list_by_tenant(self, tenant_id: UUID) -> list[TenantMembership]:
        stmt = select(TenantMembershipModel).where(TenantMembershipModel.tenant_id == tenant_id)
        models = (await self._session.execute(stmt)).scalars().all()
        return [_membership_to_domain(m) for m in models]

    async def list_by_user(self, user_id: UUID) -> list[TenantMembership]:
        stmt = select(TenantMembershipModel).where(TenantMembershipModel.user_id == user_id)
        models = (await self._session.execute(stmt)).scalars().all()
        return [_membership_to_domain(m) for m in models]

    async def add(self, membership: TenantMembership) -> None:
        self._session.add(
            TenantMembershipModel(
                id=membership.id,
                tenant_id=membership.tenant_id,
                user_id=membership.user_id,
                status=membership.status.value,
                is_default=membership.is_default,
                department_id=membership.department_id,
                team_id=membership.team_id,
                job_title=membership.job_title,
                metadata_=membership.metadata,
                invited_by_user_id=membership.invited_by_user_id,
                invited_at=membership.invited_at,
                joined_at=membership.joined_at,
                last_activity_at=membership.last_activity_at,
                suspended_at=membership.suspended_at,
                suspended_reason=membership.suspended_reason,
                revoked_at=membership.revoked_at,
                revoked_reason=membership.revoked_reason,
            )
        )
        await self._session.flush()

    async def save(self, membership: TenantMembership) -> None:
        await self._session.execute(
            update(TenantMembershipModel)
            .where(TenantMembershipModel.id == membership.id)
            .values(
                status=membership.status.value,
                is_default=membership.is_default,
                department_id=membership.department_id,
                team_id=membership.team_id,
                job_title=membership.job_title,
                metadata_=membership.metadata,
                joined_at=membership.joined_at,
                last_activity_at=membership.last_activity_at,
                suspended_at=membership.suspended_at,
                suspended_reason=membership.suspended_reason,
                revoked_at=membership.revoked_at,
                revoked_reason=membership.revoked_reason,
            )
        )


def _invitation_to_domain(m: TenantInvitationModel) -> TenantInvitation:
    return TenantInvitation(
        id=m.id,
        tenant_id=m.tenant_id,
        email=Email(m.email),
        invited_by_user_id=m.invited_by_user_id,
        role_ids=list(m.role_ids),
        status=InvitationStatus(m.status),
        token_hash=m.token_hash,
        department_id=m.department_id,
        team_id=m.team_id,
        expires_at=m.expires_at,
        accepted_at=m.accepted_at,
        accepted_by_user_id=m.accepted_by_user_id,
        created_at=m.created_at,
    )


class SqlTenantInvitationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_token_hash(self, token_hash: str) -> TenantInvitation | None:
        stmt = select(TenantInvitationModel).where(TenantInvitationModel.token_hash == token_hash)
        model = (await self._session.execute(stmt)).scalars().first()
        return _invitation_to_domain(model) if model else None

    async def get_pending_by_tenant_and_email(
        self, tenant_id: UUID, email: str
    ) -> TenantInvitation | None:
        stmt = select(TenantInvitationModel).where(
            TenantInvitationModel.tenant_id == tenant_id,
            TenantInvitationModel.email == email,
            TenantInvitationModel.status == "pending",
        )
        model = (await self._session.execute(stmt)).scalars().first()
        return _invitation_to_domain(model) if model else None

    async def add(self, invitation: TenantInvitation) -> None:
        self._session.add(
            TenantInvitationModel(
                id=invitation.id,
                tenant_id=invitation.tenant_id,
                email=str(invitation.email),
                invited_by_user_id=invitation.invited_by_user_id,
                role_ids=invitation.role_ids,
                status=invitation.status.value,
                token_hash=invitation.token_hash,
                department_id=invitation.department_id,
                team_id=invitation.team_id,
                expires_at=invitation.expires_at,
                accepted_at=invitation.accepted_at,
                accepted_by_user_id=invitation.accepted_by_user_id,
            )
        )
        await self._session.flush()

    async def save(self, invitation: TenantInvitation) -> None:
        await self._session.execute(
            update(TenantInvitationModel)
            .where(TenantInvitationModel.id == invitation.id)
            .values(
                status=invitation.status.value,
                accepted_at=invitation.accepted_at,
                accepted_by_user_id=invitation.accepted_by_user_id,
            )
        )


class SqlTenantFeatureRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_enabled_codes(self, tenant_id: UUID) -> set[str]:
        stmt = select(TenantFeatureModel.feature_code).where(
            TenantFeatureModel.tenant_id == tenant_id, TenantFeatureModel.enabled.is_(True)
        )
        result = await self._session.execute(stmt)
        return set(result.scalars().all())

    async def enable(self, tenant_id: UUID, feature_code: str, *, now: datetime) -> None:
        self._session.add(
            TenantFeatureModel(
                id=uuid4(), tenant_id=tenant_id, feature_code=feature_code, enabled=True, source="override"
            )
        )
        await self._session.flush()
