import { apiFetch } from "@/lib/api-client";
import type { AccountProfile, LoginResponse } from "@/lib/types";

export interface SessionInfo {
  authenticated: boolean;
  impersonating: boolean;
  impersonationSessionId?: string | null;
}

/** Reads the same-origin session marker -- not the backend -- see
 * app/api/session/route.ts for why. */
export async function fetchSession(): Promise<SessionInfo> {
  const res = await fetch("/api/session", { credentials: "same-origin" });
  return res.json();
}

export function register(email: string, password: string) {
  return apiFetch<{ detail: string }>("v1/auth/register", {
    method: "POST",
    body: { email, password },
  });
}

export function verifyEmail(token: string) {
  return apiFetch<{ detail: string }>(`v1/auth/verify-email?token=${encodeURIComponent(token)}`);
}

export function login(email: string, password: string) {
  return apiFetch<LoginResponse>("v1/auth/login", { method: "POST", body: { email, password } });
}

export function verifyMfa(challengeId: string, code: string) {
  return apiFetch<LoginResponse>("v1/auth/mfa/verify", {
    method: "POST",
    body: { challenge_id: challengeId, code },
  });
}

export function logout() {
  // The backend's LogoutRequest wants the raw refresh token, which is
  // httpOnly and unreachable from here by design. The placeholder below is
  // replaced with the real token by the proxy before the request leaves the
  // server (see `substituteRefreshToken` in
  // app/api/backend/[...path]/route.ts) -- that substitution is what makes
  // the session actually get revoked rather than merely forgotten locally.
  return apiFetch<void>("v1/auth/logout", { method: "POST", body: { refresh_token: "" } });
}

export function logoutAll() {
  return apiFetch<void>("v1/auth/logout-all", { method: "POST" });
}

export function fetchMyAccount() {
  return apiFetch<AccountProfile>("v1/auth/me");
}

export function changePassword(currentPassword: string, newPassword: string) {
  return apiFetch<{ detail: string }>("v1/auth/password/change", {
    method: "POST",
    body: { current_password: currentPassword, new_password: newPassword },
  });
}

export function requestPasswordReset(email: string) {
  return apiFetch<{ detail: string }>("v1/auth/password-reset/request", {
    method: "POST",
    body: { email },
  });
}

export function confirmPasswordReset(token: string, newPassword: string) {
  return apiFetch<{ detail: string }>("v1/auth/password-reset/confirm", {
    method: "POST",
    body: { token, new_password: newPassword },
  });
}

export function startTotpEnrollment() {
  return apiFetch<{ mfa_method_id: string; secret: string; provisioning_uri: string }>(
    "v1/auth/mfa/totp/start",
    { method: "POST" },
  );
}

export function confirmTotpEnrollment(mfaMethodId: string, code: string) {
  return apiFetch<{ detail: string }>("v1/auth/mfa/totp/confirm", {
    method: "POST",
    body: { mfa_method_id: mfaMethodId, code },
  });
}

export function startOAuth(provider: "google" | "facebook") {
  return apiFetch<{ authorization_url: string }>(`v1/auth/oauth/${provider}/start`);
}

export function completeOAuth(provider: string, code: string, state: string) {
  return apiFetch<LoginResponse>(
    `v1/auth/oauth/${provider}/callback?code=${encodeURIComponent(code)}&state=${encodeURIComponent(state)}`,
  );
}
