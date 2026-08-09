// Server-only cookie helpers shared by the BFF proxy and any dedicated auth
// route handlers. Access and refresh tokens live ONLY here -- httpOnly, so
// no client-side script (including an XSS payload) can ever read them. This
// is the single place token lifetime/flags are decided; nothing else should
// construct these cookies by hand.

import { IS_PRODUCTION } from "@/lib/env";

export const ACCESS_TOKEN_COOKIE = "iam_at";
export const REFRESH_TOKEN_COOKIE = "iam_rt";

// Refresh tokens are long-lived (30 days server-side); the cookie mirrors
// that so the browser doesn't drop it before the backend would anyway.
const REFRESH_TOKEN_MAX_AGE_SECONDS = 60 * 60 * 24 * 30;

const baseCookieOptions = {
  httpOnly: true,
  secure: IS_PRODUCTION,
  sameSite: "lax" as const,
  path: "/",
};

export interface TokenPairCookies {
  accessToken: string;
  refreshToken?: string;
  expiresInSeconds: number;
}

/** Returns the `Set-Cookie`-shaped entries to apply via a NextResponse's
 * cookie jar. Kept as plain data (not tied to a specific `cookies()` API)
 * so both Route Handlers and the BFF proxy can apply them identically. */
export function buildAuthCookies(tokens: TokenPairCookies) {
  const entries = [
    {
      name: ACCESS_TOKEN_COOKIE,
      value: tokens.accessToken,
      options: { ...baseCookieOptions, maxAge: tokens.expiresInSeconds },
    },
  ];
  if (tokens.refreshToken) {
    entries.push({
      name: REFRESH_TOKEN_COOKIE,
      value: tokens.refreshToken,
      options: { ...baseCookieOptions, maxAge: REFRESH_TOKEN_MAX_AGE_SECONDS },
    });
  }
  return entries;
}

export function clearAuthCookieEntries() {
  return [
    { name: ACCESS_TOKEN_COOKIE, value: "", options: { ...baseCookieOptions, maxAge: 0 } },
    { name: REFRESH_TOKEN_COOKIE, value: "", options: { ...baseCookieOptions, maxAge: 0 } },
  ];
}
