"use client";

import Link from "next/link";
import { useIsFetching, useQueryClient } from "@tanstack/react-query";
import { format } from "date-fns";
import {
  ChevronRight,
  Coins,
  MessagesSquare,
  MessageSquareText,
  RefreshCw,
  ThumbsUp,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader } from "@/components/shared/page-header";
import { ErrorState } from "@/components/shared/states";
import { RiskBadge, StatusBadge } from "@/components/shared/status-badge";
import { ActivityChart } from "@/features/platform/dashboard/activity-chart";
import { AttentionPanel, buildAttention } from "@/features/platform/dashboard/attention-panel";
import { describeChange, plural, satisfaction, usd } from "@/features/platform/dashboard/format";
import { HealthCard } from "@/features/platform/dashboard/health-card";
import { KpiCard } from "@/features/platform/dashboard/kpi-card";
import {
  ProviderSpendSection,
  TenantSpendSection,
} from "@/features/platform/dashboard/spend-sections";
import {
  usePlatformActivity,
  usePlatformOverview,
  usePlatformUsers,
  useTenants,
} from "@/features/platform/hooks";
import {
  usePlatformEffectivePermissions,
  usePlatformPermissionCatalog,
  usePlatformRoles,
} from "@/features/rbac/hooks";

/**
 * The operator's first screen, ordered by what they need first:
 *
 * 1. **What needs me?** -- open problems, most urgent first, or an explicit
 *    all-clear. An empty space is not an all-clear; it could be a failed load.
 * 2. **How is the platform doing?** -- four headline numbers, each with the
 *    comparison that makes it mean something.
 * 3. **Trend and health** -- two weeks of activity beside service state.
 * 4. **Spend** -- by model, then by tenant.
 * 5. **Directory and my authority** -- reference data, deliberately last:
 *    the number of permission codes in the catalogue changes when someone
 *    ships code, not when something happens that an operator must act on.
 */
