/**
 * Decodes a JWT payload WITHOUT verifying its signature. Safe only for
 * cosmetic display purposes (the impersonation banner needs to know "is
 * this an impersonation session" to render itself) -- every real
 * authorization decision is re-made by the backend, which does verify the
 * signature, on every actual request. Never use this output to make an
 * access-control decision in this app.
 */

interface DecodedAccessTokenClaims {
  sub?: string;
  session_id?: string;
  amr?: string[];
  act?: { sub: string; imp_sid: string };
}

export function decodeJwtPayloadUnsafe(token: string): DecodedAccessTokenClaims | null {
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  try {
    const base64url = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const padded = base64url.padEnd(base64url.length + ((4 - (base64url.length % 4)) % 4), "=");
    const json = Buffer.from(padded, "base64").toString("utf-8");
    return JSON.parse(json) as DecodedAccessTokenClaims;
  } catch {
    return null;
  }
}
