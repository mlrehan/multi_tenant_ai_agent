"use client";

import { useState } from "react";
import { AlertTriangle, CheckCircle2, Clock, Copy, Globe, Mail, RefreshCw } from "lucide-react";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  useChatWidgets,
  useSetChatWidgetStatus,
  useUpdateChatWidget,
} from "@/features/ai-resources/hooks";
import { useTenantPlan } from "@/features/chatbot/hooks";
import { ago } from "@/features/platform/dashboard/format";
import { isApiError } from "@/lib/api-client";
import type { ChatWidget } from "@/lib/types";

import { installState, pendingRefusedOrigin, toOrigin } from "./install-status";

async function copy(text: string, done: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast.success(done);
  } catch {
    toast.error("Couldn't copy. Select the text and copy it yourself.");
  }
}

/** Plain-English instructions for whoever maintains the site. Written for a
 *  developer who has never heard of this product and was forwarded an email. */
function developerMessage(widget: ChatWidget): string {
  return [
    "Hi,",
    "",
    "Please add our website chatbot to the site. It's one line of code:",
    "",
    widget.embed_snippet,
    "",
    "Where: just before the closing </body> tag, on every page where the chatbot should appear (usually a site-wide footer or layout template is easiest).",
    "",
    `It will only appear on these addresses: ${widget.allowed_origins.join(", ")}. If the site runs on another address (for example with or without "www", or a staging domain), tell me and I'll add it — otherwise the chatbot stays hidden there.`,
    "",
    "No other setup, account or keys are needed. The code is not secret.",
    "",
    "Thanks!",
  ].join("\n");
}

/**
 * "On your website" -- everything needed to get the chatbot onto a site and to
 * know whether it is actually there.
 *
 * The install check answers the question an administrator otherwise has no
 * way to answer: *did my web developer actually do it?* It reads what the
 * public widget endpoint recorded, so "Installed" means a real browser loaded
 * the chatbot from a listed address, not that the code was copied.
 */
export function WebsiteInstallSection({
  tenantId,
  canManage,
}: {
  tenantId: string;
  canManage: boolean | undefined;
}) {
  const widgets = useChatWidgets(tenantId);
  const list = widgets.data?.chat_widgets ?? [];
  if (widgets.isLoading || list.length === 0) return null;

  return (
    <Card className="mb-4">
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <Globe className="size-4 text-primary" /> On your website
            </CardTitle>
            <CardDescription>
              Put your chatbot on your website, and check that it&rsquo;s really there.
            </CardDescription>
          </div>
          <Button
            size="sm"
            variant="outline"
            disabled={widgets.isFetching}
            onClick={() => void widgets.refetch()}
          >
            <RefreshCw className={widgets.isFetching ? "animate-spin" : undefined} />
            Check again
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-6">
        {list.map((widget) => (
          <WidgetInstall
            key={widget.id}
            tenantId={tenantId}
            widget={widget}
            canManage={canManage}
            showName={list.length > 1}
          />
        ))}
      </CardContent>
    </Card>
  );
}

function StatusLine({ widget }: { widget: ChatWidget }) {
  const state = installState(widget);
  switch (state) {
    case "unknown":
      return null;
    case "off":
      return (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Badge variant="secondary">Turned off</Badge>
          Hidden on every website until you turn it back on.
        </p>
      );
    case "installed":
      return (
        <p className="flex flex-wrap items-center gap-2 text-sm">
          <Badge className="bg-emerald-600 text-white hover:bg-emerald-600">
            <CheckCircle2 /> Installed
          </Badge>
          <span className="text-muted-foreground">
            Last opened on <strong className="text-foreground">{widget.last_seen_origin}</strong>{" "}
            {ago(widget.last_seen_at ?? null)} ago.
          </span>
        </p>
      );
    case "not-seen-recently":
      return (
        <p className="flex flex-wrap items-center gap-2 text-sm">
          <Badge variant="outline" className="border-amber-500 text-amber-700 dark:text-amber-400">
            <Clock /> Not seen recently
          </Badge>
          <span className="text-muted-foreground">
            Last opened on {widget.last_seen_origin} {ago(widget.last_seen_at ?? null)} ago. If
            your website changed, the code may have been removed.
          </span>
        </p>
      );
    case "not-installed":
      return (
        <p className="flex flex-wrap items-center gap-2 text-sm">
          <Badge variant="outline">Not installed yet</Badge>
          <span className="text-muted-foreground">
            We haven&rsquo;t seen it on your website yet. Once the code is added, open your site
            and press <strong>Check again</strong>.
          </span>
        </p>
      );
  }
}

