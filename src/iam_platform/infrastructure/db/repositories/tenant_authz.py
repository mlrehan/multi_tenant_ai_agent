"""SQLAlchemy implementations of the tenant-authorization repository ports."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from iam_platform.domain.tenant_authz.entities import (
    AuthorizationOverride,
    OverrideEffect,
    OverrideScope,
    OverrideSubjectType,
    RoleHierarchyEdge,
    RoleScope,
    TenantMembershipRole,
    TenantPermission,
    TenantRole,
)
from iam_platform.infrastructure.db.models.tenant_authz import (
    AuthorizationOverrideModel,
    RoleHierarchyModel,
    TenantMembershipRoleModel,
    TenantPermissionModel,
    TenantRoleModel,
    TenantRolePermissionModel,
)


def _role_to_domain(m: TenantRoleModel) -> TenantRole:
    return TenantRole(
        id=m.id,
        tenant_id=m.tenant_id,
        code=m.code,
        name=m.name,
        description=m.description,
        is_system=m.is_system,
        rank=m.rank,
        created_by_user_id=m.created_by_user_id,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


class SqlTenantRoleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, role_id: UUID) -> TenantRole | None:
        model = await self._session.get(TenantRoleModel, role_id)
        return _role_to_domain(model) if model else None

    async def get_by_code(self, tenant_id: UUID | None, code: str) -> TenantRole | None:
        stmt = select(TenantRoleModel).where(
            TenantRoleModel.tenant_id == tenant_id, TenantRoleModel.code == code
        )
        model = (await self._session.execute(stmt)).scalars().first()
        return _role_to_domain(model) if model else None

    async def list_available_to_tenant(self, tenant_id: UUID) -> list[TenantRole]:
        stmt = select(TenantRoleModel).where(
            (TenantRoleModel.tenant_id.is_(None)) | (TenantRoleModel.tenant_id == tenant_id)
        )
        models = (await self._session.execute(stmt)).scalars().all()
        return [_role_to_domain(m) for m in models]

    async def add(self, role: TenantRole) -> None:
        self._session.add(
            TenantRoleModel(
                id=role.id,
                tenant_id=role.tenant_id,
                code=role.code,
                name=role.name,
                description=role.description,
                is_system=role.is_system,
                rank=role.rank,
                created_by_user_id=role.created_by_user_id,
            )
        )
        await self._session.flush()

    async def save(self, role: TenantRole) -> None:
        await self._session.execute(
            update(TenantRoleModel)
            .where(TenantRoleModel.id == role.id)
            .values(name=role.name, description=role.description, rank=role.rank)
        )


def _permission_to_domain(m: TenantPermissionModel) -> TenantPermission:
    return TenantPermission(
        id=m.id,
        code=m.code,
        resource=m.resource,
        action=m.action,
        description=m.description,
        risk_level=m.risk_level,
        is_system=m.is_system,
        tenant_customizable=m.tenant_customizable,
        required_feature=m.required_feature,
        created_at=m.created_at,
    )


class SqlTenantPermissionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, permission_id: UUID) -> TenantPermission | None:
        model = await self._session.get(TenantPermissionModel, permission_id)
        return _permission_to_domain(model) if model else None

    async def get_by_code(self, code: str) -> TenantPermission | None:
        stmt = select(TenantPermissionModel).where(TenantPermissionModel.code == code)
        model = (await self._session.execute(stmt)).scalars().first()
        return _permission_to_domain(model) if model else None

    async def list_by_codes(self, codes: set[str]) -> list[TenantPermission]:
        if not codes:
            return []
        stmt = select(TenantPermissionModel).where(TenantPermissionModel.code.in_(codes))
        models = (await self._session.execute(stmt)).scalars().all()
        return [_permission_to_domain(m) for m in models]

    async def list_all(self) -> list[TenantPermission]:
        stmt = select(TenantPermissionModel).order_by(TenantPermissionModel.code)
        models = (await self._session.execute(stmt)).scalars().all()
        return [_permission_to_domain(m) for m in models]

    async def get_role_permission_codes(self, role_ids: set[UUID]) -> dict[UUID, set[str]]:
        if not role_ids:
            return {}
        stmt = (
            select(TenantRolePermissionModel.role_id, TenantPermissionModel.code)
            .join(
                TenantPermissionModel,
                TenantPermissionModel.id == TenantRolePermissionModel.permission_id,
            )
            .where(TenantRolePermissionModel.role_id.in_(role_ids))
        )
        result: dict[UUID, set[str]] = {rid: set() for rid in role_ids}
        for role_id, code in (await self._session.execute(stmt)).all():
            result[role_id].add(code)
        return result

    async def assign_to_role(self, *, role_id: UUID, permission_code: str, now: datetime) -> None:
        permission = await self.get_by_code(permission_code)
        if permission is None:
            raise ValueError(f"unknown permission code: {permission_code}")
        role = await self._session.get(TenantRoleModel, role_id)
        self._session.add(
            TenantRolePermissionModel(
                role_id=role_id,
                permission_id=permission.id,
                tenant_id=role.tenant_id if role is not None else None,
            )
        )
        await self._session.flush()

    async def revoke_from_role(self, *, role_id: UUID, permission_code: str) -> None:
        permission = await self.get_by_code(permission_code)
        if permission is None:
            return
        stmt = select(TenantRolePermissionModel).where(
            TenantRolePermissionModel.role_id == role_id,
            TenantRolePermissionModel.permission_id == permission.id,
        )
        model = (await self._session.execute(stmt)).scalars().first()
        if model is not None:
            await self._session.delete(model)
            await self._session.flush()


def _assignment_to_domain(m: TenantMembershipRoleModel) -> TenantMembershipRole:
    return TenantMembershipRole(
        id=m.id,
        tenant_id=m.tenant_id,
        membership_id=m.membership_id,
        role_id=m.role_id,
        granted_by_user_id=m.granted_by_user_id,
        granted_at=m.granted_at,
        revoked_at=m.revoked_at,
    )


class SqlTenantMembershipRoleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_active_by_membership(self, membership_id: UUID) -> list[TenantMembershipRole]:
        stmt = select(TenantMembershipRoleModel).where(
            TenantMembershipRoleModel.membership_id == membership_id,
            TenantMembershipRoleModel.revoked_at.is_(None),
        )
        models = (await self._session.execute(stmt)).scalars().all()
        return [_assignment_to_domain(m) for m in models]

    async def get_active(self, *, membership_id: UUID, role_id: UUID) -> TenantMembershipRole | None:
        stmt = select(TenantMembershipRoleModel).where(
            TenantMembershipRoleModel.membership_id == membership_id,
            TenantMembershipRoleModel.role_id == role_id,
            TenantMembershipRoleModel.revoked_at.is_(None),
        )
        model = (await self._session.execute(stmt)).scalars().first()
        return _assignment_to_domain(model) if model else None

    async def add(self, assignment: TenantMembershipRole) -> None:
        self._session.add(
            TenantMembershipRoleModel(
                id=assignment.id,
                tenant_id=assignment.tenant_id,
                membership_id=assignment.membership_id,
                role_id=assignment.role_id,
                granted_by_user_id=assignment.granted_by_user_id,
            )
        )
        await self._session.flush()

    async def save(self, assignment: TenantMembershipRole) -> None:
        await self._session.execute(
            update(TenantMembershipRoleModel)
            .where(TenantMembershipRoleModel.id == assignment.id)
            .values(revoked_at=assignment.revoked_at)
        )


class SqlRoleHierarchyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_edges_by_parent(
        self, *, scope: RoleScope, tenant_id: UUID | None
    ) -> dict[UUID, list[UUID]]:
        stmt = select(RoleHierarchyModel.parent_role_id, RoleHierarchyModel.child_role_id).where(
            RoleHierarchyModel.role_scope == scope.value
        )
        if scope == RoleScope.TENANT:
            stmt = stmt.where(
                (RoleHierarchyModel.tenant_id.is_(None)) | (RoleHierarchyModel.tenant_id == tenant_id)
            )
        result: dict[UUID, list[UUID]] = {}
        for parent_id, child_id in (await self._session.execute(stmt)).all():
            result.setdefault(parent_id, []).append(child_id)
        return result

    async def add(self, edge: RoleHierarchyEdge) -> None:
        self._session.add(
            RoleHierarchyModel(
                id=edge.id,
                parent_role_id=edge.parent_role_id,
                child_role_id=edge.child_role_id,
                role_scope=edge.role_scope.value,
                tenant_id=edge.tenant_id,
            )
        )
        await self._session.flush()


def _override_to_domain(m: AuthorizationOverrideModel) -> AuthorizationOverride:
    return AuthorizationOverride(
        id=m.id,
        scope=OverrideScope(m.scope),
        tenant_id=m.tenant_id,
        subject_type=OverrideSubjectType(m.subject_type),
        subject_id=m.subject_id,
        platform_permission_id=m.platform_permission_id,
        tenant_permission_id=m.tenant_permission_id,
        effect=OverrideEffect(m.effect),
        resource_type=m.resource_type,
        resource_id=m.resource_id,
        reason=m.reason,
        created_by_user_id=m.created_by_user_id,
        expires_at=m.expires_at,
        created_at=m.created_at,
        revoked_at=m.revoked_at,
    )


class SqlAuthorizationOverrideRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_active_for_subject(
        self, *, scope: OverrideScope, tenant_id: UUID | None, subject_id: UUID, now: datetime
    ) -> list[AuthorizationOverride]:
        stmt = select(AuthorizationOverrideModel).where(
            AuthorizationOverrideModel.scope == scope.value,
            AuthorizationOverrideModel.subject_id == subject_id,
            AuthorizationOverrideModel.revoked_at.is_(None),
        )
        if scope == OverrideScope.TENANT:
            stmt = stmt.where(AuthorizationOverrideModel.tenant_id == tenant_id)
        models = (await self._session.execute(stmt)).scalars().all()
        return [
            _override_to_domain(m)
            for m in models
            if m.expires_at is None or now < m.expires_at
        ]

    async def add(self, override: AuthorizationOverride) -> None:
        self._session.add(
            AuthorizationOverrideModel(
                id=override.id,
                scope=override.scope.value,
                tenant_id=override.tenant_id,
                subject_type=override.subject_type.value,
                subject_id=override.subject_id,
                platform_permission_id=override.platform_permission_id,
                tenant_permission_id=override.tenant_permission_id,
                effect=override.effect.value,
                resource_type=override.resource_type,
                resource_id=override.resource_id,
                reason=override.reason,
                created_by_user_id=override.created_by_user_id,
                expires_at=override.expires_at,
            )
        )
        await self._session.flush()

    async def revoke(self, override_id: UUID, *, now: datetime) -> None:
        await self._session.execute(
            update(AuthorizationOverrideModel)
            .where(AuthorizationOverrideModel.id == override_id)
            .values(revoked_at=now)
        )
