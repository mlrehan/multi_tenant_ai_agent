"use client";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import type { PlatformActivity } from "@/lib/types";
import { ago, plural } from "./format";

type Status = "ok" | "warn" | "down";

function Row({ label, status, value }: { label: string; status: Status; value: string }) {
  return (
    <li className="flex items-center justify-between gap-3 py-2">
      <span className="flex items-center gap-2 text-sm">
        <span
          aria-hidden
          className={cn(
            "size-2 rounded-full",
            status === "ok" && "bg-emerald-500",
            status === "warn" && "bg-amber-500",
            status === "down" && "bg-destructive",
          )}
        />
        {label}
      </span>
      <span
        className={cn(
          "text-right text-xs",
          status === "down" ? "font-medium text-destructive" : "text-muted-foreground",
        )}
      >
        {value}
      </span>
    </li>
  );
}

/**
 * Service health and the two queues a person works through.
 *
 * The dependency rows are this API process's own probe -- the one `/readyz`
 * runs -- so "healthy" here means "the process that answered this page can
 * reach it", which is exactly what an operator can act on. The ingestion row
 * stands in for the worker, which the API cannot probe directly: a document
 * stuck in `processing` is how a dead worker becomes visible.
 */
export function HealthCard({
  activity,
  failed = false,
}: {
  activity: PlatformActivity | undefined;
  failed?: boolean;
}) {
  const dep = (name: string) => activity?.health.find((d) => d.name === name);
  const database = [dep("postgres_tenant"), dep("postgres_platform")];
  const databaseDown = database.some((d) => d && !d.healthy);
  const redis = dep("redis");

  return (
    <Card>
      <CardHeader>
        <CardTitle>Operations</CardTitle>
        <CardDescription>Service health and the queues that need people.</CardDescription>
      </CardHeader>
      <CardContent>
        {!activity && failed ? (
          <p className="text-sm text-muted-foreground">
            Service status is unavailable from this API.
          </p>
        ) : !activity ? (
          <Skeleton className="h-40 w-full" />
        ) : (
          <ul className="divide-y divide-border">
            <Row
              label="Database"
              status={databaseDown ? "down" : "ok"}
              value={databaseDown ? "Not responding" : "Healthy"}
            />
            <Row
              label="Redis"
              status={redis && !redis.healthy ? "down" : "ok"}
              value={redis && !redis.healthy ? "Not responding" : "Healthy"}
            />
            <Row
              label="Document ingestion"
              status={
                activity.documents_stuck > 0 ? "down" : activity.documents_failed > 0 ? "warn" : "ok"
              }
              value={
                activity.documents_stuck > 0
                  ? `${activity.documents_stuck} stuck`
                  : activity.documents_processing > 0
                    ? `${activity.documents_processing} processing`
                    : activity.documents_failed > 0
                      ? `${plural(activity.documents_failed, "failed document")}`
                      : "Idle"
              }
            />
            <Row
              label="Human handoff queue"
              status={activity.waiting_handoffs > 0 ? "warn" : "ok"}
              value={
                activity.waiting_handoffs > 0
                  ? `${activity.waiting_handoffs} waiting · oldest ${ago(activity.oldest_waiting_at) ?? "—"}`
                  : activity.handled_handoffs > 0
                    ? `${activity.handled_handoffs} with an agent`
                    : "Empty"
              }
            />
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
