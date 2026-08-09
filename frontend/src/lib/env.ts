// Server-only. Never imported from a "use client" module -- the backend
// origin is not meant to be reachable from the browser at all; every client
// request goes through the same-origin `/api/backend/*` proxy instead.

function required(name: string, fallback?: string): string {
  const value = process.env[name] ?? fallback;
  if (!value) {
    throw new Error(`Missing required environment variable: ${name}`);
  }
  return value;
}

export const BACKEND_API_URL = required(
  "BACKEND_API_URL",
  process.env.NODE_ENV === "production" ? undefined : "http://localhost:8000",
).replace(/\/+$/, "");

export const IS_PRODUCTION = process.env.NODE_ENV === "production";
