"use client";

import { use as usePromise } from "react";
import Link from "next/link";
import { format } from "date-fns";
import {
  ArrowRight,
  BookOpen,
  Bot,
  HandHelping,
  MessageSquareText,
  MessagesSquare,
  ThumbsUp,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader } from "@/components/shared/page-header";
import { useChatWidgets, useKnowledgeBases } from "@/features/ai-resources/hooks";
import {
  useChatbotSettings,
  useTeams,
  useTenantActivity,
  useTenantPlan,
} from "@/features/chatbot/hooks";
import { ActivityChart } from "@/features/platform/dashboard/activity-chart";
import {
  AttentionPanel,
  type AttentionItem,
} from "@/features/platform/dashboard/attention-panel";
import {
  ago,
  alertSeverity,
  describeChange,
  fallbackAlertLevel,
  plural,
  satisfaction,
} from "@/features/platform/dashboard/format";
import { KpiCard } from "@/features/platform/dashboard/kpi-card";
import { useTenantEffectivePermissions } from "@/features/rbac/hooks";
import { tenantLabel } from "@/features/tenancy/api";
import { useMyMemberships } from "@/features/tenancy/hooks";
import { installState, pendingRefusedOrigin } from "@/features/chatbot/install-status";
import { SetupChecklist, type SetupStep } from "@/features/tenant-dashboard/setup-checklist";
import { UnansweredQuestionsCard } from "@/features/tenant-dashboard/unanswered-card";

/**
 * The tenant's home screen, written for the person who runs a nursery rather
 * than the person who built the platform.
 *
 * It answers, in order: *is my chatbot ready?* (the setup checklist, until it
 * is), *does anything need me?* (attention, or an explicit all-clear), *how is
 * it doing?* (four numbers, each with last week beside it), and *what does it
 * know, and where is it live?*. Nothing here is a raw identifier or a
 * permission code -- those answer an engineer's questions, and live on the
 * screens built for them.
 *
 * Every card degrades on its own: a member who may not read conversations sees
 * the chatbot and knowledge cards without activity, never an error page.
 */