export default function PlatformOverviewPage() {
  const queryClient = useQueryClient();
  const tenants = useTenants();
  const users = usePlatformUsers({ limit: 5 });
  const roles = usePlatformRoles();
  const permissions = usePlatformPermissionCatalog();
  const mine = usePlatformEffectivePermissions();
  const overview = usePlatformOverview();
  const activity = usePlatformActivity();
  const refreshing =
    useIsFetching({ queryKey: ["platform-overview"] }) +
      useIsFetching({ queryKey: ["platform-activity"] }) >
    0;

  const a = activity.data;
  // A failed activity read must read as "unavailable", never as a skeleton
  // that spins for ever -- the one outcome indistinguishable from "loading".
  const unavailable = activity.error ? "—" : undefined;
  const o = overview.data;
  // Not memoised: it reads the clock ("waiting 14 minutes"), and a memo keyed
  // on the data would show an older age than the Operations card beside it.
  const attention = buildAttention(a, o);

  function refresh() {
    for (const key of ["platform-overview", "platform-activity", "platform-tenants", "platform-users"]) {
      void queryClient.invalidateQueries({ queryKey: [key] });
    }
  }

  // -- headline numbers -------------------------------------------------------
  const days = a?.trend_days ?? 7;
  const lastWeek = a?.daily.slice(-days) ?? [];
  const handoffsWeek = lastWeek.reduce((n, d) => n + d.handoffs, 0);
  const questions = a ? describeChange(a.questions, days) : undefined;
  const conversations = a ? describeChange(a.conversations, days) : undefined;

  const pctNow = a ? satisfaction(a.helpful.current, a.not_helpful.current) : null;
  const pctBefore = a ? satisfaction(a.helpful.previous, a.not_helpful.previous) : null;
  const ratings = a ? a.helpful.current + a.not_helpful.current : 0;
  const satisfactionTrend =
    pctNow === null || pctBefore === null
      ? undefined
      : {
          trend: (pctNow > pctBefore ? "up" : pctNow < pctBefore ? "down" : "flat") as
            | "up"
            | "down"
            | "flat",
          label:
            pctNow === pctBefore
              ? `Same as the previous ${a?.satisfaction_days} days`
              : `${pctNow > pctBefore ? "+" : ""}${pctNow - pctBefore} pts vs previous ${a?.satisfaction_days} days`,
        };

  // Tokens: the sum is only claimed when every tenant's counter was readable,
  // and a percentage only when every tenant has a cap -- otherwise the figure
  // would silently leave someone out.
  const tenantRows = o?.tenants ?? [];
  const tokensKnown = tenantRows.every((t) => t.used_tokens !== null);
  const tokensUsed = tenantRows.reduce((n, t) => n + (t.used_tokens ?? 0), 0);
  const allCapped = tenantRows.length > 0 && tenantRows.every((t) => t.max_tokens_per_month !== null);
  const allowance = tenantRows.reduce((n, t) => n + (t.max_tokens_per_month ?? 0), 0);
  const recordedSince = a?.usage_recorded_since ? new Date(a.usage_recorded_since) : null;
  const ledgerStartedThisMonth =
    recordedSince !== null &&
    recordedSince.getUTCFullYear() === new Date().getUTCFullYear() &&
    recordedSince.getUTCMonth() === new Date().getUTCMonth() &&
    recordedSince.getUTCDate() !== 1;

  const activeTenants = tenants.data?.filter((t) => t.status === "active").length;
  const suspendedTenants = tenants.data?.filter((t) => t.status === "suspended").length ?? 0;
  const catalogue = new Map((permissions.data ?? []).map((p) => [p.code, p]));

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Platform"
        title="Overview"
        description="Health, activity and spend across every tenant. Refreshes every minute."
        actions={
          <>
            {a && (
              <span className="text-xs text-muted-foreground" title={a.generated_at}>
                Updated {format(new Date(a.generated_at), "HH:mm")}
              </span>
            )}
            <Button variant="outline" size="sm" onClick={refresh} disabled={refreshing}>
              <RefreshCw className={refreshing ? "size-3.5 animate-spin" : "size-3.5"} />
              Refresh
            </Button>
          </>
        }
      />

      {activity.error ? (
        <ErrorState error={activity.error} resource="platform activity" scope="platform" />
      ) : (
        <AttentionPanel items={attention} loading={activity.isLoading || overview.isLoading} />
      )}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard
          icon={MessageSquareText}
          label={`Questions · ${days} days`}
          value={a?.questions.current.toLocaleString() ?? unavailable}
          trend={questions?.trend}
          trendLabel={questions?.label}
          detail={a ? `${plural(handoffsWeek, "handoff")} to a person` : undefined}
          series={a?.daily.map((d) => d.questions)}
        />
        <KpiCard
          icon={MessagesSquare}
          label={`Conversations · ${days} days`}
          value={a?.conversations.current.toLocaleString() ?? unavailable}
          trend={conversations?.trend}
          trendLabel={conversations?.label}
          detail={
            a && tenants.data
              ? `${a.active_tenants} of ${tenants.data.length} tenants active`
              : undefined
          }
          series={a?.daily.map((d) => d.conversations_started)}
        />
        <KpiCard
          icon={ThumbsUp}
          label={`Satisfaction · ${a?.satisfaction_days ?? 30} days`}
          value={a ? (pctNow === null ? "—" : `${pctNow}%`) : unavailable}
          trend={satisfactionTrend?.trend ?? (a && pctNow !== null ? "new" : undefined)}
          trendLabel={
            satisfactionTrend?.label ??
            (a && pctNow !== null ? `No ratings in the previous ${a.satisfaction_days} days` : undefined)
          }
          detail={
            a
              ? ratings === 0
                ? "No answers rated yet"
                : `rated helpful, from ${plural(ratings, "rating")}`
              : undefined
          }
        />
        <KpiCard
          icon={Coins}
          label="Tokens · this month"
          value={o ? (tokensKnown ? tokensUsed.toLocaleString() : "?") : undefined}
          detail={
            o
              ? [
                  allCapped && allowance > 0
                    ? `${Math.round((tokensUsed / allowance) * 100)}% of ${allowance.toLocaleString()} allowed`
                    : "Some tenants have no monthly cap",
                  ledgerStartedThisMonth && recordedSince
                    ? `recorded per answer since ${format(recordedSince, "d MMM")}`
                    : null,
                  // Only claimed once something is priced; "≈ $0.00" for a
                  // month with no prices entered would read as "free".
                  o.costs && o.costs.priced_tokens > 0
                    ? `≈ ${usd(o.costs.total_usd)} estimated${o.costs.unpriced_tokens > 0 ? " (partly priced)" : ""}`
                    : null,
                ]
                  .filter(Boolean)
                  .join(" · ")
              : undefined
          }
          series={a?.daily.map((d) => d.tokens)}
        />
      </div>

      <div className="grid gap-6 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader>
            <CardTitle>Activity</CardTitle>
            <CardDescription>
              Last 14 days, UTC. Counts questions in saved conversations (website chatbot and
              console threads); one-off console test questions are not saved, so they appear in
              token spend only.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {a ? (
              <ActivityChart daily={a.daily} />
            ) : activity.error ? (
              <p className="py-10 text-center text-sm text-muted-foreground">
                Activity is unavailable from this API.
              </p>
            ) : (
              <Skeleton className="h-64 w-full" />
            )}
          </CardContent>
        </Card>
        <HealthCard activity={a} failed={Boolean(activity.error)} />
      </div>

      <ProviderSpendSection overview={overview} />
      <TenantSpendSection overview={overview} />

      <div className="grid gap-6 lg:grid-cols-2 xl:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>Directory</CardTitle>
            <CardDescription>Who and what this platform governs.</CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="divide-y divide-border">
              <DirectoryRow
                href="/platform/tenants"
                label="Tenants"
                value={tenants.data?.length}
                hint={
                  tenants.data
                    ? `${activeTenants} active${suspendedTenants ? `, ${suspendedTenants} suspended` : ""}`
                    : undefined
                }
              />
              <DirectoryRow
                href="/platform/users"
                label="User accounts"
                value={users.error ? null : users.data?.total}
                hint={users.error ? "You can't view the user directory" : "across the platform"}
              />
              <DirectoryRow
                href="/platform/roles"
                label="Platform roles"
                value={roles.data?.length}
                hint="separate from tenant roles"
              />
              <DirectoryRow
                href="/platform/permissions"
                label="Platform permissions"
                value={permissions.data?.length}
                hint="in the catalogue"
              />
            </ul>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Recent tenants</CardTitle>
            <CardDescription>Newest first.</CardDescription>
          </CardHeader>
          <CardContent>
            {tenants.isLoading && <Skeleton className="h-24 w-full" />}
            {tenants.error && <ErrorState error={tenants.error} resource="tenants" scope="platform" />}
            {tenants.data && tenants.data.length === 0 && (
              <p className="text-sm text-muted-foreground">
                No tenants yet.{" "}
                <Link href="/platform/tenants" className="underline underline-offset-4">
                  Create the first one
                </Link>
                .
              </p>
            )}
            {tenants.data && tenants.data.length > 0 && (
              <ul className="divide-y divide-border">
                {tenants.data.slice(0, 5).map((tenant) => (
                  <li key={tenant.id} className="flex items-center justify-between py-2.5">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium">{tenant.display_name}</p>
                      <p className="font-mono text-xs text-muted-foreground">{tenant.slug}</p>
                    </div>
                    <StatusBadge status={tenant.status} />
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>

        <Card className="lg:col-span-2 xl:col-span-1">
          <CardHeader>
            <CardTitle>Your platform authority</CardTitle>
            <CardDescription>What you can do here, from your platform roles.</CardDescription>
          </CardHeader>
          <CardContent>
            {mine.isLoading && <Skeleton className="h-24 w-full" />}
            {mine.data && mine.data.permissions.length === 0 && (
              <p className="text-sm text-muted-foreground">
                You hold no platform permissions. Platform screens will refuse your requests —
                switch to a tenant from the top bar to do tenant-scope work.
              </p>
            )}
            {mine.data && mine.data.permissions.length > 0 && (
              <ul className="space-y-2.5">
                {mine.data.permissions.map((code) => {
                  const entry = catalogue.get(code);
                  return (
                    <li key={code} className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="text-sm">{entry?.description ?? code}</p>
                        <p className="truncate font-mono text-[0.7rem] text-muted-foreground">{code}</p>
                      </div>
                      {entry && <RiskBadge level={entry.risk_level} className="shrink-0" />}
                    </li>
                  );
                })}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function DirectoryRow({
  href,
  label,
  value,
  hint,
}: {
  href: string;
  label: string;
  /** `null` = not viewable by this operator; `undefined` = still loading. */
  value: number | null | undefined;
  hint?: string;
}) {
  return (
    <li>
      <Link
        href={href}
        className="group flex items-center justify-between gap-3 py-2.5 transition-colors"
      >
        <div className="min-w-0">
          <p className="text-sm font-medium group-hover:underline group-hover:underline-offset-4">
            {label}
          </p>
          {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
        </div>
        <span className="flex items-center gap-1 text-lg font-semibold tabular-nums">
          {value === undefined ? <Skeleton className="h-6 w-8" /> : value === null ? "—" : value}
          <ChevronRight className="size-4 text-muted-foreground" />
        </span>
      </Link>
    </li>
  );
}
