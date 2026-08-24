"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "@/features/platform/api";
import { useImpersonationStore } from "@/stores/impersonation-store";
import { sessionQueryKey } from "@/features/auth/hooks";

export function useTenants() {
  return useQuery({ queryKey: ["platform-tenants"], queryFn: api.listTenants });
}

export function useCreateTenant() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      slug,
      displayName,
      ownerUserId,
    }: {
      slug: string;
      displayName: string;
      ownerUserId: string;
    }) => api.createTenant(slug, displayName, ownerUserId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["platform-tenants"] }),
  });
}

export function useSuspendTenant() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ tenantId, reason }: { tenantId: string; reason: string }) =>
      api.suspendTenant(tenantId, reason),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["platform-tenants"] }),
  });
}

export function useReactivateTenant() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (tenantId: string) => api.reactivateTenant(tenantId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["platform-tenants"] }),
  });
}

export function useRenameTenant() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ tenantId, displayName }: { tenantId: string; displayName: string }) =>
      api.renameTenant(tenantId, displayName),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["platform-tenants"] }),
  });
}

export const platformUsersKey = ["platform-users"] as const;

export function usePlatformUsers(params: { search?: string; limit?: number; offset?: number }) {
  return useQuery({
    queryKey: [...platformUsersKey, params],
    queryFn: () => api.listUsers(params),
    // Keeps the previous page on screen while the next one loads, so typing
    // in the search box doesn't flash the table to a skeleton on every
    // keystroke.
    placeholderData: (previous) => previous,
  });
}

export function usePlatformUser(userId: string | null) {
  return useQuery({
    queryKey: [...platformUsersKey, "detail", userId],
    queryFn: () => api.getUser(userId as string),
    enabled: userId != null,
  });
}

export function useSetUserStatus() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      userId,
      suspend,
      reason,
    }: {
      userId: string;
      suspend: boolean;
      reason?: string | null;
    }) => (suspend ? api.suspendUser(userId, reason ?? null) : api.reactivateUser(userId)),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: platformUsersKey }),
  });
}

export function useCreateUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ email, password }: { email: string; password: string }) =>
      api.createUser(email, password),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: platformUsersKey }),
  });
}

export function useUpdateUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, email }: { userId: string; email: string }) =>
      api.updateUser(userId, email),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: platformUsersKey }),
  });
}

export function useDeleteUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (userId: string) => api.deleteUser(userId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: platformUsersKey }),
  });
}

export function usePlatformRolePermissions() {
  return useQuery({
    queryKey: ["platform-role-permissions"],
    queryFn: api.listPlatformRolePermissions,
  });
}

export function useStartImpersonation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      tenantId,
      targetUserId,
      reason,
    }: {
      tenantId: string;
      targetUserId: string;
      reason: string;
    }) => api.startImpersonation(tenantId, targetUserId, reason),
    onSuccess: (result, vars) => {
      // The response never carries the raw access token to client JS (the
      // BFF proxy strips it and sets the httpOnly cookie itself) -- we only
      // get expires_in back. The session id used to end this session later
      // comes from useSession() (decoded server-side from the new cookie),
      // not from here -- see stores/impersonation-store.ts.
      useImpersonationStore.getState().start({
        tenantId: vars.tenantId,
        targetUserId: vars.targetUserId,
        reason: vars.reason,
        expiresAt: new Date(Date.now() + result.expires_in * 1000).toISOString(),
      });
      queryClient.invalidateQueries({ queryKey: sessionQueryKey });
      queryClient.invalidateQueries({ queryKey: ["tenant-effective-permissions"] });
      queryClient.invalidateQueries({ queryKey: ["platform-effective-permissions"] });
    },
  });
}

export function useEndImpersonation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (impersonationSessionId: string) => api.endImpersonation(impersonationSessionId),
    onSuccess: () => {
      useImpersonationStore.getState().end();
      queryClient.invalidateQueries({ queryKey: sessionQueryKey });
      queryClient.invalidateQueries({ queryKey: ["tenant-effective-permissions"] });
      queryClient.invalidateQueries({ queryKey: ["platform-effective-permissions"] });
    },
  });
}

/** The operator dashboard.
 *
 *  Refetched on an interval because it is a live spend view someone leaves
 *  open — a number that was true when the tab was opened this morning is worse
 *  than no number, since it reads as current. 60s rather than seconds: the
 *  request fans out across every tenant (see `platform_overview.py`), and
 *  token budgets do not move fast enough to justify hammering it. */
export function usePlatformOverview() {
  return useQuery({
    queryKey: ["platform-overview"],
    queryFn: () => api.getPlatformOverview(),
    refetchInterval: 60_000,
  });
}

const modelConfigurationsKey = ["platform-model-configurations"];

export function usePlatformModelConfigurations() {
  return useQuery({
    queryKey: modelConfigurationsKey,
    queryFn: () => api.listPlatformModelConfigurations(),
  });
}

/** Every mutation on this screen changes the same list, and several of them
 *  change what tenants can see too, so they share one invalidation. */
function useModelConfigurationMutation<TVars>(
  mutationFn: (vars: TVars) => Promise<unknown>,
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: modelConfigurationsKey });
      // A grant or revoke changes what a tenant admin is offered.
      queryClient.invalidateQueries({ queryKey: ["model-configurations"] });
    },
  });
}

export function useCreateModelConfiguration() {
  return useModelConfigurationMutation(
    (body: { model_name: string; token_budget_per_month?: number | null }) =>
      api.createModelConfiguration(body),
  );
}

export function useUpdateModelConfiguration() {
  return useModelConfigurationMutation(
    (vars: {
      id: string;
      model_name: string;
      token_budget_per_month?: number | null;
    }) =>
      api.updateModelConfiguration(vars.id, {
        model_name: vars.model_name,
        token_budget_per_month: vars.token_budget_per_month,
      }),
  );
}

export function useArchiveModelConfiguration() {
  return useModelConfigurationMutation((id: string) => api.archiveModelConfiguration(id));
}

export function useRestoreModelConfiguration() {
  return useModelConfigurationMutation((id: string) => api.restoreModelConfiguration(id));
}

export function useGrantModelConfiguration() {
  return useModelConfigurationMutation((vars: { id: string; tenantId: string }) =>
    api.grantModelConfiguration(vars.id, vars.tenantId),
  );
}

export function useRevokeModelConfiguration() {
  return useModelConfigurationMutation((vars: { id: string; tenantId: string }) =>
    api.revokeModelConfiguration(vars.id, vars.tenantId),
  );
}