export default function TenantDashboardPage({
  params,
}: {
  params: Promise<{ tenantId: string }>;
}) {
  const { tenantId } = usePromise(params);
  const base = `/tenant/${tenantId}`;

  const permissions = useTenantEffectivePermissions(tenantId);
  const held = new Set(permissions.data?.permissions ?? []);
  const canViewActivity = held.has("tenant.conversations.view");

  const memberships = useMyMemberships();
  const plan = useTenantPlan(tenantId);
  const settings = useChatbotSettings(tenantId);
  const widgets = useChatWidgets(tenantId);
  const knowledgeBases = useKnowledgeBases(tenantId);
  const teams = useTeams(tenantId);
  const activity = useTenantActivity(tenantId, canViewActivity);

  const membership = memberships.data?.find((m) => m.tenant_id === tenantId);
  const a = activity.data;
  // A failed activity read shows "—" and a note, never a placeholder that
  // spins for ever (e.g. an API from before `/activity` existed).
  const activityFailed = Boolean(activity.error);
  const unavailable = activityFailed ? "—" : undefined;
  const s = settings.data;
  const liveWidgets = (widgets.data?.chat_widgets ?? []).filter((w) => w.status === "active");
  const websites = [...new Set(liveWidgets.flatMap((w) => w.allowed_origins))];
  // The most recently opened live widget; `undefined` against an API that
  // predates the install check, so the card makes no claim either way.
  const lastSeen = liveWidgets.some((w) => w.last_seen_at === undefined)
    ? undefined
    : ([...liveWidgets].sort((x, y) =>
        (y.last_seen_at ?? "").localeCompare(x.last_seen_at ?? ""),
      )[0] ?? null);
  const activeTeams = (teams.data?.teams ?? []).filter((t) => t.is_active);
  const emptyTeams = activeTeams.filter((t) => t.member_ids.length === 0);

  // -- setup checklist ----------------------------------------------------------
  const knowledgeReady =
    a !== undefined ? a.knowledge.ready > 0 : (knowledgeBases.data?.knowledge_bases.length ?? 0) > 0;
  const steps: SetupStep[] = [
    {
      key: "knowledge",
      title: "Teach your chatbot",
      why: "Add your website pages or documents. Your chatbot only answers from what you give it.",
      href: `${base}/knowledge-bases`,
      done: knowledgeReady,
    },
    {
      key: "about",
      title: "Tell it about your organisation",
      why: "A short description helps it introduce you and answer general questions well.",
      href: `${base}/chatbot`,
      done: Boolean(s?.company_description?.trim()),
    },
    {
      key: "website",
      title: "Put it on your website",
      why: "Copy one line of code into your website, or send it to whoever manages your site.",
      href: `${base}/chatbot`,
      // Done when a real page has loaded it -- not when a widget merely
      // exists. An older API without the install check falls back to that.
      done: liveWidgets.some((w) =>
        w.last_seen_at === undefined ? true : installState(w) === "installed",
      ),
    },
    {
      key: "people",
      title: "Choose who helps visitors",
      why: "When a visitor asks for a person, their conversation goes to a team. Add people to it.",
      href: `${base}/inbox`,
      done: s?.allow_human_handoff === false || activeTeams.some((t) => t.member_ids.length > 0),
    },
  ];
  // Only shown once the answers are in: a checklist that flashes "not done"
  // while its data loads tells a set-up tenant they have work to do.
  const checklistReady =
    settings.data !== undefined && widgets.data !== undefined && teams.data !== undefined;

  // -- attention -------------------------------------------------------------
  const tokensUsed = plan.data?.tokens_used_this_month ?? null;
  const tokenLimit = plan.data?.max_tokens_per_month ?? null;
  const messagesUsed = plan.data?.messages_used_today ?? null;
  const messageLimit = plan.data?.effective_daily_message_limit ?? null;
  const attention: AttentionItem[] = [];
  if (s && !s.ai_chatbot_enabled) {
    attention.push({
      key: "off",
      severity: "critical",
      title: "Your chatbot is switched off",
      detail: "Visitors are sent straight to your team and get no automatic answers.",
      href: `${base}/chatbot`,
    });
  }
  if (a && a.waiting_handoffs > 0) {
    attention.push({
      key: "waiting",
      severity: "warning",
      title: `${plural(a.waiting_handoffs, "visitor")} waiting to talk to a person`,
      detail: a.oldest_waiting_at ? `The longest has waited ${ago(a.oldest_waiting_at)}.` : undefined,
      href: `${base}/inbox`,
    });
  }
  // 80 / 90 / 95 / 100%, decided by the server with the same rule the
  // platform operator's screen uses. The fallback only serves an older API.
  const messageLevel =
    plan.data?.message_alert_level !== undefined
      ? plan.data.message_alert_level
      : fallbackAlertLevel(messagesUsed, messageLimit);
  if (messageLevel !== null && messageLimit !== null) {
    attention.push({
      key: "daily",
      severity: messageLevel === 100 ? "warning" : alertSeverity(messageLevel),
      title:
        messageLevel === 100
          ? "Today's question limit is used up"
          : `${messageLevel}% of today's questions used`,
      detail:
        messageLevel === 100
          ? `Your chatbot has answered ${messageLimit.toLocaleString()} questions today. Visitors can still ask for a person; it starts answering again at midnight (${s?.quota_timezone ?? "UTC"}).`
          : `${(messagesUsed ?? 0).toLocaleString()} of ${messageLimit.toLocaleString()} today. When the limit is reached the chatbot stops answering until midnight (${s?.quota_timezone ?? "UTC"}).`,
      href: `${base}/chatbot`,
    });
  }
  const tokenLevel =
    plan.data?.token_alert_level !== undefined
      ? plan.data.token_alert_level
      : fallbackAlertLevel(tokensUsed, tokenLimit);
  if (tokenLevel !== null) {
    attention.push({
      key: "monthly",
      severity: alertSeverity(tokenLevel),
      title:
        tokenLevel === 100
          ? "Your chatbot has used this month's AI allowance"
          : `${tokenLevel}% of this month's AI allowance used`,
      detail:
        tokenLevel === 100
          ? "It has stopped answering until the 1st of next month (UTC). Ask your platform administrator to raise the allowance."
          : "When it runs out, the chatbot stops answering until the 1st of next month. Ask your platform administrator if you need more.",
    });
  }
  for (const w of liveWidgets) {
    const refused = pendingRefusedOrigin(w);
    if (!refused) continue;
    attention.push({
      key: `refused-${w.id}`,
      severity: "warning",
      title: `Your chatbot is hidden on ${refused}`,
      detail: `A page there tried to show it${w.last_refused_at ? ` ${ago(w.last_refused_at)} ago` : ""}, but that address isn't in your list.`,
      href: `${base}/chatbot`,
    });
  }
  if (a && (a.knowledge.failed > 0 || a.documents_stuck > 0)) {
    attention.push({
      key: "sources",
      severity: "info",
      title: `${plural(a.knowledge.failed + a.documents_stuck, "source")} could not be read`,
      detail: "Your chatbot can't use these. Open your knowledge to see why and try again.",
      href: `${base}/knowledge-bases`,
    });
  }
  if (s?.allow_human_handoff && emptyTeams.length > 0) {
    attention.push({
      key: "teams",
      severity: "info",
      title: `${plural(emptyTeams.length, "team")} with no one in it`,
      detail: `Visitors aren’t offered ${emptyTeams.map((t) => `“${t.name}”`).join(" or ")} until you add someone to ${emptyTeams.length === 1 ? "it" : "them"}.`,
      href: `${base}/inbox`,
    });
  }

  // -- headline numbers -----------------------------------------------------
  const days = a?.trend_days ?? 7;
  const questions = a ? describeChange(a.questions, days) : undefined;
  const conversations = a ? describeChange(a.conversations, days) : undefined;
  const handoffs = a ? describeChange(a.handoffs, days) : undefined;
  const pctNow = a ? satisfaction(a.helpful.current, a.not_helpful.current) : null;
  const ratings = a ? a.helpful.current + a.not_helpful.current : 0;

  const name = membership ? tenantLabel(membership) : "Overview";

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Overview"
        title={name}
        description="How your website chatbot is doing, and anything that needs you."
        actions={
          <>
            {a && (
              <span className="text-xs text-muted-foreground" title={a.generated_at}>
                Updated {format(new Date(a.generated_at), "HH:mm")}
              </span>
            )}
            <Button size="sm" variant="outline" nativeButton={false} render={<Link href={`${base}/chatbot`} />}>
              <Bot className="size-3.5" />
              Chatbot settings
            </Button>
          </>
        }
      />

      {checklistReady && <SetupChecklist steps={steps} />}

      <AttentionPanel
        items={attention}
        loading={plan.isLoading || settings.isLoading || (canViewActivity && activity.isLoading)}
        allClearTitle="All good — nothing needs you right now"
        allClearDetail="Your chatbot is on, nobody is waiting for a person, and all your sources can be read."
      />

      {canViewActivity && (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <KpiCard
            icon={MessageSquareText}
            label={`Questions asked · ${days} days`}
            value={a?.questions.current.toLocaleString() ?? unavailable}
            trend={questions?.trend}
            trendLabel={questions?.label}
            series={a?.daily.map((d) => d.questions)}
          />
          <KpiCard
            icon={MessagesSquare}
            label={`Conversations · ${days} days`}
            value={a?.conversations.current.toLocaleString() ?? unavailable}
            trend={conversations?.trend}
            trendLabel={conversations?.label}
            series={a?.daily.map((d) => d.conversations_started)}
          />
          <KpiCard
            icon={HandHelping}
            label={`Passed to your team · ${days} days`}
            value={a?.handoffs.current.toLocaleString() ?? unavailable}
            trend={handoffs?.trend}
            trendLabel={handoffs?.label}
            // More handoffs is not good news: each is a question the chatbot
            // could not finish.
            goodWhen="down"
            detail={a ? `${plural(a.handled_handoffs, "conversation")} with your team now` : undefined}
          />
          <KpiCard
            icon={ThumbsUp}
            label={`Helpful answers · ${a?.satisfaction_days ?? 30} days`}
            value={a ? (pctNow === null ? "—" : `${pctNow}%`) : unavailable}
            detail={
              a
                ? ratings === 0
                  ? "No ratings yet — visitors can rate answers with 👍 or 👎"
                  : `from ${plural(ratings, "rating")}`
                : undefined
            }
          />
        </div>
      )}

      <div className="grid gap-6 xl:grid-cols-3">
        {canViewActivity && (
          <Card className="xl:col-span-2">
            <CardHeader>
              <CardTitle>Activity</CardTitle>
              <CardDescription>Questions and new conversations each day, last 14 days.</CardDescription>
            </CardHeader>
            <CardContent>
              {a ? (
                <ActivityChart daily={a.daily} />
              ) : activityFailed ? (
                <p className="py-10 text-center text-sm text-muted-foreground">
                  Activity isn&rsquo;t available right now.
                </p>
              ) : (
                <Skeleton className="h-64 w-full" />
              )}
            </CardContent>
          </Card>
        )}

        <Card className={canViewActivity ? undefined : "xl:col-span-2"}>
          <CardHeader>
            <CardTitle className="flex items-center justify-between gap-2">
              Your chatbot
              {s && (
                <Badge variant={s.ai_chatbot_enabled ? "secondary" : "outline"}>
                  {s.ai_chatbot_enabled ? "On" : "Off"}
                </Badge>
              )}
            </CardTitle>
            <CardDescription>
              {/* Nothing is claimed until the list has loaded: "not on any
                  website" while it is still loading is a false alarm. */}
              {widgets.data === undefined
                ? " "
                : liveWidgets.length === 0
                  ? "Not on any website yet."
                  : lastSeen?.last_seen_at
                    ? `Last opened on ${lastSeen.last_seen_origin?.replace(/^https?:\/\//, "")} ${ago(lastSeen.last_seen_at)} ago.`
                    : lastSeen === undefined
                      ? `Set up for ${plural(websites.length, "website")}.`
                      : `Set up for ${plural(websites.length, "website")} — not seen on them yet.`}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {websites.length > 0 && (
              <ul className="space-y-1">
                {websites.slice(0, 3).map((site) => (
                  <li key={site} className="truncate text-sm text-muted-foreground" title={site}>
                    {site.replace(/^https?:\/\//, "")}
                  </li>
                ))}
              </ul>
            )}
            <UsageRow
              label="Questions answered today"
              used={messagesUsed}
              limit={messageLimit}
              loading={plan.isLoading}
            />
            <UsageRow
              label="AI usage this month"
              used={tokensUsed}
              limit={tokenLimit}
              loading={plan.isLoading}
              asPercent
              hint="Each answer uses some of your monthly allowance; longer answers use more."
            />
            <Link
              href={`${base}/chatbot`}
              className="inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline"
            >
              Change how it behaves <ArrowRight className="size-3.5" />
            </Link>
          </CardContent>
        </Card>
      </div>

      {canViewActivity && <UnansweredQuestionsCard tenantId={tenantId} />}

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <BookOpen className="size-4 text-muted-foreground" />
            What your chatbot knows
          </CardTitle>
          <CardDescription>
            Your chatbot answers only from these sources. Keep them up to date and its answers
            stay right.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {!a ? (
            knowledgeBases.isLoading ? (
              <Skeleton className="h-12 w-full" />
            ) : (
              <p className="text-sm text-muted-foreground">
                {plural(knowledgeBases.data?.knowledge_bases.length ?? 0, "knowledge collection")}.
              </p>
            )
          ) : a.knowledge.ready + a.knowledge.processing + a.knowledge.failed === 0 ? (
            <p className="text-sm text-muted-foreground">
              Nothing yet. Add your website or a document so your chatbot has something to
              answer from.
            </p>
          ) : (
            <div className="grid gap-4 sm:grid-cols-4">
              <Fact label="Ready to use" value={a.knowledge.ready} />
              <Fact label="Web pages" value={a.knowledge.web_pages} />
              <Fact label="Files" value={a.knowledge.files} />
              <Fact
                label="Couldn't be read"
                value={a.knowledge.failed}
                tone={a.knowledge.failed > 0 ? "warn" : undefined}
                note={a.knowledge.processing > 0 ? `${a.knowledge.processing} still being read` : undefined}
              />
            </div>
          )}
          {a?.ingestion_tokens_this_month !== undefined && a.ingestion_tokens_this_month > 0 && (
            <p className="mt-4 text-sm text-muted-foreground">
              Reading your documents used{" "}
              <span className="font-medium text-foreground tabular-nums">
                {a.ingestion_tokens_this_month.toLocaleString()}
              </span>{" "}
              tokens this month. This doesn&rsquo;t count against your AI allowance.
            </p>
          )}
          <div className="mt-4 flex flex-wrap items-center justify-between gap-2 text-sm">
            <span className="text-muted-foreground">
              {a?.knowledge.last_added_at
                ? `Last added ${ago(a.knowledge.last_added_at)} ago`
                : " "}
            </span>
            <Link
              href={`${base}/knowledge-bases`}
              className="inline-flex items-center gap-1 font-medium text-primary hover:underline"
            >
              Manage knowledge <ArrowRight className="size-3.5" />
            </Link>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function Fact({
  label,
  value,
  tone,
  note,
}: {
  label: string;
  value: number | string;
  tone?: "warn";
  note?: string;
}) {
  return (
    <div>
      <p className="text-xs text-muted-foreground">{label}</p>
      <p
        className={`text-2xl font-semibold tabular-nums ${
          tone === "warn" ? "text-amber-700 dark:text-amber-400" : ""
        }`}
      >
        {typeof value === "number" ? value.toLocaleString() : value}
      </p>
      {note && <p className="text-xs text-muted-foreground">{note}</p>}
    </div>
  );
}

/**
 * One allowance, in words first and numbers second.
 *
 * `asPercent` exists for the monthly AI allowance, which is counted in
 * "tokens" -- a unit that means nothing to the person reading this. "3% of
 * this month's allowance" is a sentence they can act on; the exact figure
 * stays available as a tooltip for anyone who needs it.
 */
function UsageRow({
  label,
  used,
  limit,
  loading,
  asPercent = false,
  hint,
}: {
  label: string;
  used: number | null;
  limit: number | null;
  loading: boolean;
  asPercent?: boolean;
  hint?: string;
}) {
  if (loading) return <Skeleton className="h-10 w-full" />;
  const pct = used !== null && limit ? Math.min(100, Math.round((used / limit) * 100)) : null;
  const text =
    used === null
      ? "Not available right now"
      : limit === null
        ? asPercent
          ? "No monthly limit"
          : `${used.toLocaleString()} · no daily limit`
        : asPercent
          ? `${pct}% of this month's allowance`
          : `${used.toLocaleString()} of ${limit.toLocaleString()}`;
  return (
    <div title={asPercent && used !== null ? `${used.toLocaleString()} tokens` : undefined}>
      <div className="flex items-baseline justify-between gap-2 text-sm">
        <span>{label}</span>
        <span className="text-muted-foreground tabular-nums">{text}</span>
      </div>
      {pct !== null && <Progress value={pct} className="mt-1.5 h-1.5" />}
      {hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}
