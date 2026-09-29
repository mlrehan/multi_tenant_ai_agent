"use client";

import Link from "next/link";
import { CheckCircle2, ChevronRight, Circle } from "lucide-react";

import { cn } from "@/lib/utils";

export interface SetupStep {
  key: string;
  title: string;
  /** What doing it achieves, in the admin's terms -- not what the screen is. */
  why: string;
  href: string;
  done: boolean;
}

/**
 * "Get your chatbot ready" -- the first-run path, as a checklist.
 *
 * A first-time administrator's real question is "what do I do next?", and a
 * dashboard of zeros does not answer it. Each step links straight to the
 * screen that completes it, and the list disappears once everything is done:
 * a permanent checklist becomes furniture nobody reads.
 */
export function SetupChecklist({ steps }: { steps: SetupStep[] }) {
  const done = steps.filter((s) => s.done).length;
  if (done === steps.length) return null;
  const next = steps.find((s) => !s.done);

  return (
    <section aria-label="Get your chatbot ready" className="rounded-xl border bg-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold">Get your chatbot ready</h2>
          <p className="text-xs text-muted-foreground">
            {done} of {steps.length} done
            {next && <> · Next: {next.title.toLowerCase()}</>}
          </p>
        </div>
        <div className="h-1.5 w-40 overflow-hidden rounded-full bg-muted" aria-hidden>
          <div
            className="h-full rounded-full bg-primary transition-all"
            style={{ width: `${Math.round((done / steps.length) * 100)}%` }}
          />
        </div>
      </div>
      <ol className="divide-y">
        {steps.map((step, i) => (
          <li key={step.key}>
            <Link
              href={step.href}
              className={cn(
                "flex items-center gap-3 px-4 py-3 transition-colors hover:bg-muted/40",
                step === next && "bg-primary/5",
              )}
            >
              {step.done ? (
                <CheckCircle2 className="size-5 shrink-0 text-emerald-600 dark:text-emerald-400" />
              ) : (
                <Circle className="size-5 shrink-0 text-muted-foreground" />
              )}
              <div className="min-w-0 flex-1">
                <p className={cn("text-sm font-medium", step.done && "text-muted-foreground line-through")}>
                  {i + 1}. {step.title}
                </p>
                {!step.done && <p className="text-xs text-muted-foreground">{step.why}</p>}
              </div>
              {!step.done && <ChevronRight className="size-4 shrink-0 text-muted-foreground" />}
            </Link>
          </li>
        ))}
      </ol>
    </section>
  );
}
