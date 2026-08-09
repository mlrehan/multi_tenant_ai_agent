/**
 * Client-side fetch wrapper. Every call goes to the same-origin
 * `/api/backend/*` proxy (never the FastAPI origin directly) so the
 * browser never needs to know where the backend lives or hold a token.
 *
 * Mirrors the error envelope verified against the real backend
 * (api/exception_handlers.py): `{"detail": string}` for most errors,
 * `{"detail": string, "violations": string[]}` for weak-password and
 * self-escalation responses, and FastAPI's own
 * `{"detail": [{"loc", "msg", "type"}, ...]}` shape for 422 validation
 * failures -- a genuinely different shape from every other error, so it's
 * normalized here rather than left for every call site to special-case.
 */

export interface FieldError {
  loc: (string | number)[];
  msg: string;
  type: string;
}

export class ApiError extends Error {
  readonly status: number;
  readonly violations: string[] | undefined;
  readonly fieldErrors: FieldError[] | undefined;

  constructor(
    status: number,
    message: string,
    opts?: { violations?: string[]; fieldErrors?: FieldError[] },
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.violations = opts?.violations;
    this.fieldErrors = opts?.fieldErrors;
  }

  /** True for the generic "not found / not yours" 404 the backend uses
   * deliberately to avoid leaking existence -- see docs/03-threat-model.md. */
  get isNotFound(): boolean {
    return this.status === 404;
  }

  get isForbidden(): boolean {
    return this.status === 403;
  }

  get isRateLimited(): boolean {
    return this.status === 429;
  }
}

interface ApiFetchOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  /**
   * Multipart payload, for file uploads. Mutually exclusive with `body`.
   *
   * Deliberately a separate option rather than "pass a FormData as body":
   * `body` is JSON-stringified, and the content-type header must be left
   * *unset* for FormData so the browser can add the multipart boundary.
   * Setting it by hand produces a body the server cannot parse, with an error
   * that points at the server rather than at this line.
   */
  formData?: FormData;
  tenantId?: string;
  signal?: AbortSignal;
}

function parseErrorBody(status: number, body: unknown): ApiError {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") {
      const violations = (body as { violations?: string[] }).violations;
      return new ApiError(status, detail, { violations });
    }
    if (Array.isArray(detail)) {
      // 422 validation-error shape.
      const fieldErrors = detail as FieldError[];
      const message = fieldErrors.map((f) => f.msg).join("; ") || "Validation failed";
      return new ApiError(status, message, { fieldErrors });
    }
  }
  return new ApiError(status, `Request failed with status ${status}`);
}

export async function apiFetch<T = unknown>(path: string, options: ApiFetchOptions = {}): Promise<T> {
  const headers: Record<string, string> = {};
  // No content-type for FormData -- see the option's docstring.
  if (options.body !== undefined) headers["content-type"] = "application/json";
  if (options.tenantId) headers["x-tenant-id"] = options.tenantId;

  const response = await fetch(`/api/backend/${path.replace(/^\/+/, "")}`, {
    method: options.method ?? "GET",
    headers,
    body:
      options.formData ??
      (options.body !== undefined ? JSON.stringify(options.body) : undefined),
    credentials: "same-origin",
    signal: options.signal,
  });

  const contentType = response.headers.get("content-type") ?? "";
  const isJson = contentType.includes("application/json");
  const body = isJson ? await response.json().catch(() => null) : null;

  if (!response.ok) {
    // A 401 that reaches here means the proxy already tried a transparent
    // refresh and it failed -- the session is genuinely over (expired,
    // revoked, reuse-detected, or the account is gone). Send the user to
    // sign in rather than surfacing the backend's raw "missing bearer
    // token" string on whatever screen they happened to be looking at.
    if (response.status === 401 && typeof window !== "undefined") {
      const next = encodeURIComponent(window.location.pathname);
      // A full page load, not router.push(), on purpose: the session is
      // dead, so every piece of client state derived from it (React Query
      // cache, tenant/impersonation stores) must be discarded. A soft
      // navigation would carry that stale state into the login screen and
      // back out again after the next sign-in. This is also a plain
      // function, not a component, so useRouter() isn't available anyway.
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination
      window.location.href = `/login?next=${next}`;
    }
    throw parseErrorBody(response.status, body);
  }

  return body as T;
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}
