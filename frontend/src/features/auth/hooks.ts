"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import * as api from "@/features/auth/api";
import { useTenantStore } from "@/stores/tenant-store";
import { useImpersonationStore } from "@/stores/impersonation-store";

export const sessionQueryKey = ["session"] as const;

export function useSession() {
  return useQuery({
    queryKey: sessionQueryKey,
    queryFn: api.fetchSession,
    staleTime: 60_000,
  });
}

export function useLogin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ email, password }: { email: string; password: string }) =>
      api.login(email, password),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: sessionQueryKey }),
  });
}

export function useVerifyMfa() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ challengeId, code }: { challengeId: string; code: string }) =>
      api.verifyMfa(challengeId, code),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: sessionQueryKey }),
  });
}

export function useRegister() {
  return useMutation({
    mutationFn: ({ email, password }: { email: string; password: string }) =>
      api.register(email, password),
  });
}

export function useLogout() {
  const queryClient = useQueryClient();
  const router = useRouter();
  return useMutation({
    mutationFn: api.logout,
    onSettled: () => {
      // Settled, not onSuccess: the proxy clears cookies on this path
      // regardless of the upstream backend response (see the LOGOUT_PATHS
      // handling in app/api/backend/[...path]/route.ts) -- so the client
      // should always drop local state and leave, even on a network error.
      useTenantStore.getState().setCurrentTenant(null);
      useImpersonationStore.getState().end();
      queryClient.clear();
      router.push("/login");
    },
  });
}

export function useLogoutAll() {
  return useLogout(); // same client-side effect; only the backend call differs
}

export const accountQueryKey = ["account"] as const;

export function useMyAccount() {
  return useQuery({ queryKey: accountQueryKey, queryFn: api.fetchMyAccount });
}

export function useChangePassword() {
  const queryClient = useQueryClient();
  const router = useRouter();
  return useMutation({
    mutationFn: ({ currentPassword, newPassword }: { currentPassword: string; newPassword: string }) =>
      api.changePassword(currentPassword, newPassword),
    onSuccess: () => {
      // A successful change bumps the security stamp server-side, which kills
      // every session including this one -- the cookies we still hold are
      // already dead. Clear local state and send the user to sign in again
      // rather than letting the next request fail confusingly.
      useTenantStore.getState().setCurrentTenant(null);
      useImpersonationStore.getState().end();
      queryClient.clear();
      router.push("/login?reason=password-changed");
    },
  });
}

export function useRequestPasswordReset() {
  return useMutation({ mutationFn: (email: string) => api.requestPasswordReset(email) });
}

export function useConfirmPasswordReset() {
  return useMutation({
    mutationFn: ({ token, password }: { token: string; password: string }) =>
      api.confirmPasswordReset(token, password),
  });
}

export function useStartTotpEnrollment() {
  return useMutation({ mutationFn: api.startTotpEnrollment });
}

export function useConfirmTotpEnrollment() {
  return useMutation({
    mutationFn: ({ mfaMethodId, code }: { mfaMethodId: string; code: string }) =>
      api.confirmTotpEnrollment(mfaMethodId, code),
  });
}
