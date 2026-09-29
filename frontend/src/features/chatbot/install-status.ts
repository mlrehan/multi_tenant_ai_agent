import type { ChatWidget } from "@/lib/types";

/** A page that loaded the chatbot within this long counts as "installed".
 *  Longer than a quiet weekend, shorter than "someone removed the code a
 *  month ago and nobody noticed". */
export const RECENTLY_SEEN_DAYS = 7;

export type InstallState =
  /** The API is older than the install check: say nothing rather than guess. */
  | "unknown"
  | "off"
  | "installed"
  | "not-seen-recently"
  | "not-installed";

/** Same reduction the server applies (`normalise_origin`): a browser only
 *  ever sends `scheme://host[:port]`, lower-cased. */
export function toOrigin(value: string): string | null {
  try {
    const url = new URL(value.trim());
    if (url.protocol !== "https:" && url.protocol !== "http:") return null;
    return url.origin.toLowerCase();
  } catch {
    return null;
  }
}

export function installState(widget: ChatWidget, now: Date = new Date()): InstallState {
  if (widget.status !== "active") return "off";
  if (widget.last_seen_at === undefined) return "unknown";
  if (widget.last_seen_at === null) return "not-installed";
  const ageMs = now.getTime() - new Date(widget.last_seen_at).getTime();
  return ageMs <= RECENTLY_SEEN_DAYS * 86_400_000 ? "installed" : "not-seen-recently";
}

/**
 * The address a page was refused from, when it still needs the admin.
 *
 * Only while it is *not* in the list: once added, the old refusal is history,
 * not a problem. The comparison is on normalised origins, so a stored
 * `https://Example.com` matches a refusal from `https://example.com`.
 */
export function pendingRefusedOrigin(widget: ChatWidget): string | null {
  const refused = widget.last_refused_origin;
  if (!refused) return null;
  const allowed = new Set(widget.allowed_origins.map((o) => toOrigin(o) ?? o));
  return allowed.has(toOrigin(refused) ?? refused) ? null : refused;
}
