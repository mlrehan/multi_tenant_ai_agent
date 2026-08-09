import { apiFetch } from "@/lib/api-client";
import type { MembershipRoleAssignment, TenantMember, TenantMembership } from "@/lib/types";

export function listMyMemberships() {
  return apiFetch<TenantMembership[]>("v1/tenants/me/memberships");
}

export function listTenantMembers(tenantId: string) {
  return apiFetch<TenantMember[]>(`v1/tenants/${tenantId}/memberships`, { tenantId });
}

export function listMembershipRoles(tenantId: string, membershipId: string) {
  return apiFetch<MembershipRoleAssignment[]>(
    `v1/tenants/${tenantId}/memberships/${membershipId}/roles`,
    { tenantId },
  );
}

export function inviteMember(tenantId: string, email: string, roleCodes: string[]) {
  return apiFetch<{ detail: string }>(`v1/tenants/${tenantId}/invitations`, {
    method: "POST",
    tenantId,
    body: { email, role_codes: roleCodes },
  });
}

export function acceptInvitation(tenantId: string, token: string) {
  return apiFetch<{ detail: string }>(`v1/tenants/${tenantId}/invitations/accept`, {
    method: "POST",
    tenantId,
    body: { token },
  });
}

export function suspendMembership(tenantId: string, membershipId: string, reason: string) {
  return apiFetch<void>(`v1/tenants/${tenantId}/memberships/${membershipId}/suspend`, {
    method: "POST",
    tenantId,
    body: { reason },
  });
}

export function reactivateMembership(tenantId: string, membershipId: string) {
  return apiFetch<void>(`v1/tenants/${tenantId}/memberships/${membershipId}/reactivate`, {
    method: "POST",
    tenantId,
  });
}

export function revokeMembership(tenantId: string, membershipId: string, reason: string) {
  return apiFetch<void>(`v1/tenants/${tenantId}/memberships/${membershipId}/revoke`, {
    method: "POST",
    tenantId,
    body: { reason },
  });
}

export function restoreMembership(tenantId: string, membershipId: string) {
  return apiFetch<void>(`v1/tenants/${tenantId}/memberships/${membershipId}/restore`, {
    method: "POST",
    tenantId,
  });
}

/** Adds an already-registered user straight to ACTIVE membership, with no
 * invitation email -- see AddMemberDirectly's docstring for why this exists
 * (this deployment has no working email provider, so `inviteMember` alone
 * left no way to actually complete onboarding). */
export function addMember(
  tenantId: string,
  body: { userId: string; roleCodes: string[]; jobTitle: string | null },
) {
  return apiFetch<{ membership_id: string }>(`v1/tenants/${tenantId}/memberships`, {
    method: "POST",
    tenantId,
    body: { user_id: body.userId, role_codes: body.roleCodes, job_title: body.jobTitle },
  });
}

export function updateMembership(tenantId: string, membershipId: string, jobTitle: string | null) {
  return apiFetch<void>(`v1/tenants/${tenantId}/memberships/${membershipId}`, {
    method: "PATCH",
    tenantId,
    body: { job_title: jobTitle },
  });
}
