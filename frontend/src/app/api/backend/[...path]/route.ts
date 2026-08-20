/**
 * The BFF proxy -- every authenticated call the browser makes goes through
 * here, never directly to the FastAPI backend. This is what keeps access
 * and refresh tokens out of client-side JavaScript entirely: the browser
 * only ever holds an httpOnly session cookie it cannot read, and this route
 * is the only code that ever sees the raw JWT strings.
 *
 * Four responsibilities, in order:
 *  1. Attach `Authorization: Bearer` from the httpOnly access-token cookie.
 *  2. Forward `X-Tenant-Id` verbatim if the client sent one (not secret --
 *     the backend re-validates real membership regardless, per
 *     docs/07-tenant-isolation-and-rls.md; a client-tampered value is
 *     harmless by the backend's own design, not by anything enforced here).
 *  3. On a 401 from anything other than the login/refresh endpoints
 *     themselves, transparently refresh once using the httpOnly refresh
 *     cookie and retry -- the browser never sees the intermediate 401.
 *  4. For any response whose JSON body carries raw token strings (login,
 *     mfa/verify, refresh, oauth callback, impersonation start), extract
 *     them into httpOnly cookies and strip them from the body before it
 *     reaches the browser.
 */

import { NextResponse, type NextRequest } from "next/server";
import { cookies } from "next/headers";
import { BACKEND_API_URL } from "@/lib/env";
import {
  ACCESS_TOKEN_COOKIE,
  REFRESH_TOKEN_COOKIE,
  buildAuthCookies,
  clearAuthCookieEntries,
} from "@/lib/auth-cookies";

export const runtime = "nodejs";

// Paths whose JSON body may carry a `tokens: {access_token, refresh_token,
// expires_in}` envelope (LoginResponse shape).
const TOKEN_ENVELOPE_PATHS = new Set([
  "v1/auth/login",
  "v1/auth/mfa/verify",
]);
const OAUTH_CALLBACK_PATTERN = /^v1\/auth\/oauth\/[^/]+\/callback$/;

// Paths whose JSON body IS a bare TokenResponse (no `tokens` wrapper).
const BARE_TOKEN_PATHS = new Set(["v1/auth/refresh"]);

// Impersonation start returns {access_token, token_type, expires_in} --
// its own shape again, and it replaces the access token only (no new
// refresh token is issued for an impersonation session).
const IMPERSONATION_START_PATH = "v1/platform/impersonation/start";

const LOGOUT_PATHS = new Set(["v1/auth/logout", "v1/auth/logout-all"]);

// Statuses the fetch spec forbids a body on. Most of this API's mutating
// endpoints answer 204, so this set is on the hot path, not an edge case.
const NULL_BODY_STATUSES = new Set([204, 205, 304]);

// Refreshing itself, or the endpoints that run before any session exists,
// must never trigger the transparent-refresh-and-retry step -- refreshing
// a login attempt makes no sense, and refreshing the refresh call itself
// would recurse.
const NEVER_REFRESH_PATHS = new Set([
  "v1/auth/login",
  "v1/auth/refresh",
  "v1/auth/register",
  "v1/auth/mfa/verify",
  // Logout deliberately included: by the time it can return anything, the
  // refresh token it was given has been revoked. Retrying through
  // `refreshAccessToken` would then present a revoked token, which the
  // backend's family-based reuse detection reads as a stolen-token replay --
  // signing out would trip a security alarm on the way out the door.
  "v1/auth/logout",
]);

interface TokenResponseShape {
  access_token: string;
  refresh_token?: string;
  expires_in: number;
}

interface CookieEntry {
  name: string;
  value: string;
  options: Record<string, unknown>;
}

async function refreshAccessToken(refreshToken: string): Promise<TokenResponseShape | null> {
  const resp = await fetch(`${BACKEND_API_URL}/v1/auth/refresh`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
    cache: "no-store",
  });
  if (!resp.ok) return null;
  return (await resp.json()) as TokenResponseShape;
}

function extractTokens(joinedPath: string, body: unknown): TokenResponseShape | null {
  if (TOKEN_ENVELOPE_PATHS.has(joinedPath) || OAUTH_CALLBACK_PATTERN.test(joinedPath)) {
    const envelope = body as { tokens?: TokenResponseShape } | null;
    return envelope?.tokens ?? null;
  }
  if (BARE_TOKEN_PATHS.has(joinedPath)) {
    return body as TokenResponseShape;
  }
  if (joinedPath === IMPERSONATION_START_PATH) {
    // No refresh_token in this shape -- only the access token changes.
    return body as TokenResponseShape;
  }
  return null;
}

/** Removes the raw token fields from a response body before it reaches the
 * browser, leaving everything else (status, mfa_challenge_id, ...) intact. */
