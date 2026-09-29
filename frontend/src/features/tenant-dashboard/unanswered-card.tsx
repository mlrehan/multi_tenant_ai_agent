"use client";

import Link from "next/link";
import { ArrowRight, CircleHelp } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useUnansweredQuestions } from "@/features/chatbot/hooks";
import { ago } from "@/features/platform/dashboard/format";

const SHOWN = 6;

/**
 * "Questions your chatbot couldn't answer" -- the improvement loop.
 *
 * A chatbot is only as good as what it was given, and the one thing an
 * administrator can't see unaided is *which* questions it failed. Most-asked
 * first, so the next page or document to add is obvious. The status behind
 * each row was recorded by the pipeline when it answered (nothing found, or
 * nothing cited) -- never guessed from the wording.
 */
export function UnansweredQuestionsCard({ tenantId }: { tenantId: string }) {
  const feed = useUnansweredQuestions(tenantId);
  const rows = feed.data?.questions ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <CircleHelp className="size-4 text-muted-foreground" />
          Questions your chatbot couldn&rsquo;t answer
        </CardTitle>
        <CardDescription>
          From the last {feed.data?.days ?? 30} days, most asked first. Add a page or document
          that answers them and your chatbot will start answering too.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {feed.isLoading ? (
          <Skeleton className="h-24 w-full" />
        ) : feed.error ? (
          <p className="text-sm text-muted-foreground">Couldn&rsquo;t load these right now.</p>
        ) : rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            Nothing here right now. Any question your chatbot can’t answer from your sources will show up here.
          </p>
        ) : (
          <ul className="divide-y">
            {rows.slice(0, SHOWN).map((row) => (
              <li key={row.question} className="flex items-start gap-3 py-2.5">
                <div className="min-w-0 flex-1">
                  <p className="break-words text-sm">&ldquo;{row.question}&rdquo;</p>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {row.no_sources
                      ? "Not covered by your sources"
                      : "Answered without using your sources"}
                    {" · "}last asked {ago(row.last_asked_at)} ago
                  </p>
                </div>
                {row.times_asked > 1 && (
                  <Badge variant="secondary" className="shrink-0">
                    Asked {row.times_asked}×
                  </Badge>
                )}
              </li>
            ))}
          </ul>
        )}
        <div className="mt-3 flex justify-end text-sm">
          <Link
            href={`/tenant/${tenantId}/knowledge-bases`}
            className="inline-flex items-center gap-1 font-medium text-primary hover:underline"
          >
            Add to what your chatbot knows <ArrowRight className="size-3.5" />
          </Link>
        </div>
      </CardContent>
    </Card>
  );
}
