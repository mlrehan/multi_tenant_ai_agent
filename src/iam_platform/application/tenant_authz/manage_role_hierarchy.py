"""Tenant role-hierarchy edge creation -- docs/06-authorization-model.md,
docs/14-schema-tenant-authorization.md.

Creating an edge grants the parent role every permission the child role
carries (directly or via its own inheritance) -- that's exactly the kind of
transfer the self-escalation guard exists to police, so it's applied here
too, not just at direct role assignment.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from iam_platform.application.tenant_authz.effective_permissions import compute_effective_tenant_state
from iam_platform.application.tenant_authz.exceptions import (
    PermissionDeniedError,
    RoleNotFoundError,
    SelfEscalationError,
)
from iam_platform.application.tenant_authz.ports import TenantUowFactory
from iam_platform.core.clock import Clock
from iam_platform.domain.shared.exceptions import InvariantViolationError
from iam_platform.domain.shared.policies import (
    can_assign_role,
    expand_role_hierarchy,
    validate_hierarchy_edge,
)
from iam_platform.domain.tenant_authz.entities import RoleHierarchyEdge, RoleScope

_MANAGE_ROLES_PERMISSION = "tenant.roles.manage"


@dataclass(frozen=True, slots=True)
class CreateRoleHierarchyEdgeCommand:
    actor_user_id: str
    tenant_id: str
    parent_role_code: str
    child_role_code: str


class CreateRoleHierarchyEdge:
    def __init__(self, uow_factory: TenantUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: CreateRoleHierarchyEdgeCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id, tenant_id) as uow:
            actor_state = await compute_effective_tenant_state(uow, tenant_id, actor_id, now=now)
            if actor_state is None or _MANAGE_ROLES_PERMISSION not in actor_state.permissions:
                raise PermissionDeniedError(_MANAGE_ROLES_PERMISSION)

            parent_role = await uow.tenant_roles.get_by_code(
                tenant_id, command.parent_role_code
            ) or await uow.tenant_roles.get_by_code(None, command.parent_role_code)
            child_role = await uow.tenant_roles.get_by_code(
                tenant_id, command.child_role_code
            ) or await uow.tenant_roles.get_by_code(None, command.child_role_code)
            if parent_role is None:
                raise RoleNotFoundError(command.parent_role_code)
            if child_role is None:
                raise RoleNotFoundError(command.child_role_code)

            edges = await uow.role_hierarchy.list_edges_by_parent(
                scope=RoleScope.TENANT, tenant_id=tenant_id
            )
            try:
                validate_hierarchy_edge(parent_role.id, child_role.id, edges)
            except InvariantViolationError as exc:
                raise SelfEscalationError([str(exc)]) from exc

            # The full permission set the parent role would gain by inheriting
            # from the child (child's own direct permissions plus whatever the
            # child already inherits) must be a subset of the actor's own
            # effective permissions.
            child_expanded_roles = expand_role_hierarchy({child_role.id}, edges) | {child_role.id}
            role_permission_codes = await uow.tenant_permissions.get_role_permission_codes(
                child_expanded_roles
            )
            child_full_permissions: set[str] = set()
            for codes in role_permission_codes.values():
                child_full_permissions |= codes

            violations = can_assign_role(
                actor_effective_permissions=actor_state.permissions,
                actor_highest_rank=actor_state.highest_role_rank,
                is_self_assignment=False,
                target_role_rank=parent_role.rank,
                target_role_permission_codes=frozenset(child_full_permissions),
            )
            if violations:
                raise SelfEscalationError(violations)

            edge = RoleHierarchyEdge(
                id=uuid4(),
                parent_role_id=parent_role.id,
                child_role_id=child_role.id,
                role_scope=RoleScope.TENANT,
                tenant_id=tenant_id,
                created_at=now,
            )
            await uow.role_hierarchy.add(edge)
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=None,
                tenant_id=tenant_id,
                action="tenant_authz.role_hierarchy_edge_created",
                resource_type="role_hierarchy",
                resource_id=edge.id,
                result="success",
                metadata={"parent": command.parent_role_code, "child": command.child_role_code},
            )
