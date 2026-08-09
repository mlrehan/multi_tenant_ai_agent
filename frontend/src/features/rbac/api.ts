import { apiFetch } from "@/lib/api-client";
import type {
  OverrideEffect,
  PermissionSummary,
  RolePermissionMap,
  RoleSummary,
  TenantPermissionSummary,
} from "@/lib/types";

// ---- Tenant scope ----

export function getMyTenantEffectivePermissions(tenantId: string) {
  return apiFetch<{ permissions: string[] }>(`v1/tenants/${tenantId}/me/effective-permissions`, {
    tenantId,
  });
}

export function listTenantRoles(tenantId: string) {
  return apiFetch<RoleSummary[]>(`v1/tenants/${tenantId}/roles`, { tenantId });
}

export function listTenantPermissions(tenantId: string) {
  return apiFetch<TenantPermissionSummary[]>(`v1/tenants/${tenantId}/permissions`, { tenantId });
}

export function createCustomRole(
  tenantId: string,
  body: { code: string; name: string; description: string | null; rank: number; permissionCodes: string[] },
) {
  return apiFetch<{ role_id: string }>(`v1/tenants/${tenantId}/roles`, {
    method: "POST",
    tenantId,
    body: {
      code: body.code,
      name: body.name,
      description: body.description,
      rank: body.rank,
      permission_codes: body.permissionCodes,
    },
  });
}

export function createRoleHierarchyEdge(tenantId: string, parentRoleCode: string, childRoleCode: string) {
  return apiFetch<void>(`v1/tenants/${tenantId}/roles/hierarchy`, {
    method: "POST",
    tenantId,
    body: { parent_role_code: parentRoleCode, child_role_code: childRoleCode },
  });
}

export function assignMembershipRole(tenantId: string, membershipId: string, roleCode: string) {
  return apiFetch<void>(`v1/tenants/${tenantId}/memberships/${membershipId}/roles`, {
    method: "POST",
    tenantId,
    body: { role_code: roleCode },
  });
}

export function revokeMembershipRole(tenantId: string, membershipId: string, roleCode: string) {
  return apiFetch<void>(
    `v1/tenants/${tenantId}/memberships/${membershipId}/roles/${encodeURIComponent(roleCode)}`,
    { method: "DELETE", tenantId },
  );
}

export function createOverride(
  tenantId: string,
  body: {
    targetMembershipId: string;
    permissionCode: string;
    effect: OverrideEffect;
    reason: string;
    expiresAt: string | null;
  },
) {
  return apiFetch<{ override_id: string }>(`v1/tenants/${tenantId}/overrides`, {
    method: "POST",
    tenantId,
    body: {
      target_membership_id: body.targetMembershipId,
      permission_code: body.permissionCode,
      effect: body.effect,
      reason: body.reason,
      expires_at: body.expiresAt,
    },
  });
}

export function revokeOverride(tenantId: string, overrideId: string) {
  return apiFetch<void>(`v1/tenants/${tenantId}/overrides/${overrideId}`, {
    method: "DELETE",
    tenantId,
  });
}

export function addPermissionToRole(tenantId: string, roleCode: string, permissionCode: string) {
  return apiFetch<void>(
    `v1/tenants/${tenantId}/roles/${encodeURIComponent(roleCode)}/permissions/${encodeURIComponent(permissionCode)}`,
    { method: "POST", tenantId },
  );
}

export function removePermissionFromRole(tenantId: string, roleCode: string, permissionCode: string) {
  return apiFetch<void>(
    `v1/tenants/${tenantId}/roles/${encodeURIComponent(roleCode)}/permissions/${encodeURIComponent(permissionCode)}`,
    { method: "DELETE", tenantId },
  );
}

export function getTenantRolePermissions(tenantId: string) {
  return apiFetch<RolePermissionMap>(`v1/tenants/${tenantId}/roles/permissions`, { tenantId });
}

// ---- Platform scope ----

export function getMyPlatformEffectivePermissions() {
  return apiFetch<{ permissions: string[] }>("v1/platform/me/effective-permissions");
}

export function listPlatformRoles() {
  return apiFetch<RoleSummary[]>("v1/platform/roles");
}

export function listPlatformPermissions() {
  return apiFetch<PermissionSummary[]>("v1/platform/permissions");
}

export function grantPlatformRole(targetUserId: string, roleCode: string) {
  return apiFetch<void>("v1/platform/roles/grant", {
    method: "POST",
    body: { target_user_id: targetUserId, role_code: roleCode },
  });
}

export function revokePlatformRole(targetUserId: string, roleCode: string) {
  return apiFetch<void>("v1/platform/roles/revoke", {
    method: "POST",
    body: { target_user_id: targetUserId, role_code: roleCode },
  });
}

export function createPlatformRole(body: {
  code: string;
  name: string;
  description: string | null;
  rank: number;
  permissionCodes: string[];
}) {
  return apiFetch<{ role_id: string }>("v1/platform/roles", {
    method: "POST",
    body: {
      code: body.code,
      name: body.name,
      description: body.description,
      rank: body.rank,
      permission_codes: body.permissionCodes,
    },
  });
}

export function addPermissionToPlatformRole(roleCode: string, permissionCode: string) {
  return apiFetch<void>(
    `v1/platform/roles/${encodeURIComponent(roleCode)}/permissions/${encodeURIComponent(permissionCode)}`,
    { method: "POST" },
  );
}

export function removePermissionFromPlatformRole(roleCode: string, permissionCode: string) {
  return apiFetch<void>(
    `v1/platform/roles/${encodeURIComponent(roleCode)}/permissions/${encodeURIComponent(permissionCode)}`,
    { method: "DELETE" },
  );
}
