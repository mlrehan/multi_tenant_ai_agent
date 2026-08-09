/**
 * Lets client components ask "is there a session, and is it impersonated?"
 * without ever reading the httpOnly cookies directly (they can't -- that's
 * the point). This is an *optimistic* signal only: it reports cookie
 * presence and JWT payload contents without verifying the signature (see
 * lib/jwt-decode.ts). The real enforcement happens per-request in the
 * `/api/backend/*` proxy, which calls the actual backend and transparently
 * refreshes or fails -- this route exists purely to drive the impersonation
 * banner, not to gate anything.
 */

import { NextResponse } from "next/server";
import { cookies } from "next/headers";
import { ACCESS_TOKEN_COOKIE, REFRESH_TOKEN_COOKIE } from "@/lib/auth-cookies";
import { decodeJwtPayloadUnsafe } from "@/lib/jwt-decode";

export const runtime = "nodejs";

export async function GET() {
  const cookieStore = await cookies();
  const accessToken = cookieStore.get(ACCESS_TOKEN_COOKIE)?.value;
  const hasRefresh = Boolean(cookieStore.get(REFRESH_TOKEN_COOKIE)?.value);

  if (!accessToken) {
    return NextResponse.json({ authenticated: hasRefresh, impersonating: false });
  }

  const claims = decodeJwtPayloadUnsafe(accessToken);
  return NextResponse.json({
    authenticated: true,
    impersonating: Boolean(claims?.act),
    impersonationSessionId: claims?.act?.imp_sid ?? null,
  });
}