function stripTokens(joinedPath: string, body: unknown): unknown {
  if (BARE_TOKEN_PATHS.has(joinedPath) || joinedPath === IMPERSONATION_START_PATH) {
    return { status: "success" };
  }
  if (TOKEN_ENVELOPE_PATHS.has(joinedPath) || OAUTH_CALLBACK_PATTERN.test(joinedPath)) {
    const envelope = body as Record<string, unknown>;
    return { ...envelope, tokens: envelope.tokens ? { status: "issued" } : null };
  }
  return body;
}

async function forward(
  request: NextRequest,
  joinedPath: string,
  accessToken: string | undefined,
  body: ArrayBuffer | string | undefined,
): Promise<Response> {
  const url = new URL(request.url);
  const target = `${BACKEND_API_URL}/${joinedPath}${url.search}`;

  const headers = new Headers();
  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);
  // Header first, then the path. `EventSource` cannot set request headers at
  // all, so an SSE subscription to `/v1/tenants/{id}/...` would otherwise be
  // rejected by the backend's tenant resolver for a tenant plainly named in
  // the URL it is calling.
  //
  // Deriving it from the path adds no trust: the backend re-validates whatever
  // it is given against real membership rows before any query runs, so this
  // only saves the client from restating a value the URL already carries.
  const tenantId =
    request.headers.get("x-tenant-id") ??
    joinedPath.match(/^v1\/tenants\/([0-9a-f-]{36})\//i)?.[1];
  if (tenantId) headers.set("x-tenant-id", tenantId);
  const correlationId = request.headers.get("x-correlation-id");
  if (correlationId) headers.set("x-correlation-id", correlationId);
  // Forwarded through, not left to `fetch`'s own default. This proxy runs
  // server-side in Node, so an un-set User-Agent silently becomes Node's own
  // string rather than the caller's browser -- harmless for auth (nothing
  // here decides access on it) but it breaks any diagnostic that logs it,
  // e.g. `push_subscriptions.user_agent`, which exists specifically to show
  // an operator which browser a dead subscription belonged to.
  const userAgent = request.headers.get("user-agent");
  if (userAgent) headers.set("user-agent", userAgent);
  if (accessToken) headers.set("authorization", `Bearer ${accessToken}`);

  return fetch(target, {
    method: request.method,
    headers,
    body,
    cache: "no-store",
  });
}

/**
 * `POST /v1/auth/logout` revokes a *specific* refresh-token family, so the
 * backend needs the raw token in the request body -- but the browser has
 * never had it (that's the whole point of this proxy), and the client sends
 * `{"refresh_token": ""}` as a placeholder. Substituting the real value from
 * the httpOnly cookie here is what makes signing out actually revoke the
 * session server-side.
 *
 * Without this, logout cleared the browser's cookies and looked like it
 * worked, while the refresh token stayed valid in the database until it
 * expired on its own -- anyone holding a copy could still mint fresh access
 * tokens from an account the user believed they had signed out of.
 */
function substituteRefreshToken(body: string | undefined, refreshToken: string | undefined): string {
  if (!refreshToken) return body ?? "{}";
  try {
    const parsed = body ? JSON.parse(body) : {};
    return JSON.stringify({ ...parsed, refresh_token: refreshToken });
  } catch {
    return JSON.stringify({ refresh_token: refreshToken });
  }
}

