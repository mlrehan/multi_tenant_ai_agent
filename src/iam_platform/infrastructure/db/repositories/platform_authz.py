"""SQLAlchemy implementations of the platform-authorization repository ports."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from iam_platform.domain.platform_authz.entities import (
    ImpersonationSession,
    PlatformPermission,
    PlatformRole,
    PlatformUserRole,
)
from iam_platform.infrastructure.db.models.platform_authz import (
    ImpersonationSessionModel,
    PlatformPermissionModel,
    PlatformRoleModel,
    PlatformRolePermissionModel,
    PlatformUserRoleModel,
)


def _role_to_domain(m: PlatformRoleModel) -> PlatformRole:
    return PlatformRole(
        id=m.id,
        code=m.code,
        name=m.name,
        description=m.description,
        is_system=m.is_system,
        rank=m.rank,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


class SqlPlatformRoleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, role_id: UUID) -> PlatformRole | None:
        model = await self._session.get(PlatformRoleModel, role_id)
        return _role_to_domain(model) if model else None

    async def get_by_code(self, code: str) -> PlatformRole | None:
        stmt = select(PlatformRoleModel).where(PlatformRoleModel.code == code)
        model = (await self._session.execute(stmt)).scalars().first()
        return _role_to_domain(model) if model else None

    async def list_all(self) -> list[PlatformRole]:
        models = (await self._session.execute(select(PlatformRoleModel))).scalars().all()
        return [_role_to_domain(m) for m in models]

    async def add(self, role: PlatformRole) -> None:
        self._session.add(
            PlatformRoleModel(
                id=role.id,
                code=role.code,
                name=role.name,
                description=role.description,
                is_system=role.is_system,
                rank=role.rank,
            )
        )
        await self._session.flush()

    async def save(self, role: PlatformRole) -> None:
        await self._session.execute(
            update(PlatformRoleModel)
            .where(PlatformRoleModel.id == role.id)
            .values(name=role.name, description=role.description, rank=role.rank)
        )


def _permission_to_domain(m: PlatformPermissionModel) -> PlatformPermission:
    return PlatformPermission(
        id=m.id,
        code=m.code,
        resource=m.resource,
        action=m.action,
        description=m.description,
        risk_level=m.risk_level,
        is_system=m.is_system,
        created_at=m.created_at,
    )


class SqlPlatformPermissionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, permission_id: UUID) -> PlatformPermission | None:
        model = await self._session.get(PlatformPermissionModel, permission_id)
        return _permission_to_domain(model) if model else None

    async def get_by_code(self, code: str) -> PlatformPermission | None:
        stmt = select(PlatformPermissionModel).where(PlatformPermissionModel.code == code)
        model = (await self._session.execute(stmt)).scalars().first()
        return _permission_to_domain(model) if model else None

    async def list_all(self) -> list[PlatformPermission]:
        stmt = select(PlatformPermissionModel).order_by(PlatformPermissionModel.code)
        models = (await self._session.execute(stmt)).scalars().all()
        return [_permission_to_domain(m) for m in models]

    async def get_role_permission_codes(self, role_ids: set[UUID]) -> dict[UUID, set[str]]:
        if not role_ids:
            return {}
        stmt = (
            select(PlatformRolePermissionModel.role_id, PlatformPermissionModel.code)
            .join(
                PlatformPermissionModel,
                PlatformPermissionModel.id == PlatformRolePermissionModel.permission_id,
            )
            .where(PlatformRolePermissionModel.role_id.in_(role_ids))
        )
        result: dict[UUID, set[str]] = {rid: set() for rid in role_ids}
        for role_id, code in (await self._session.execute(stmt)).all():
            result[role_id].add(code)
        return result

    async def assign_to_role(self, *, role_id: UUID, permission_code: str) -> None:
        permission = await self.get_by_code(permission_code)
        if permission is None:
            raise ValueError(f"unknown permission code: {permission_code}")
        self._session.add(
            PlatformRolePermissionModel(role_id=role_id, permission_id=permission.id)
        )
        await self._session.flush()

    async def revoke_from_role(self, *, role_id: UUID, permission_code: str) -> None:
        permission = await self.get_by_code(permission_code)
        if permission is None:
            return
        stmt = select(PlatformRolePermissionModel).where(
            PlatformRolePermissionModel.role_id == role_id,
            PlatformRolePermissionModel.permission_id == permission.id,
        )
        model = (await self._session.execute(stmt)).scalars().first()
        if model is not None:
            await self._session.delete(model)
            await self._session.flush()


def _impersonation_to_domain(m: ImpersonationSessionModel) -> ImpersonationSession:
    return ImpersonationSession(
        id=m.id,
        platform_user_id=m.platform_user_id,
        target_user_id=m.target_user_id,
        tenant_id=m.tenant_id,
        reason=m.reason,
        approval_status=m.approval_status,
        approved_by_user_id=m.approved_by_user_id,
        started_at=m.started_at,
        expires_at=m.expires_at,
        ended_at=m.ended_at,
        ip=m.ip,
        session_id=m.session_id,
    )


class SqlPlatformUserRoleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_active_by_user(self, user_id: UUID) -> list[PlatformUserRole]:
        stmt = select(PlatformUserRoleModel).where(
            PlatformUserRoleModel.user_id == user_id, PlatformUserRoleModel.revoked_at.is_(None)
        )
        models = (await self._session.execute(stmt)).scalars().all()
        return [
            PlatformUserRole(
                id=m.id,
                user_id=m.user_id,
                role_id=m.role_id,
                granted_by_user_id=m.granted_by_user_id,
                granted_at=m.granted_at,
                revoked_at=m.revoked_at,
                revoked_by_user_id=m.revoked_by_user_id,
            )
            for m in models
        ]

    async def get_active(self, *, user_id: UUID, role_id: UUID) -> PlatformUserRole | None:
        stmt = select(PlatformUserRoleModel).where(
            PlatformUserRoleModel.user_id == user_id,
            PlatformUserRoleModel.role_id == role_id,
            PlatformUserRoleModel.revoked_at.is_(None),
        )
        m = (await self._session.execute(stmt)).scalars().first()
        if m is None:
            return None
        return PlatformUserRole(
            id=m.id,
            user_id=m.user_id,
            role_id=m.role_id,
            granted_by_user_id=m.granted_by_user_id,
            granted_at=m.granted_at,
            revoked_at=m.revoked_at,
            revoked_by_user_id=m.revoked_by_user_id,
        )

    async def add(self, assignment: PlatformUserRole) -> None:
        self._session.add(
            PlatformUserRoleModel(
                id=assignment.id,
                user_id=assignment.user_id,
                role_id=assignment.role_id,
                granted_by_user_id=assignment.granted_by_user_id,
            )
        )
        await self._session.flush()

    async def save(self, assignment: PlatformUserRole) -> None:
        await self._session.execute(
            update(PlatformUserRoleModel)
            .where(PlatformUserRoleModel.id == assignment.id)
            .values(revoked_at=assignment.revoked_at, revoked_by_user_id=assignment.revoked_by_user_id)
        )


class SqlImpersonationSessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, session_id: UUID) -> ImpersonationSession | None:
        model = await self._session.get(ImpersonationSessionModel, session_id)
        return _impersonation_to_domain(model) if model else None

    async def get_active_for_platform_user(
        self, platform_user_id: UUID, *, now: datetime
    ) -> ImpersonationSession | None:
        stmt = select(ImpersonationSessionModel).where(
            ImpersonationSessionModel.platform_user_id == platform_user_id,
            ImpersonationSessionModel.ended_at.is_(None),
            ImpersonationSessionModel.expires_at > now,
        )
        model = (await self._session.execute(stmt)).scalars().first()
        return _impersonation_to_domain(model) if model else None

    async def add(self, session: ImpersonationSession) -> None:
        self._session.add(
            ImpersonationSessionModel(
                id=session.id,
                platform_user_id=session.platform_user_id,
                target_user_id=session.target_user_id,
                tenant_id=session.tenant_id,
                reason=session.reason,
                approval_status=session.approval_status,
                approved_by_user_id=session.approved_by_user_id,
                expires_at=session.expires_at,
                ended_at=session.ended_at,
                ip=session.ip,
                session_id=session.session_id,
            )
        )
        await self._session.flush()

    async def save(self, session: ImpersonationSession) -> None:
        await self._session.execute(
            update(ImpersonationSessionModel)
            .where(ImpersonationSessionModel.id == session.id)
            .values(ended_at=session.ended_at)
        )
