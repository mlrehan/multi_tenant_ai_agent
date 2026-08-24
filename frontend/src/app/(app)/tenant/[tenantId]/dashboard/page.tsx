"use client";

import { use as usePromise } from "react";
import Link from "next/link";
import { ArrowRight, Database, Inbox, KeySquare, Users } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader } from "@/components/shared/page-header";
import { IdentityChip } from "@/components/shared/identity-chip";
import { PermissionList } from "@/components/shared/permission-list";
import { SpendMeter } from "@/components/shared/spend-meter";
import { useTenantEffectivePermissions, useTenantPermissionCatalog } from "@/features/rbac/hooks";
import { useTenantMembers } from "@/features/tenancy/hooks";
import { useKnowledgeBases } from "@/features/ai-resources/hooks";
import { useTenantPlan, useUnassignedInbox } from "@/features/chatbot/hooks";

/** Same threshold the platform dashboard highlights at.
 *
 *  The server computes `running_low` for the *platform* view; this screen reads
 *  the tenant's own plan endpoint, which returns the raw numbers, so the rule
 *  is applied here. Both sides therefore say "under 10%" -- if that ever needs
 *  changing, `LOW_REMAINING_FRACTION` in `platform_overview.py` is the value to
 *  move, and this constant must follow it. */
const LOW_REMAINING_FRACTION = 0.1;

function isRunningLow(limit: number | null, used: number | null): boolean {
  if (limit === null || limit <= 0 || used === null) return false;
  return limit - used < limit * LOW_REMAINING_FRACTION;
}

export default function TenantDashboardPage({
  params,
}: {
  params: Promise<{ tenantId: string }>;
}) {
  const { tenantId } = usePromise(params);

  const permissions = useTenantEffectivePermissions(tenantId);
  const catalog = useTenantPermissionCatalog(tenantId);
  const members = useTenantMembers(tenantId);
  const knowledgeBases = useKnowledgeBases(tenantId);
  const plan = useTenantPlan(tenantId);

  const held = new Set(permissions.data?.permissions ?? []);
  const canManageMembers = held.has("tenant.users.manage");
  // The queue is only meaningful to someone who can work it. A waiting count
  // shown to a member who cannot open the Inbox is a number they can do
  // nothing about.
  const canWorkInbox = held.has("tenant.conversations.view");
  const inbox = useUnassignedInbox(tenantId, canWorkInbox);

  const tokensUsed = plan.data?.tokens_used_this_month ?? null;
  const tokenLimit = plan.data?.max_tokens_per_month ?? null;
  const tokensLow = isRunningLow(tokenLimit, tokensUsed);
  const messagesUsed = plan.data?.messages_used_today ?? null;
  const messageLimit = plan.data?.effective_daily_message_limit ?? null;
  const waiting = inbox.data?.conversations.length ?? 0;

  return (
    <div>
      <PageHeader
        title="Overview"
        description="What you can see and do in this tenant."
        actions={<IdentityChip value={tenantId} label="tenant" truncate={false} />}
      />

      {/* Usage first: this is the part that changes daily and the part that
          stops the chatbot working when it runs out. */}
      <div className="mb-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {plan.isLoading ? (
          <>
            <Skeleton className="h-32 w-full" />
            <Skeleton className="h-32 w-full" />
            <Skeleton className="h-32 w-full" />
          </>
        ) : (
          <>
            <SpendMeter
              label="Tokens this month"
              used={tokensUsed}
              limit={tokenLimit}
              remaining={
                tokenLimit === null || tokensUsed === null
                  ? null
                  : Math.max(0, tokenLimit - tokensUsed)
              }
              runningLow={tokensLow}
              note={tokensLow ? "Ask your platform administrator to raise it" : undefined}
            />
            <Link href={`/tenant/${tenantId}/conversations`} className="block">
              <div className="h-full transition-colors hover:opacity-90">
                <SpendMeter
                  label="Messages today"
                  unit="messages"
                  used={messagesUsed}
                  limit={messageLimit}
                  remaining={
                    messageLimit === null || messagesUsed === null
                      ? null
                      : Math.max(0, messageLimit - messagesUsed)
                  }
                  runningLow={isRunningLow(messageLimit, messagesUsed)}
                  note="View conversations"
                />
              </div>
            </Link>
            <WaitingTile
              tenantId={tenantId}
              waiting={waiting}
              canWork={canWorkInbox}
              isLoading={canWorkInbox && inbox.isLoading}
            />
          </>
        )}
      </div>

      <div className="mb-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <StatTile
          icon={Users}
          label="Members"
          value={canManageMembers ? members.data?.length : undefined}
          hint={canManageMembers ? undefined : "Needs tenant.users.manage"}
          href={canManageMembers ? `/tenant/${tenantId}/members` : undefined}
          isLoading={canManageMembers && members.isLoading}
        />
        <StatTile
          icon={Database}
          label="Knowledge bases"
          value={knowledgeBases.data?.knowledge_bases.length}
          href={`/tenant/${tenantId}/knowledge-bases`}
          isLoading={knowledgeBases.isLoading}
        />
        <StatTile
          icon={KeySquare}
          label="Your permissions"
          value={permissions.data?.permissions.length}
          href={`/tenant/${tenantId}/rbac`}
          isLoading={permissions.isLoading}
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>What you can do here</CardTitle>
          <CardDescription>
            Your effective permissions in this tenant, after roles, inheritance, and overrides are
            resolved.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {permissions.isLoading && <Skeleton className="h-32 w-full" />}
          {permissions.data && catalog.data && (
            <PermissionList
              permissions={catalog.data.filter((p) => held.has(p.code))}
              emptyMessage="You hold no permissions in this tenant yet. Ask an administrator to assign you a role."
            />
          )}
        </CardContent>
      </Card>
    </div>
  );
}

