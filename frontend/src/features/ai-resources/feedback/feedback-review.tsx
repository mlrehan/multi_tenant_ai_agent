"use client";

import { Globe, MessageSquareText, MonitorSmartphone, ThumbsDown, ThumbsUp } from "lucide-react";
import { useState } from "react";

import { EmptyState, ErrorState, TableSkeleton } from "@/components/shared/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { AnswerFeedbackItem, FeedbackFilters } from "@/features/ai-resources/api";
import { Markdown } from "@/features/ai-resources/chat/markdown";
import { cn } from "@/lib/utils";

export const PAGE_SIZE = 25;

/** Headline numbers. Satisfaction is helpful / all rated -- stated as a share
 *  of *ratings*, not of answers: most answers are never rated. */
export function FeedbackSummary({
  helpful,
  notHelpful,
}: {
  helpful: number;
  notHelpful: number;
}) {
  const total = helpful + notHelpful;
  const pct = (n: number) => (total ? `${Math.round((n / total) * 100)}%` : "—");
  const cards = [
    { label: "Ratings", value: total.toLocaleString(), hint: "answers rated so far", icon: MessageSquareText },
    { label: "Helpful", value: helpful.toLocaleString(), hint: `${pct(helpful)} of ratings`, icon: ThumbsUp },
    { label: "Not helpful", value: notHelpful.toLocaleString(), hint: `${pct(notHelpful)} of ratings`, icon: ThumbsDown },
    { label: "Satisfaction", value: pct(helpful), hint: "helpful ÷ all ratings", icon: ThumbsUp },
  ];
  return (
    <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      {cards.map((c) => (
        <Card key={c.label}>
          <CardContent className="pt-4">
            <div className="flex items-center gap-1.5 text-xs font-medium tracking-wide text-muted-foreground uppercase">
              <c.icon className="size-3.5" /> {c.label}
            </div>
            <div className="mt-1 text-2xl font-semibold tabular-nums">{c.value}</div>
            <div className="text-xs text-muted-foreground">{c.hint}</div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T | undefined;
  options: { value: T | undefined; label: string }[];
  onChange: (value: T | undefined) => void;
  label: string;
}) {
  return (
    <div role="group" aria-label={label} className="inline-flex rounded-lg border border-border p-0.5">
      {options.map((o) => (
        <button
          key={o.label}
          type="button"
          aria-pressed={value === o.value}
          onClick={() => onChange(o.value)}
          className={cn(
            "rounded-md px-2.5 py-1 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground",
            value === o.value && "bg-muted text-foreground",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function FeedbackFilterBar({
  filters,
  onChange,
  extra,
}: {
  filters: FeedbackFilters;
  onChange: (next: FeedbackFilters) => void;
  extra?: React.ReactNode;
}) {
  return (
    <div className="mb-3 flex flex-wrap items-center gap-2">
      <Segmented
        label="Rating"
        value={filters.rating}
        onChange={(rating) => onChange({ ...filters, rating, offset: 0 })}
        options={[
          { value: undefined, label: "All" },
          { value: "up", label: "Helpful" },
          { value: "down", label: "Not helpful" },
        ]}
      />
      <Segmented
        label="Where it was rated"
        value={filters.channel}
        onChange={(channel) => onChange({ ...filters, channel, offset: 0 })}
        options={[
          { value: undefined, label: "Everywhere" },
          { value: "website", label: "Website chatbot" },
          { value: "console", label: "Console" },
        ]}
      />
      {extra}
    </div>
  );
}

function RatingBadge({ rating }: { rating: "up" | "down" }) {
  return rating === "up" ? (
    <Badge variant="secondary" className="gap-1 text-emerald-700 dark:text-emerald-400">
      <ThumbsUp className="size-3" /> Helpful
    </Badge>
  ) : (
    <Badge variant="secondary" className="gap-1 text-rose-700 dark:text-rose-400">
      <ThumbsDown className="size-3" /> Not helpful
    </Badge>
  );
}

function Who({ item }: { item: AnswerFeedbackItem }) {
  if (item.channel === "website") {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
        <Globe className="size-3" /> Website visitor
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
      <MonitorSmartphone className="size-3" /> {item.author_email ?? "Team member (console)"}
    </span>
  );
}

function FeedbackDetail({
  item,
  onClose,
  showTenant,
}: {
  item: AnswerFeedbackItem | null;
  onClose: () => void;
  showTenant: boolean;
}) {
  return (
    <Dialog open={item !== null} onOpenChange={(open) => !open && onClose()}>
      {item && (
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle className="flex flex-wrap items-center gap-2">
              <RatingBadge rating={item.rating} />
              <span className="text-sm font-normal text-muted-foreground">
                {new Date(item.created_at).toLocaleString()}
              </span>
            </DialogTitle>
            <DialogDescription className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <Who item={item} />
              {showTenant && item.tenant_name && <span className="text-xs">Tenant: {item.tenant_name}</span>}
              {item.knowledge_base_name && (
                <span className="text-xs">Knowledge base: {item.knowledge_base_name}</span>
              )}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            {item.comment && (
              <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3">
                <p className="text-xs font-medium text-muted-foreground uppercase">Comment</p>
                <p className="mt-1 text-sm whitespace-pre-wrap break-words">{item.comment}</p>
              </div>
            )}
            <div>
              <p className="text-xs font-medium text-muted-foreground uppercase">Question</p>
              {/* What a person typed: shown as typed, never parsed. */}
              <p className="mt-1 text-sm whitespace-pre-wrap break-words">{item.question}</p>
            </div>
            <div>
              <p className="text-xs font-medium text-muted-foreground uppercase">Answer that was rated</p>
              <div className="mt-1 rounded-lg bg-muted/40 p-3">
                <Markdown text={item.answer} />
              </div>
            </div>
          </div>
        </DialogContent>
      )}
    </Dialog>
  );
}

/**
 * The ratings list shared by the tenant and platform screens.
 *
 * Newest first. A row shows enough to triage -- the verdict, the question,
 * who rated it and any comment -- and opening it shows the full answer that
 * was rated, rendered exactly as the reader saw it.
 */
export function FeedbackList({
  items,
  total,
  isLoading,
  error,
  filters,
  onPage,
  showTenant = false,
}: {
  items: AnswerFeedbackItem[] | undefined;
  total: number;
  isLoading: boolean;
  error: unknown;
  filters: FeedbackFilters;
  onPage: (offset: number) => void;
  showTenant?: boolean;
}) {
  const [open, setOpen] = useState<AnswerFeedbackItem | null>(null);
  const offset = filters.offset ?? 0;

  if (isLoading && !items) return <TableSkeleton rows={5} columns={showTenant ? 5 : 4} />;
  if (error) return <ErrorState error={error} resource="feedback" />;
  if (!items || items.length === 0) {
    return (
      <EmptyState
        icon={ThumbsUp}
        title={filters.rating || filters.channel || filters.tenantId ? "No ratings match" : "No ratings yet"}
        description={
          filters.rating || filters.channel || filters.tenantId
            ? "Try clearing a filter."
            : "Ratings appear here when someone presses 👍 or 👎 under an answer — in the website chatbot or the console."
        }
      />
    );
  }

  return (
    <>
      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-[130px]">Rating</TableHead>
                <TableHead>Question</TableHead>
                {/* On the platform view the tenant shares this cell rather than
                    taking its own column: a whole column for it pushed the table
                    past a laptop-width window. */}
                <TableHead>{showTenant ? "Tenant · rated by" : "Rated by"}</TableHead>
                {/* Below `lg` the comment column is dropped -- the platform's
                    table has a tenant column too, and six columns cannot fit a
                    narrow window without the whole page scrolling sideways. The
                    question cell then carries a comment marker instead, and the
                    full comment is always in the detail view. */}
                <TableHead className="hidden lg:table-cell">Comment</TableHead>
                <TableHead className="w-[90px]">When</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((item) => (
                <TableRow
                  key={item.id}
                  className="cursor-pointer"
                  onClick={() => setOpen(item)}
                  onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && setOpen(item)}
                  tabIndex={0}
                  aria-label={`${item.rating === "up" ? "Helpful" : "Not helpful"} rating: ${item.question}. Open details.`}
                >
                  <TableCell>
                    <RatingBadge rating={item.rating} />
                  </TableCell>
                  <TableCell className="max-w-[280px] min-w-[140px]">
                    <span className="line-clamp-2 text-sm whitespace-normal">{item.question}</span>
                    {item.comment && (
                      <span className="mt-0.5 inline-flex items-center gap-1 text-[0.7rem] text-amber-700 lg:hidden dark:text-amber-400">
                        <MessageSquareText className="size-3" /> Has a comment
                      </span>
                    )}
                  </TableCell>
                  {/* `whitespace-normal`: the shared cell is nowrap, and an email
                      or a full timestamp held on one line made the whole page
                      scroll sideways at narrow widths. */}
                  <TableCell className="max-w-[180px] whitespace-normal break-words">
                    {showTenant && (
                      <span className="block text-sm font-medium">{item.tenant_name ?? "—"}</span>
                    )}
                    <Who item={item} />
                  </TableCell>
                  <TableCell className="hidden max-w-[200px] lg:table-cell">
                    {item.comment ? (
                      <span className="line-clamp-2 text-sm whitespace-normal">{item.comment}</span>
                    ) : (
                      <span className="text-xs text-muted-foreground">—</span>
                    )}
                  </TableCell>
                  <TableCell className="text-xs whitespace-normal text-muted-foreground">
                    {new Date(item.created_at).toLocaleDateString()}
                    <br />
                    {new Date(item.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <div className="mt-3 flex items-center justify-between text-xs text-muted-foreground">
        <span>
          {offset + 1}–{Math.min(offset + items.length, total)} of {total}
        </span>
        <div className="flex gap-2">
          <Button size="sm" variant="outline" disabled={offset === 0} onClick={() => onPage(Math.max(0, offset - PAGE_SIZE))}>
            Previous
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={offset + items.length >= total}
            onClick={() => onPage(offset + PAGE_SIZE)}
          >
            Next
          </Button>
        </div>
      </div>

      <FeedbackDetail item={open} onClose={() => setOpen(null)} showTenant={showTenant} />
    </>
  );
}
