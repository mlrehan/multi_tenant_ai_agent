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

import { createHash } from "node:crypto";
import { isIP } from "node:net";
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

/**
 * A reused keep-alive connection the backend has already closed.
 *
 * uvicorn drops an idle connection after 5 s. Node's pool closes its own end
 * sooner -- when its event loop is free to run the timer. When it isn't (a
 * dev-server page compile stalls it for 10-30 s; heavy load does the same in
 * production), the pool hands out a socket the backend closed, and the
 * request dies with "other side closed" before the backend read it. Seen
 * live as a stray 500 on `GET /v1/tenants/me/memberships`.
 */
const STALE_CONNECTION_CODES = new Set(["UND_ERR_SOCKET", "UND_ERR_CLOSED", "ECONNRESET", "EPIPE"]);

function isStaleConnection(err: unknown): boolean {
  const code = (err as { cause?: { code?: unknown } } | null)?.cause?.code;
  return typeof code === "string" && STALE_CONNECTION_CODES.has(code);
}

/**
 * `fetch`, retried once on a stale connection -- but only where repeating
 * the request is harmless. A GET is. A POST that dies this way *probably*
 * never reached the backend, but "probably" is not good enough for sending a
 * message or creating a tenant twice, so writes surface the failure instead.
 */
async function fetchUpstream(target: string, init: RequestInit, retryable: boolean): Promise<Response> {
  try {
    return await fetch(target, init);
  } catch (err) {
    if (!retryable || !isStaleConnection(err)) throw err;
    return fetch(target, init);
  }
}

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

/**
 * The backend answered, but not with a verdict on the session (a 5xx while it
 * restarts, say). Surfaced as a 502 like a network failure, never read as
 * "session over".
 */
class UpstreamUnavailableError extends Error {}

/**
 * The new tokens; `null` only when the backend *refused* the refresh token
 * (expired, rotated, revoked) -- the one answer that really ends a session.
 * Anything else throws, so a restart or a network blip at the moment an
 * access token expires no longer signs the administrator out. Found live: a
 * session whose refresh token was never even presented to the backend had its
 * cookies cleared, because a failed fetch was reported as `null`.
 */
/**
 * The browser's IP, as the reverse proxy in front of this console saw it.
 *
 * Every request this proxy makes reaches the API from *this* process, so
 * without this the API's per-IP rate limit put every signed-in user in one
 * bucket -- a few admins on live dashboards could exhaust it for everyone --
 * and login attempts and audit entries all recorded the console's address.
 *
 * **The last entry, and only a real IP.** The last entry is the one the
 * nearest proxy wrote: with Nginx overwriting the header (DEPLOYMENT.md Step
 * 8c) it is the only entry; with a proxy that appends, it is still the one a
 * client cannot forge, while the first could be anything the client sent.
 * Anything that doesn't parse as an IP is dropped rather than passed on.
 *
 * With no proxy in front (local development), there is no header and nothing
 * is forwarded -- the API then sees this process, as before.
 */
function clientIp(request: NextRequest): string | null {
  const header = request.headers.get("x-forwarded-for");
  if (!header) return null;
  let last = header.split(",").pop()?.trim() ?? "";
  // An IPv4 client on a dual-stack socket arrives as "::ffff:203.0.113.7";
  // unwrapped, so one client does not get two rate-limit buckets.
  if (last.toLowerCase().startsWith("::ffff:") && isIP(last.slice(7)) === 4) last = last.slice(7);
  return isIP(last) ? last : null;
}