/** People waiting for a colleague.
 *
 *  Highlighted rather than merely counted when the queue is not empty: a
 *  visitor waiting is the one number on this screen with a person on the other
 *  end of it. The console-wide alerting (chime, system notification, tab
 *  badge, push) is separate and lives in the app shell -- this tile is what a
 *  colleague sees when they *do* look at the dashboard. */
function WaitingTile({
  tenantId,
  waiting,
  canWork,
  isLoading,
}: {
  tenantId: string;
  waiting: number;
  canWork: boolean;
  isLoading?: boolean;
}) {
  if (!canWork) {
    return (
      <div className="rounded-lg border p-4">
        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Waiting for a colleague
        </p>
        <p className="mt-1 text-2xl font-semibold tabular-nums">
          <span className="text-base font-normal text-muted-foreground">—</span>
        </p>
        <p className="mt-2 text-xs text-muted-foreground">Needs tenant.conversations.view</p>
      </div>
    );
  }

  const urgent = waiting > 0;
  return (
    <Link
      href={`/tenant/${tenantId}/inbox`}
      className={[
        "block rounded-lg border p-4 transition-colors",
        urgent
          ? "animate-pulse border-destructive/60 bg-destructive/10 hover:bg-destructive/15"
          : "hover:border-primary/40",
      ].join(" ")}
    >
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Waiting for a colleague
        </p>
        <Inbox className={urgent ? "size-4 text-destructive" : "size-4 text-muted-foreground"} />
      </div>
      <div className="mt-1 text-2xl font-semibold tabular-nums">
        {isLoading ? (
          <Skeleton className="h-8 w-10" />
        ) : (
          <span className={urgent ? "text-destructive" : undefined}>{waiting}</span>
        )}
      </div>
      <p className="mt-2 flex items-center gap-1 text-xs text-muted-foreground">
        {urgent ? "Open the Inbox and claim them" : "Nobody is waiting"}
        <ArrowRight className="size-3" />
      </p>
    </Link>
  );
}

function StatTile({
  icon: Icon,
  label,
  value,
  hint,
  href,
  isLoading,
}: {
  icon: LucideIcon;
  label: string;
  value?: number;
  hint?: string;
  href?: string;
  isLoading?: boolean;
}) {
  const body = (
    <CardContent className="py-4">
      <div className="flex items-center justify-between">
        <span className="text-sm text-muted-foreground">{label}</span>
        <Icon className="size-4 text-muted-foreground" />
      </div>
      <div className="mt-2 flex items-end justify-between gap-2">
        {isLoading ? (
          <Skeleton className="h-8 w-12" />
        ) : (
          <span className="text-2xl font-semibold tabular-nums">
            {value ?? <span className="text-base font-normal text-muted-foreground">—</span>}
          </span>
        )}
        {href && <ArrowRight className="size-3.5 text-muted-foreground" />}
      </div>
      {hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
    </CardContent>
  );

  if (!href) return <Card>{body}</Card>;

  return (
    <Card className="transition-colors hover:border-primary/40">
      <Link href={href} className="block">
        {body}
      </Link>
    </Card>
  );
}
