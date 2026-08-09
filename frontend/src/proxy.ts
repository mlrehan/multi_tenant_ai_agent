/**
 * Route-level redirect only -- an *optimistic* check, per Next's own
 * guidance ("should not be used as a full session management or
 * authorization solution"). The actual security boundary is the backend:
 * every real request goes through `/api/backend/*`, which attaches the
 * bearer token server-side and lets FastAPI's own permission checks decide.
 * This file only prevents the flash of a protected page before that
 * request would fail, and bounces an already-authenticated visitor away
 * from the login screen.
 *
 * Named `proxy.ts`, not `middleware.ts` -- Next.js 16 renamed the
 * convention (same file-based mechanism, see AGENTS.md in this project).
 */

import { NextResponse, type NextRequest } from "next/server";
import { ACCESS_TOKEN_COOKIE, REFRESH_TOKEN_COOKIE } from "@/lib/auth-cookies";

const PUBLIC_PATHS = ["/login", "/register", "/verify-email", "/forgot-password", "/reset-password", "/oauth"];

export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const hasSession = Boolean(
    request.cookies.get(ACCESS_TOKEN_COOKIE)?.value || request.cookies.get(REFRESH_TOKEN_COOKIE)?.value,
  );
  const isPublicPath = PUBLIC_PATHS.some((p) => pathname === p || pathname.startsWith(`${p}/`));

  if (!hasSession && !isPublicPath) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    url.searchParams.set("next", pathname);
    return NextResponse.redirect(url);
  }

  if (hasSession && (pathname === "/login" || pathname === "/register")) {
    // "/" and not "/select-tenant": the root route decides where this
    // particular person belongs (platform overview, their single tenant, or
    // the picker) from their effective permissions, which aren't visible here.
    const url = request.nextUrl.clone();
    url.pathname = "/";
    url.search = "";
    return NextResponse.redirect(url);
  }

  return NextResponse.next();
}

export const config = {
  // Excludes Next's internal paths, the API/proxy routes (which do their
  // own auth, not a redirect), and anything that looks like a static file
  // (has a dot in the last path segment -- .svg, .png, .ico, ...). Without
  // that last exclusion, public/ assets like /next.svg get run through the
  // same redirect-to-login logic as a real page (caught during manual
  // testing: a request for /next.svg came back as a redirect to
  // /login?next=%2Fnext.svg instead of the image).
  matcher: ["/((?!_next/static|_next/image|favicon.ico|api/|.*\\.[\\w]+$).*)"],
};
