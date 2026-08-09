import { apiFetch } from "@/lib/api-client";
import type {
  PlatformUserDetail,
  PlatformUserPage,
  RolePermissionMap,
  Tenant,
} from "@/lib/types";

export function listTenants() {
  return apiFetch<Tenant[]>("v1/platform/tenants");
}

export function listUsers(params: { search?: string; limit?: number; offset?: number } = {}) {
  const qs = new URLSearchParams();
  if (params.search) qs.set("search", params.search);
  if (params.limit != null) qs.set("limit", String(params.limit));
  if (params.offset != null) qs.set("offset", String(params.offset));
  const suffix = qs.toString() ? `?${qs}` : "";
  return apiFetch<PlatformUserPage>(`v1/platform/users${suffix}`);
}

export function getUser(userId: string) {
  return apiFetch<PlatformUserDetail>(`v1/platform/users/${userId}`);
}

export function createUser(email: string, password: string) {
  return apiFetch<{ user_id: string; email: string }>("v1/platform/users", {
    method: "POST",
    body: { email, password },
  });
}

export function updateUser(userId: string, email: string) {
  return apiFetch<void>(`v1/platform/users/${userId}`, { method: "PATCH", body: { email } });
}

export function deleteUser(userId: string) {
  return apiFetch<void>(`v1/platform/users/${userId}`, { method: "DELETE" });
}

export function suspendUser(userId: string, reason: string | null) {
  return apiFetch<void>(`v1/platform/users/${userId}/suspend`, {
    method: "POST",
    body: { reason },
  });
}

export function reactivateUser(userId: string) {
  return apiFetch<void>(`v1/platform/users/${userId}/reactivate`, { method: "POST" });
}

export function listPlatformRolePermissions() {
  return apiFetch<RolePermissionMap>("v1/platform/roles/permissions");
}

export function createTenant(slug: string, displayName: string, ownerUserId: string) {
  return apiFetch<{ tenant_id: string }>("v1/platform/tenants", {
    method: "POST",
    body: { slug, display_name: displayName, owner_user_id: ownerUserId },
  });
}

export function suspendTenant(tenantId: string, reason: string) {
  return apiFetch<void>(`v1/platform/tenants/${tenantId}/suspend`, {
    method: "POST",
    body: { reason },
  });
}

export function reactivateTenant(tenantId: string) {
  return apiFetch<void>(`v1/platform/tenants/${tenantId}/reactivate`, { method: "POST" });
}

export function renameTenant(tenantId: string, displayName: string) {
  return apiFetch<void>(`v1/platform/tenants/${tenantId}`, {
    method: "PATCH",
    body: { display_name: displayName },
  });
}

export function startImpersonation(tenantId: string, targetUserId: string, reason: string) {
  return apiFetch<{ access_token: string; token_type: "Bearer"; expires_in: number }>(
    "v1/platform/impersonation/start",
    { method: "POST", body: { tenant_id: tenantId, target_user_id: targetUserId, reason } },
  );
}

export function endImpersonation(impersonationSessionId: string) {
  return apiFetch<void>("v1/platform/impersonation/end", {
    method: "POST",
    body: { impersonation_session_id: impersonationSessionId },
  });
}