function WidgetInstall({
  tenantId,
  widget,
  canManage,
  showName,
}: {
  tenantId: string;
  widget: ChatWidget;
  canManage: boolean | undefined;
  showName: boolean;
}) {
  const update = useUpdateChatWidget(tenantId);
  const setStatus = useSetChatWidgetStatus(tenantId);
  const plan = useTenantPlan(tenantId);
  // The server refuses a new cap above the plan's ceiling; the tenant's own
  // (possibly lower) daily limit still applies at answer time.
  const ceiling = plan.data?.max_messages_per_day ?? null;
  const enforced = plan.data?.effective_daily_message_limit ?? null;
  const [editing, setEditing] = useState(false);
  const [origins, setOrigins] = useState(widget.allowed_origins.join("\n"));
  const [limit, setLimit] = useState(String(widget.daily_question_limit));
  const refused = pendingRefusedOrigin(widget);

  async function saveSites(nextOrigins: string[], nextLimit: number, done: string) {
    try {
      await update.mutateAsync({
        widgetId: widget.id,
        name: widget.name,
        allowed_origins: nextOrigins,
        daily_question_limit: nextLimit,
      });
      toast.success(done);
      return true;
    } catch (error) {
      toast.error(isApiError(error) ? error.message : "Could not save.");
      return false;
    }
  }

  async function submitEdit(event: React.FormEvent) {
    event.preventDefault();
    const parsed = origins
      .split(/[\n,]/)
      .map((o) => o.trim())
      .filter(Boolean);
    const bad = parsed.filter((o) => toOrigin(o) === null);
    if (parsed.length === 0) {
      toast.error("Add at least one website address.");
      return;
    }
    if (bad.length > 0) {
      toast.error(`“${bad[0]}” isn't a website address. Include https://, e.g. https://www.example.co.uk`);
      return;
    }
    const n = Number(limit);
    if (!Number.isInteger(n) || n < 1) {
      toast.error("Questions per day must be a whole number of at least 1.");
      return;
    }
    if (ceiling !== null && n > ceiling && n !== widget.daily_question_limit) {
      toast.error(`Questions per day can be at most ${ceiling.toLocaleString()}, your plan's daily limit.`);
      return;
    }
    if (await saveSites(parsed, n, "Saved.")) setEditing(false);
  }

  return (
    <div className="space-y-4">
      {showName && <h3 className="text-sm font-semibold">{widget.name}</h3>}

      <StatusLine widget={widget} />

      {refused && widget.status === "active" && (
        <Alert>
          <AlertTriangle className="size-4" />
          <AlertTitle>A page on {refused} tried to show your chatbot</AlertTitle>
          <AlertDescription>
            <p>
              That address isn&rsquo;t in your list, so the chatbot stayed hidden there
              {widget.last_refused_at ? ` (${ago(widget.last_refused_at)} ago)` : ""}. If it&rsquo;s
              your website, allow it.
            </p>
            {canManage && (
              <Button
                size="sm"
                className="mt-2"
                disabled={update.isPending}
                onClick={() =>
                  void saveSites(
                    [...widget.allowed_origins, refused],
                    widget.daily_question_limit,
                    `${refused} added. The chatbot will appear there now.`,
                  )
                }
              >
                Allow {refused}
              </Button>
            )}
          </AlertDescription>
        </Alert>
      )}

      <div className="space-y-1.5">
        <div className="flex items-center justify-between gap-2">
          <Label>Where it can appear</Label>
          {canManage && (
            <Button
              size="xs"
              variant="ghost"
              onClick={() => {
                // Re-read on open: a quick "Allow" since mount changed the list.
                setOrigins(widget.allowed_origins.join("\n"));
                setLimit(String(widget.daily_question_limit));
                setEditing((v) => !v);
              }}
            >
              {editing ? "Cancel" : "Change"}
            </Button>
          )}
        </div>
        {editing ? (
          <form onSubmit={submitEdit} className="space-y-3 rounded-lg border p-3">
            <div className="space-y-1.5">
              <Label htmlFor={`sites-${widget.id}`} className="text-xs">
                Website addresses, one per line
              </Label>
              <Textarea
                id={`sites-${widget.id}`}
                rows={3}
                value={origins}
                onChange={(e) => setOrigins(e.target.value)}
              />
              <p className="text-xs text-muted-foreground">
                Include <code>https://</code>. List <code>www.</code> and non-<code>www</code>{" "}
                separately if your site uses both. A page address is shortened to the site, since
                a browser only tells us the site.
              </p>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={`limit-${widget.id}`} className="text-xs">
                Most questions it answers a day on these sites
              </Label>
              <Input
                id={`limit-${widget.id}`}
                type="number"
                min={1}
                // No browser-side max when the stored cap is already above the
                // plan (set before the rule existed): the browser would block
                // saving an unrelated change, like adding a website, with
                // "must be ≤ 100". `submitEdit` enforces the ceiling for a
                // *new* value, which is exactly the server's rule.
                max={
                  ceiling !== null && widget.daily_question_limit <= ceiling
                    ? ceiling
                    : undefined
                }
                value={limit}
                onChange={(e) => setLimit(e.target.value)}
                className="w-40"
              />
              {ceiling !== null && (
                <p className="text-xs text-muted-foreground">
                  Up to {ceiling.toLocaleString()} — your plan&rsquo;s daily limit, shared by all
                  your chatbots and your team&rsquo;s questions.
                  {enforced !== null &&
                    enforced < ceiling &&
                    ` Your own daily limit of ${enforced.toLocaleString()} (Handoff & limits) is lower, so it's reached first.`}
                </p>
              )}
            </div>
            <Button type="submit" size="sm" disabled={update.isPending}>
              {update.isPending ? "Saving…" : "Save"}
            </Button>
          </form>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {widget.allowed_origins.map((o) => (
              <Badge key={o} variant="secondary" className="font-normal">
                {o}
              </Badge>
            ))}
            <span className="text-xs text-muted-foreground self-center">
              {/* The lower cap wins at answer time, so that is the number
                  shown -- "500 a day" on a plan of 50 is a promise nobody keeps. */}
              {enforced !== null && enforced < widget.daily_question_limit
                ? `· up to ${enforced.toLocaleString()} questions a day (your organisation's daily limit)`
                : `· up to ${widget.daily_question_limit.toLocaleString()} questions a day`}
            </span>
          </div>
        )}
      </div>

      <div className="space-y-1.5">
        <Label>Add this to your website</Label>
        <p className="text-xs text-muted-foreground">
          Paste it just before <code>&lt;/body&gt;</code> on every page, or send it to whoever
          looks after your website. It isn&rsquo;t secret.
        </p>
        <pre className="overflow-x-auto whitespace-pre-wrap break-all rounded-md bg-muted p-3 text-xs">
          {widget.embed_snippet}
        </pre>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" onClick={() => void copy(widget.embed_snippet, "Code copied.")}>
            <Copy /> Copy code
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() =>
              void copy(
                developerMessage(widget),
                "Instructions copied. Paste them into an email to your web developer.",
              )
            }
          >
            <Mail /> Copy instructions for your web developer
          </Button>
          {canManage && (
            <Button
              size="sm"
              variant="ghost"
              disabled={setStatus.isPending}
              onClick={() =>
                setStatus
                  .mutateAsync({ widgetId: widget.id, enabled: widget.status !== "active" })
                  .then(() =>
                    toast.success(
                      widget.status === "active"
                        ? "Hidden on your website."
                        : "Showing on your website again.",
                    ),
                  )
                  .catch((error: unknown) =>
                    toast.error(isApiError(error) ? error.message : "Could not change it."),
                  )
              }
            >
              {widget.status === "active" ? "Hide from website" : "Show on website"}
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