async function handle(request: NextRequest, pathSegments: string[]): Promise<NextResponse> {
  const joinedPath = pathSegments.join("/");
  const cookieStore = await cookies();
  const accessToken = cookieStore.get(ACCESS_TOKEN_COOKIE)?.value;
  const sessionRefreshToken = cookieStore.get(REFRESH_TOKEN_COOKIE)?.value;

  // Read the request body once, **as bytes** -- a NextRequest body is a stream
  // and can only be consumed a single time, but the 401-refresh path below has
  // to replay the same request.
  //
  // `request.text()` here was silently destroying every binary upload. It
  // decodes the body as UTF-8 with `errors="replace"`, so each byte that is
  // not valid UTF-8 became U+FFFD and was then re-encoded as three bytes on
  // the way upstream. Multipart boundaries and headers are ASCII, so the
  // request still looked perfectly well-formed and the file still arrived,
  // saved and processed -- it was the *contents* that were gone. A 792 KB PDF
  // was stored as 1.43 MB of which 95% of the non-ASCII bytes were
  // replacement characters, its pages rendering blank, which reads exactly
  // like an unreadable scan rather than like a bug in the proxy.
  //
  // Text bodies are unaffected either way; bytes are correct for both.
  const hasBody = !["GET", "HEAD", "DELETE"].includes(request.method);
  let outboundBody: ArrayBuffer | string | undefined = hasBody
    ? await request.arrayBuffer()
    : undefined;
  if (joinedPath === "v1/auth/logout") {
    // The one path that has to look inside the body. It is always small JSON,
    // so decoding it is safe and deliberate rather than incidental.
    outboundBody = substituteRefreshToken(
      outboundBody === undefined ? undefined : new TextDecoder().decode(outboundBody),
      sessionRefreshToken,
    );
  }

  let upstream = await forward(request, joinedPath, accessToken, outboundBody);
  const cookiesToApply: CookieEntry[] = [];

  if (upstream.status === 401 && !NEVER_REFRESH_PATHS.has(joinedPath)) {
    const refreshToken = sessionRefreshToken;
    if (refreshToken) {
      const refreshed = await refreshAccessToken(refreshToken);
      if (refreshed) {
        cookiesToApply.push(
          ...buildAuthCookies({
            accessToken: refreshed.access_token,
            refreshToken: refreshed.refresh_token,
            expiresInSeconds: refreshed.expires_in,
          }),
        );
        upstream = await forward(request, joinedPath, refreshed.access_token, outboundBody);
      } else {
        // Refresh itself failed (expired/reused/revoked) -- the session is
        // over regardless of what the original request wanted.
        cookiesToApply.push(...clearAuthCookieEntries());
      }
    }
  }

  const contentType = upstream.headers.get("content-type") ?? "";

  // Server-Sent Events must be piped through, never buffered. Everything below
  // this point calls `upstream.text()`, which waits for the response to
  // *complete* -- for a streamed answer that means holding every token until
  // generation finishes and then delivering them at once, which is exactly the
  // waiting that streaming exists to remove. The body is passed as a stream
  // instead, so the first token reaches the browser as soon as the model
  // produces it.
  //
  // The auth work above has already happened, so a streamed response still
  // gets the bearer token and the 401-refresh-and-retry. What it cannot get is
  // a *mid-stream* refresh: once the first byte is sent the status is fixed.
  // That is acceptable here -- an access token valid at the first byte stays
  // valid for the seconds an answer takes.
  if (contentType.includes("text/event-stream") && upstream.body) {
    const streamed = new NextResponse(upstream.body, {
      status: upstream.status,
      headers: {
        "content-type": contentType,
        // Mirrors the backend: without it a proxy or CDN buffers the whole
        // response and streaming silently degrades to waiting.
        "x-accel-buffering": "no",
        "cache-control": "no-store",
      },
    });
    for (const entry of cookiesToApply) {
      streamed.cookies.set(entry.name, entry.value, entry.options);
    }
    return streamed;
  }

  const rawBody = await upstream.text();
  const upstreamContentType = contentType;
  const isJson = upstreamContentType.includes("application/json");
  let parsedBody: unknown = rawBody;
  if (isJson && rawBody) {
    try {
      parsedBody = JSON.parse(rawBody);
    } catch {
      parsedBody = rawBody;
    }
  }

  if (isJson && upstream.ok) {
    const tokens = extractTokens(joinedPath, parsedBody);
    if (tokens) {
      cookiesToApply.push(
        ...buildAuthCookies({
          accessToken: tokens.access_token,
          refreshToken: tokens.refresh_token,
          expiresInSeconds: tokens.expires_in,
        }),
      );
      parsedBody = stripTokens(joinedPath, parsedBody);
    }
  }

  if (LOGOUT_PATHS.has(joinedPath)) {
    cookiesToApply.push(...clearAuthCookieEntries());
  }

  // 204/205/304 are "null body status" codes: the Response constructor throws
  // a TypeError if given any body at all, including the empty string that
  // `upstream.text()` yields for them. Passing one through unguarded turned
  // every 204-returning action in this console -- suspend a tenant, assign a
  // role, revoke a membership -- into a 500 from this proxy, even though the
  // backend had already carried the action out successfully. The status has
  // to be checked before the body is attached, not after.
  const isBodyless = NULL_BODY_STATUSES.has(upstream.status);
  const response = isBodyless
    ? new NextResponse(null, { status: upstream.status })
    : isJson
      ? NextResponse.json(parsedBody, { status: upstream.status })
      : new NextResponse(rawBody, {
          status: upstream.status,
          headers: upstreamContentType ? { "content-type": upstreamContentType } : undefined,
        });

  for (const entry of cookiesToApply) {
    response.cookies.set(entry.name, entry.value, entry.options);
  }
  const retryAfter = upstream.headers.get("retry-after");
  if (retryAfter) response.headers.set("retry-after", retryAfter);

  return response;
}

type RouteContext = { params: Promise<{ path: string[] }> };

export async function GET(request: NextRequest, ctx: RouteContext) {
  return handle(request, (await ctx.params).path);
}
export async function POST(request: NextRequest, ctx: RouteContext) {
  return handle(request, (await ctx.params).path);
}
export async function PUT(request: NextRequest, ctx: RouteContext) {
  return handle(request, (await ctx.params).path);
}
export async function PATCH(request: NextRequest, ctx: RouteContext) {
  return handle(request, (await ctx.params).path);
}
export async function DELETE(request: NextRequest, ctx: RouteContext) {
  return handle(request, (await ctx.params).path);
}
