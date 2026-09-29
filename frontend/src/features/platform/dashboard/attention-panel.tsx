"use client";

import Link from "next/link";
import { AlertTriangle, CheckCircle2, CircleAlert, Info } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import type { PlatformActivity, PlatformOverview } from "@/lib/types";
import { alertSeverity, ago, enforcedDailyLimit, fallbackAlertLevel, plural } from "./format";

export type Severity = "critical" | "warning" | "info";

export interface AttentionItem {
  key: string;
  severity: Severity;
  title: string;
  detail?: string;
  href?: string;
}

const HEALTH_NAMES: Record<string, string> = {
  postgres_tenant: "Database (tenant connection)",
  postgres_platform: "Database (platform connection)",
  redis: "Redis (cache, quotas and job queue)",
};

/**
 * Everything that wants a human, most urgent first.
 *
 * Built from both reads on purpose. Spend (`/overview`) knows who is running
 * out of allowance; activity (`/activity`) knows who is waiting and what is
 * broken. An operator should not have to cross-reference two panels to learn
 * that one tenant is both out of tokens *and* has visitors waiting.
 */
export function buildAttention(
  activity: PlatformActivity | undefined,
  overview: PlatformOverview | undefined,
): AttentionItem[] {
  const items: AttentionItem[] = [];

  for (const dep of activity?.health ?? []) {
    if (!dep.healthy) {
      items.push({
        key: `health-${dep.name}`,
        severity: "critical",
        title: `${HEALTH_NAMES[dep.name] ?? dep.name} is not responding`,
        detail: "Requests that need it are failing. Check the service and its credentials.",
      });
    }
  }

  if (activity && activity.documents_stuck > 0) {
    items.push({
      key: "stuck",
      severity: "critical",
      title: `${plural(activity.documents_stuck, "document")} stuck in processing for over ${activity.stuck_after_minutes} minutes`,
      detail: "Usually the ingestion worker is down or a job was lost. Tenants see these as still processing.",
    });
  }

  for (const t of activity?.attention ?? []) {
    if (t.waiting_handoffs > 0) {
      const waited = ago(t.oldest_waiting_at);
      items.push({
        key: `handoff-${t.tenant_id}`,
        severity: "warning",
        title: `${t.display_name}: ${plural(t.waiting_handoffs, "conversation")} waiting for a person`,
        detail: waited ? `Oldest has been waiting ${waited}.` : undefined,
      });
    }
  }

  for (const t of overview?.tenants ?? []) {
    // Levels come from the server (80/90/95/100), so this screen and the
    // tenant's own warn at the same moment. The fallbacks only serve an older
    // API that does not send them.
    const tokenLevel =
      t.token_alert_level !== undefined
        ? t.token_alert_level
        : fallbackAlertLevel(t.used_tokens, t.max_tokens_per_month);
    if (tokenLevel !== null) {
      items.push({
        key: `low-${t.tenant_id}`,
        severity: alertSeverity(tokenLevel),
        title:
          tokenLevel === 100
            ? `${t.display_name}: monthly token allowance used up — chatbot has stopped answering`
            : `${t.display_name}: ${tokenLevel}% of monthly tokens used`,
        detail:
          t.used_tokens === null || t.max_tokens_per_month === null
            ? undefined
            : `${t.used_tokens.toLocaleString()} of ${t.max_tokens_per_month.toLocaleString()} tokens.`,
        href: "/platform/entitlements",
      });
    }
    const dailyLimit = enforcedDailyLimit(t);
    const messageLevel =
      t.message_alert_level !== undefined
        ? t.message_alert_level
        : fallbackAlertLevel(t.used_messages_today, dailyLimit);
    if (messageLevel !== null && dailyLimit !== null && t.used_messages_today !== null) {
      items.push({
        key: `daily-${t.tenant_id}`,
        severity: alertSeverity(messageLevel),
        title:
          messageLevel === 100
            ? `${t.display_name}: daily message limit reached`
            : `${t.display_name}: ${messageLevel}% of today's messages used`,
        detail: `${t.used_messages_today.toLocaleString()} of ${dailyLimit.toLocaleString()} used.${messageLevel === 100 ? " The chatbot refuses further questions until the tenant's day resets." : ""}`,
        href: "/platform/entitlements",
      });
    }
  }

  for (const t of activity?.attention ?? []) {
    if (t.failed_documents > 0) {
      items.push({
        key: `failed-${t.tenant_id}`,
        severity: "info",
        title: `${t.display_name}: ${plural(t.failed_documents, "document")} failed to ingest`,
        detail: "Each shows its reason on the tenant's Knowledge bases screen.",
      });
    }
  }

  return items;
}

const SEVERITY: Record<Severity, { icon: LucideIcon; className: string }> = {
  critical: { icon: CircleAlert, className: "text-destructive" },
  warning: { icon: AlertTriangle, className: "text-amber-600 dark:text-amber-400" },
  info: { icon: Info, className: "text-muted-foreground" },
};

export function AttentionPanel({
  items,
  loading,
  allClearTitle = "Nothing needs your attention",
  allClearDetail = "Services healthy, no one waiting for a person, no stuck ingestion, every tenant within its limits.",
}: {
  items: AttentionItem[];
  loading: boolean;
  allClearTitle?: string;
  allClearDetail?: string;
}) {
  if (loading) return <Skeleton className="h-20 w-full rounded-xl" />;

  if (items.length === 0) {
    return (
      <div className="flex items-center gap-3 rounded-xl border border-emerald-600/25 bg-emerald-600/5 px-4 py-3">
        <CheckCircle2 className="size-5 shrink-0 text-emerald-600 dark:text-emerald-400" />
        <div>
          <p className="text-sm font-medium">{allClearTitle}</p>
          <p className="text-xs text-muted-foreground">{allClearDetail}</p>
        </div>
      </div>
    );
  }

  const critical = items.some((i) => i.severity === "critical");
  return (
    <section
      aria-label="Needs attention"
      className={cn(
        "rounded-xl border",
        critical ? "border-destructive/40 bg-destructive/5" : "border-amber-500/30 bg-amber-500/5",
      )}
    >
      <div className="flex items-center justify-between border-b border-inherit px-4 py-2.5">
        <h2 className="text-sm font-semibold">Needs attention</h2>
        <span className="text-xs text-muted-foreground">{plural(items.length, "item")}</span>
      </div>
      <ul className="divide-y divide-border/60">
        {items.map((item) => {
          const { icon: Icon, className } = SEVERITY[item.severity];
          const body = (
            <div className="flex items-start gap-3 px-4 py-2.5">
              <Icon className={cn("mt-0.5 size-4 shrink-0", className)} />
              <div className="min-w-0">
                <p className="text-sm font-medium">{item.title}</p>
                {item.detail && <p className="text-xs text-muted-foreground">{item.detail}</p>}
              </div>
            </div>
          );
          return (
            <li key={item.key}>
              {item.href ? (
                <Link href={item.href} className="block transition-colors hover:bg-muted/40">
                  {body}
                </Link>
              ) : (
                body
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
