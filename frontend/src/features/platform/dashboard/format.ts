import { formatDistanceToNowStrict } from "date-fns";

import type { PeriodComparison, TenantSpend } from "@/lib/types";

/**
 * The daily message limit actually enforced for a tenant.
 *
 * An API from before `effective_messages_per_day` existed omits the field
 * entirely -- `undefined`, not `null` -- and a strict `=== null` check then
 * called `.toLocaleString()` on it and took the whole dashboard down. Falling
 * back to the platform ceiling is the pre-fix behaviour: possibly too high
 * for a tenant that set its own lower limit, but never a crash.
 */
export function enforcedDailyLimit(t: TenantSpend): number | null {
  return t.effective_messages_per_day === undefined
    ? t.max_messages_per_day
    : t.effective_messages_per_day;
}

export type Trend = "up" | "down" | "flat" | "new" | "none";

/**
 * "12% more than the previous 7 days" -- or an honest refusal to compute one.
 *
 * A percentage change from zero is infinite, and "+∞%" or "+100%" both
 * mislead: the first is noise, the second is simply wrong. So growth from
 * nothing is labelled as new, and two empty periods say so rather than
 * printing "0%", which reads as "stable" when it means "no data".
 */
export function describeChange(c: PeriodComparison, days: number): { trend: Trend; label: string } {
  if (c.current === 0 && c.previous === 0) {
    return { trend: "none", label: `None in the last ${days * 2} days` };
  }
  if (c.previous === 0) {
    return { trend: "new", label: `None in the previous ${days} days` };
  }
  const pct = Math.round(((c.current - c.previous) / c.previous) * 100);
  if (pct === 0) return { trend: "flat", label: `Same as the previous ${days} days` };
  return {
    trend: pct > 0 ? "up" : "down",
    label: `${pct > 0 ? "+" : ""}${pct}% vs previous ${days} days`,
  };
}

/** Helpful as a whole-number share of ratings; null when there are none. */
export function satisfaction(helpful: number, notHelpful: number): number | null {
  const total = helpful + notHelpful;
  return total === 0 ? null : Math.round((helpful / total) * 100);
}

export function ago(iso: string | null): string | null {
  return iso ? formatDistanceToNowStrict(new Date(iso), { addSuffix: false }) : null;
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n.toLocaleString()} ${n === 1 ? one : many}`;
}

/** How loudly to show a usage threshold: 100% has stopped the chatbot, 90
 *  and 95 are the last warnings, 80 is notice. */
export function alertSeverity(level: 80 | 90 | 95 | 100): "critical" | "warning" | "info" {
  return level === 100 ? "critical" : level >= 90 ? "warning" : "info";
}

/** Only for an API older than the server-side `*_alert_level` fields: the
 *  same integer rule as `usage_alert_level` in the backend. */
export function fallbackAlertLevel(used: number | null, limit: number | null): 80 | 90 | 95 | 100 | null {
  if (used === null || limit === null) return null;
  if (limit <= 0) return 100;
  for (const level of [100, 95, 90, 80] as const) {
    if (used * 100 >= level * limit) return level;
  }
  return null;
}

/** An estimated cost for display. Money arrives as a decimal string; small
 *  amounts keep enough places to be non-zero ("$0.0084"), larger ones are
 *  shown to the cent. A real amount below $0.0001 is "< $0.0001", never "$0". */
export function usd(value: string | number): string {
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return "?";
  if (n === 0) return "$0.00";
  if (n > 0 && n < 0.0001) return "< $0.0001";
  const places = n < 1 ? 4 : 2;
  return n.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: places,
    maximumFractionDigits: places,
  });
}
