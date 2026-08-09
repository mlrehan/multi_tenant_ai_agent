"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "@/features/rbac/api";
import type { OverrideEffect } from "@/lib/types";

// ---- Tenant scope ----

export function useTenantEffectivePermissions(tenantId: string | null) {
  return useQuery({
    queryKey: ["tenant-effective-permissions", tenantId],
    queryFn: () => api.getMyTenantEffectivePermissions(tenantId!),
    enabled: Boolean(tenantId),
    staleTime: 30_000,
  });
}

/** The permission-gating primitive every tenant-scoped screen/action uses.
 * Returns `undefined` while loading -- callers should treat that as "don't
 * know yet" (hide, don't show) rather than either extreme. */
export function useHasTenantPermission(tenantId: string | null, code: string): boolean | undefined {
  const { data, isLoading } = useTenantEffectivePermissions(tenantId);
  if (isLoading || !data) return undefined;
  return data.permissions.includes(code);
}

export function useTenantRoles(tenantId: string | null) {
  return useQuery({
    queryKey: ["tenant-roles", tenantId],
    queryFn: () => api.listTenantRoles(tenantId!),
    enabled: Boolean(tenantId),
  });
}

export function useTenantPermissionCatalog(tenantId: string | null) {
  return useQuery({
    queryKey: ["tenant-permission-catalog", tenantId],
    queryFn: () => api.listTenantPermissions(tenantId!),
    enabled: Boolean(tenantId),
  });
}

export function useCreateCustomRole(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      code: string;
      name: string;
      description: string | null;
      rank: number;
      permissionCodes: string[];
    }) => api.createCustomRole(tenantId, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["tenant-roles", tenantId] }),
  });
}

export function useCreateRoleHierarchyEdge(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ parent, child }: { parent: string; child: string }) =>
      api.createRoleHierarchyEdge(tenantId, parent, child),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["tenant-roles", tenantId] }),
  });
}

export function useMembershipRoleAssignment(tenantId: string) {
  const queryClient = useQueryClient();
  const invalidate = (membershipId: string) => {
    queryClient.invalidateQueries({ queryKey: ["membership-roles", tenantId, membershipId] });
  };
  const assign = useMutation({
    mutationFn: ({ membershipId, roleCode }: { membershipId: string; roleCode: string }) =>
      api.assignMembershipRole(tenantId, membershipId, roleCode),
    onSuccess: (_, vars) => invalidate(vars.membershipId),
  });
  const revoke = useMutation({
    mutationFn: ({ membershipId, roleCode }: { membershipId: string; roleCode: string }) =>
      api.revokeMembershipRole(tenantId, membershipId, roleCode),
    onSuccess: (_, vars) => invalidate(vars.membershipId),
  });
  return { assign, revoke };
}

export function useCreateOverride(tenantId: string) {
  return useMutation({
    mutationFn: (body: {
      targetMembershipId: string;
      permissionCode: string;
      effect: OverrideEffect;
      reason: string;
      expiresAt: string | null;
    }) => api.createOverride(tenantId, body),
  });
}

export function useRevokeOverride(tenantId: string) {
  return useMutation({
    mutationFn: (overrideId: string) => api.revokeOverride(tenantId, overrideId),
  });
}

export function useEditRolePermissions(tenantId: string) {
  const queryClient = useQueryClient();
  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["tenant-roles", tenantId] });
    queryClient.invalidateQueries({ queryKey: ["tenant-role-permissions", tenantId] });
  };
  const add = useMutation({
    mutationFn: ({ roleCode, permissionCode }: { roleCode: string; permissionCode: string }) =>
      api.addPermissionToRole(tenantId, roleCode, permissionCode),
    onSuccess: invalidate,
  });
  const remove = useMutation({
    mutationFn: ({ roleCode, permissionCode }: { roleCode: string; permissionCode: string }) =>
      api.removePermissionFromRole(tenantId, roleCode, permissionCode),
    onSuccess: invalidate,
  });
  return { add, remove };
}

export function useTenantRolePermissions(tenantId: string | null) {
  return useQuery({
    queryKey: ["tenant-role-permissions", tenantId],
    queryFn: () => api.getTenantRolePermissions(tenantId!),
    enabled: Boolean(tenantId),
  });
}

// ---- Platform scope ----

export function usePlatformEffectivePermissions() {
  return useQuery({
    queryKey: ["platform-effective-permissions"],
    queryFn: api.getMyPlatformEffectivePermissions,
    staleTime: 30_000,
  });
}

export function useHasPlatformPermission(code: string): boolean | undefined {
  const { data, isLoading } = usePlatformEffectivePermissions();
  if (isLoading || !data) return undefined;
  return data.permissions.includes(code);
}

export function usePlatformRoles() {
  return useQuery({ queryKey: ["platform-roles"], queryFn: api.listPlatformRoles });
}

export function usePlatformPermissionCatalog() {
  return useQuery({ queryKey: ["platform-permission-catalog"], queryFn: api.listPlatformPermissions });
}

export function useGrantPlatformRole() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ targetUserId, roleCode }: { targetUserId: string; roleCode: string }) =>
      api.grantPlatformRole(targetUserId, roleCode),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["platform-effective-permissions"] }),
  });
}

export function useRevokePlatformRole() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ targetUserId, roleCode }: { targetUserId: string; roleCode: string }) =>
      api.revokePlatformRole(targetUserId, roleCode),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["platform-effective-permissions"] }),
  });
}

export function useCreatePlatformRole() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      code: string;
      name: string;
      description: string | null;
      rank: number;
      permissionCodes: string[];
    }) => api.createPlatformRole(body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["platform-roles"] }),
  });
}

export function useEditPlatformRolePermissions() {
  const queryClient = useQueryClient();
  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["platform-roles"] });
    queryClient.invalidateQueries({ queryKey: ["platform-role-permissions"] });
  };
  const add = useMutation({
    mutationFn: ({ roleCode, permissionCode }: { roleCode: string; permissionCode: string }) =>
      api.addPermissionToPlatformRole(roleCode, permissionCode),
    onSuccess: invalidate,
  });
  const remove = useMutation({
    mutationFn: ({ roleCode, permissionCode }: { roleCode: string; permissionCode: string }) =>
      api.removePermissionFromPlatformRole(roleCode, permissionCode),
    onSuccess: invalidate,
  });
  return { add, remove };
}