async function refreshAccessToken(
  refreshToken: string,
  forwardedFor: string | null,
): Promise<TokenResponseShape | null> {
  // Retried on a stale connection even though it is a POST. Repeating it is
  // safe in both cases: if the first attempt never arrived the retry simply
  // succeeds, and if it did, the retry trips reuse detection -- the same
  // sign-out that happens without it.
  const resp = await fetchUpstream(
    `${BACKEND_API_URL}/v1/auth/refresh`,
    {
      method: "POST",
      headers: {
        "content-type": "application/json",
        ...(forwardedFor ? { "x-forwarded-for": forwardedFor } : {}),
      },
      body: JSON.stringify({ refresh_token: refreshToken }),
      cache: "no-store",
    },
    true,
  );
  if (resp.status >= 500) {
    throw new UpstreamUnavailableError(`token refresh answered ${resp.status}`);
  }
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
  // Set explicitly (never copied through): see `clientIp`.
  const forwardedFor = clientIp(request);
  if (forwardedFor) headers.set("x-forwarded-for", forwardedFor);
  if (accessToken) headers.set("authorization", `Bearer ${accessToken}`);

  return fetchUpstream(
    target,
    { method: request.method, headers, body, cache: "no-store" },
    request.method === "GET" || request.method === "HEAD",
  );
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

/**
 * One refresh per refresh token, shared by every request that needs it.
 *
 * Refresh tokens rotate, and presenting one twice is treated by the backend
 * as theft: it revokes the whole token family. When an access token expires,
 * a page that fires several requests at once (the dashboards fire six, and
 * refetch every minute) sent several refreshes with the *same* token -- the
 * first rotated it, the rest tripped reuse detection, and the administrator
 * was signed out. The audit log showed these in pairs ~100 ms apart.
 *
 * Concurrent callers now await one promise, and its result is kept briefly
 * so requests that left the browser before the new cookies arrived get the
 * same new tokens rather than re-presenting the old one. Keyed by a hash, so
 * the raw token is not held as a map key.
 *
 * **Two minutes, not ten seconds.** A request carrying the old cookie can
 * reach this proxy long after the refresh that rotated it -- seen live at 28 s
 * and 61 s while the server was slow -- and past the window it re-presented
 * the rotated token, tripping reuse detection and revoking the whole session.
 * The window only has to outlast the slowest queued request; holding a result
 * longer costs nothing, since it is only ever handed to a caller that already
 * holds the token it was derived from.
 *
 * A *failed* refresh is not kept: it is dropped as soon as it settles, so the
 * next request after a backend restart tries again instead of inheriting the
 * failure.
 *
 * Per process: behind several console instances, requests can still land on
 * different ones. That needs a short reuse grace window on the backend --
 * recorded as open rather than implied fixed.
 */
const REFRESH_SHARE_MS = 120_000;
const refreshes = new Map<string, { at: number; result: Promise<TokenResponseShape | null> }>();

function refreshOnce(
  refreshToken: string,
  forwardedFor: string | null,
): Promise<TokenResponseShape | null> {
  const now = Date.now();
  for (const [key, entry] of refreshes) {
    if (now - entry.at > REFRESH_SHARE_MS) refreshes.delete(key);
  }
  const key = createHash("sha256").update(refreshToken).digest("hex");
  const existing = refreshes.get(key);
  if (existing) return existing.result;
  const result = refreshAccessToken(refreshToken, forwardedFor);
  refreshes.set(key, { at: now, result });
  result.catch(() => {
    if (refreshes.get(key)?.result === result) refreshes.delete(key);
  });
  return result;
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
      const refreshed = await refreshOnce(refreshToken, clientIp(request));
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

/**
 * The backend could not be reached: a 502 in the API's own `{detail}` shape.
 *
 * Unhandled, a network failure became a bare 500 from this proxy -- which
 * reads as "the server has a bug" rather than "it could not be reached", and
 * carries no message the console can show. Cookies are left alone on
 * purpose: an unreachable backend says nothing about whether the session is
 * still good.
 */
async function proxied(request: NextRequest, ctx: RouteContext): Promise<NextResponse> {
  const path = (await ctx.params).path;
  try {
    return await handle(request, path);
  } catch (err) {
    // Only a network failure ("fetch failed", with the socket error as its
    // cause) or a refresh the backend could not answer. Anything else is a bug
    // in this file and must stay a 500, not be relabelled as the backend's
    // absence.
    const unreachable =
      (err instanceof TypeError && Boolean((err as { cause?: unknown }).cause)) ||
      err instanceof UpstreamUnavailableError;
    if (!unreachable) throw err;
    console.error(`[bff] ${request.method} /${path.join("/")} could not reach the backend`, err);
    return NextResponse.json(
      { detail: "The service could not be reached. Please try again." },
      { status: 502 },
    );
  }
}

export const GET = proxied;
export const POST = proxied;
export const PUT = proxied;
export const PATCH = proxied;
export const DELETE = proxied;
