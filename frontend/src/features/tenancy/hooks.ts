"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "@/features/tenancy/api";

export function useMyMemberships() {
  return useQuery({ queryKey: ["memberships", "me"], queryFn: api.listMyMemberships });
}

export function useTenantMembers(tenantId: string | null) {
  return useQuery({
    queryKey: ["tenant-members", tenantId],
    queryFn: () => api.listTenantMembers(tenantId!),
    enabled: Boolean(tenantId),
  });
}

export function useMembershipRoles(tenantId: string | null, membershipId: string | null) {
  return useQuery({
    queryKey: ["membership-roles", tenantId, membershipId],
    queryFn: () => api.listMembershipRoles(tenantId!, membershipId!),
    enabled: Boolean(tenantId && membershipId),
  });
}

export function useInviteMember(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ email, roleCodes }: { email: string; roleCodes: string[] }) =>
      api.inviteMember(tenantId, email, roleCodes),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["tenant-members", tenantId] }),
  });
}

export function useAcceptInvitation(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (token: string) => api.acceptInvitation(tenantId, token),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["memberships", "me"] }),
  });
}

export function useMembershipLifecycle(tenantId: string) {
  const queryClient = useQueryClient();
  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["tenant-members", tenantId] });

  const suspend = useMutation({
    mutationFn: ({ membershipId, reason }: { membershipId: string; reason: string }) =>
      api.suspendMembership(tenantId, membershipId, reason),
    onSuccess: invalidate,
  });
  const reactivate = useMutation({
    mutationFn: (membershipId: string) => api.reactivateMembership(tenantId, membershipId),
    onSuccess: invalidate,
  });
  const revoke = useMutation({
    mutationFn: ({ membershipId, reason }: { membershipId: string; reason: string }) =>
      api.revokeMembership(tenantId, membershipId, reason),
    onSuccess: invalidate,
  });
  const restore = useMutation({
    mutationFn: (membershipId: string) => api.restoreMembership(tenantId, membershipId),
    onSuccess: invalidate,
  });

  return { suspend, reactivate, revoke, restore };
}

export function useAddMember(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { userId: string; roleCodes: string[]; jobTitle: string | null }) =>
      api.addMember(tenantId, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["tenant-members", tenantId] }),
  });
}

export function useUpdateMembership(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ membershipId, jobTitle }: { membershipId: string; jobTitle: string | null }) =>
      api.updateMembership(tenantId, membershipId, jobTitle),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["tenant-members", tenantId] }),
  });
}
