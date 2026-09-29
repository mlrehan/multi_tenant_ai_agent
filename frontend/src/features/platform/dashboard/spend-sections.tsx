"use client";

import Link from "next/link";
import { useState } from "react";
import { AlertTriangle, Building2, Cpu } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/shared/states";
import { SpendMeter } from "@/components/shared/spend-meter";
import type { usePlatformOverview } from "@/features/platform/hooks";
import type { AlertLevel, CostSummary, TenantSpend } from "@/lib/types";
import { cn } from "@/lib/utils";
import { enforcedDailyLimit, usd } from "./format";

/** Input vs output tokens. Any remainder of the total is shown as unsplit
 *  rather than silently dropped: answers recorded before the split existed
 *  carry a total only, and that cannot be reconstructed. */
function TokenSplit({ input, output, total }: { input: number; output: number; total: number | null }) {
  const unsplit = total === null ? 0 : Math.max(0, total - input - output);
  return (
    <div className="grid grid-cols-3 gap-3 rounded-lg border p-3 text-sm">
      <div>
        <p className="text-xs text-muted-foreground">Input tokens</p>
        <p className="font-medium tabular-nums">{input.toLocaleString()}</p>
        <p className="text-[0.7rem] text-muted-foreground">Question, sources and search</p>
      </div>
      <div>
        <p className="text-xs text-muted-foreground">Output tokens</p>
        <p className="font-medium tabular-nums">{output.toLocaleString()}</p>
        <p className="text-[0.7rem] text-muted-foreground">Written answers</p>
      </div>
      <div>
        <p className="text-xs text-muted-foreground">Total</p>
        <p className="font-medium tabular-nums">{total === null ? "?" : total.toLocaleString()}</p>
        {unsplit > 0 && (
          <p className="text-[0.7rem] text-muted-foreground">
            {unsplit.toLocaleString()} recorded before the split was tracked
          </p>
        )}
      </div>
    </div>
  );
}

/** This month's estimated cost. Deliberately says "estimate": it is tokens
 *  times the prices entered on AI prices, not the provider's invoice. With no
 *  prices it says so and links there, instead of showing "$0.00". */
function CostCard({ costs }: { costs: CostSummary }) {
  const nothingPriced = costs.priced_tokens === 0;
  return (
    <div className="flex flex-col gap-2 rounded-lg border p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
          Estimated cost this month
        </p>
        <Link
          href="/platform/model-prices"
          className="text-xs font-medium text-primary underline-offset-4 hover:underline"
        >
          {nothingPriced ? "Enter prices" : "AI prices"}
        </Link>
      </div>
      {nothingPriced ? (
        <p className="text-sm text-muted-foreground">
          No usage is priced yet. Enter each model&rsquo;s price per million tokens to see what
          this month is costing.
        </p>
      ) : (
        <p className="text-2xl font-semibold tabular-nums">
          {usd(costs.total_usd)}
          <span className="ml-2 text-sm font-normal text-muted-foreground">{costs.currency}</span>
        </p>
      )}
      {costs.unpriced_tokens > 0 && (
        <p className="text-xs text-amber-700 dark:text-amber-400">
          {costs.unpriced_tokens.toLocaleString()} tokens aren&rsquo;t priced (no price entered,
          or recorded before models were tracked) and aren&rsquo;t in this figure.
        </p>
      )}
      {costs.by_model.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[420px] text-sm">
            <thead className="text-left text-xs text-muted-foreground">
              <tr>
                <th className="py-1 pr-3 font-medium">Model</th>
                <th className="py-1 pr-3 font-medium">Used for</th>
                <th className="py-1 pr-3 text-right font-medium">Tokens</th>
                <th className="py-1 text-right font-medium">Cost</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {costs.by_model.map((m) => (
                <tr key={`${m.model ?? "unknown"}-${m.kind}`}>
                  <td className="py-1.5 pr-3 font-mono text-xs">
                    {m.model ?? <span className="font-sans text-muted-foreground">Not recorded</span>}
                  </td>
                  <td className="py-1.5 pr-3">{m.kind === "chat" ? "Answers" : "Embeddings"}</td>
                  <td className="py-1.5 pr-3 text-right tabular-nums">{m.tokens.toLocaleString()}</td>
                  <td className="py-1.5 text-right tabular-nums">
                    {m.unpriced_tokens === m.tokens ? (
                      <span className="text-muted-foreground">Not priced</span>
                    ) : (
                      <>
                        {usd(m.cost_usd)}
                        {m.unpriced_tokens > 0 && (
                          <span className="block text-[0.7rem] text-muted-foreground">
                            {m.unpriced_tokens.toLocaleString()} unpriced
                          </span>
                        )}
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/** "80%" / "95%" / "Used up" beside a figure, from the server's alert level. */
function LevelChip({ level }: { level: AlertLevel | undefined }) {
  if (level === null || level === undefined) return null;
  return (
    <span
      className={cn(
        "ml-1.5 inline-block rounded px-1.5 py-0.5 text-[0.7rem] font-medium",
        level === 100
          ? "bg-destructive/10 text-destructive"
          : level >= 90
            ? "bg-amber-500/10 text-amber-700 dark:text-amber-400"
            : "bg-muted text-muted-foreground",
      )}
    >
      {level === 100 ? "Used up" : `${level}%`}
    </span>
  );
}

type Overview = ReturnType<typeof usePlatformOverview>;

/** Where this month's tokens went, by what paid for them.
 *
 *  The platform default model is shown *first* and on its own card, because
 *  it is where the traffic actually is: every website chatbot answer and every
 *  Ask-panel question without a named assistant uses it, and it belongs to no
 *  budgeted configuration. Showing only the budgeted providers -- as this
 *  panel used to -- displayed "0 / 5,000,000" for a configuration nobody's
 *  answers resolved, while the real spend sat in a footnote. */
export function ProviderSpendSection({ overview }: { overview: Overview }) {
  const data = overview.data;
  return (
    <section>
      <div className="mb-3 flex items-center gap-2">
        <Cpu className="size-4 text-muted-foreground" />
        <h2 className="text-sm font-semibold">AI spend this month</h2>
      </div>

      {overview.isLoading && <Skeleton className="h-28 w-full" />}
      {overview.error && (
        <ErrorState error={overview.error} resource="provider spend" scope="platform" />
      )}
      {data && (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          <SpendMeter
            label="Platform default model"
            used={data.unattributed_tokens}
            limit={null}
            remaining={null}
            note="Website chatbot and console answers. No per-model budget; each tenant's monthly allowance still applies."
          />
          {/* Absent against an older API, which metered no ingestion at all --
              a card reading 0 would claim nothing was spent. */}
          {data.ingestion_tokens !== undefined && (
            <SpendMeter
              label="Reading documents (embeddings)"
              used={data.ingestion_tokens}
              limit={null}
              remaining={null}
              note="Uploads, crawled pages, retries and re-syncs. Metered from when this was introduced; not counted against any tenant's allowance."
            />
          )}
          {data.providers.map((p) => (
            <SpendMeter
              key={p.provider}
              label={`${p.provider} · assigned models`}
              used={p.used_tokens}
              limit={p.total_tokens}
              remaining={p.remaining_tokens}
              runningLow={p.running_low}
              note={`${p.model_count} configuration(s)${p.has_unbudgeted ? ", some unbudgeted" : ""} · budget summed across tenant grants`}
            />
          ))}
          {data.providers.length === 0 && (
            <p className="self-center text-sm text-muted-foreground">
              No budgeted model configurations.{" "}
              <Link href="/platform/model-configurations" className="underline underline-offset-4">
                Add one
              </Link>
              .
            </p>
          )}
        </div>
      )}
      {/* Its own full-width block: a table of models does not fit a meter card. */}
      {data?.costs && (
        <div className="mt-4">
          <CostCard costs={data.costs} />
        </div>
      )}
    </section>
  );
}

/** Per-tenant tokens and messages, with a drill-down.
 *
 *  Tenants running low are returned first by the server, so the rows that need
 *  attention are visible without scrolling a long table. */
export function TenantSpendSection({ overview }: { overview: Overview }) {
  const [detail, setDetail] = useState<TenantSpend | null>(null);
  const lowCount = overview.data?.tenants_running_low ?? 0;
  const threshold = Math.round((overview.data?.low_remaining_fraction ?? 0.1) * 100);

  return (
    <section>
      <div className="mb-3 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Building2 className="size-4 text-muted-foreground" />
          <h2 className="text-sm font-semibold">Tenant usage</h2>
        </div>
        {lowCount > 0 && (
          <span className="flex items-center gap-1 rounded-md bg-destructive/10 px-2 py-1 text-xs font-medium text-destructive">
            <AlertTriangle className="size-3.5" />
            {lowCount} under {threshold}% remaining
          </span>
        )}
      </div>

      {overview.isLoading && <Skeleton className="h-32 w-full" />}
      {overview.data && overview.data.tenants.length === 0 && (
        <p className="text-sm text-muted-foreground">No tenants yet.</p>
      )}
      {overview.data && overview.data.tenants.length > 0 && (
        <div className="overflow-x-auto rounded-xl border">
          <table className="w-full text-sm">
            <thead className="border-b bg-muted/40 text-left text-xs tracking-wide text-muted-foreground uppercase">
              <tr>
                <th className="px-3 py-2 font-medium">Tenant</th>
                <th className="px-3 py-2 font-medium">Tokens this month</th>
                <th className="px-3 py-2 font-medium">Remaining</th>
                <th className="px-3 py-2 font-medium">Messages today</th>
                {overview.data.costs && (
                  <th className="px-3 py-2 font-medium">Est. cost</th>
                )}
              </tr>
            </thead>
            <tbody className="divide-y">
              {overview.data.tenants.map((t) => (
                <tr key={t.tenant_id} className={t.running_low ? "bg-destructive/5" : undefined}>
                  <td className="px-3 py-2">
                    {/* A button, not a link: this opens the breakdown in place.
                        The tenant admin screens are a different journey and are
                        reachable from Platform to Tenants. */}
                    <button
                      type="button"
                      onClick={() => setDetail(t)}
                      className="text-left font-medium underline-offset-4 hover:underline"
                    >
                      {t.display_name}
                    </button>
                    <p className="font-mono text-xs text-muted-foreground">{t.slug}</p>
                  </td>
                  <td className="px-3 py-2 tabular-nums">
                    {t.used_tokens === null ? "?" : t.used_tokens.toLocaleString()}
                    <span className="text-muted-foreground">
                      {t.max_tokens_per_month === null
                        ? " / no limit"
                        : ` / ${t.max_tokens_per_month.toLocaleString()}`}
                    </span>
                    <LevelChip level={t.token_alert_level} />
                  </td>
                  <td className="px-3 py-2 tabular-nums">
                    <span className={t.running_low ? "font-medium text-destructive" : undefined}>
                      {t.remaining_tokens === null ? "—" : t.remaining_tokens.toLocaleString()}
                    </span>
                  </td>
                  <td className="px-3 py-2 tabular-nums">
                    <button
                      type="button"
                      onClick={() => setDetail(t)}
                      className="text-left underline-offset-4 hover:underline"
                    >
                      {t.used_messages_today === null
                        ? "?"
                        : t.used_messages_today.toLocaleString()}
                      <span className="text-muted-foreground">
                        {enforcedDailyLimit(t) === null
                          ? " / no limit"
                          : ` / ${enforcedDailyLimit(t)?.toLocaleString()}`}
                      </span>
                      <LevelChip level={t.message_alert_level} />
                      {/* The tenant has set itself below the platform ceiling:
                          say so, or "50" looks like a mistake beside a plan of 100. */}
                      {t.max_messages_per_day !== null &&
                        t.effective_messages_per_day != null &&
                        t.effective_messages_per_day < t.max_messages_per_day && (
                          <span className="block text-[0.7rem] text-muted-foreground">
                            tenant-set · plan allows {t.max_messages_per_day.toLocaleString()}
                          </span>
                        )}
                    </button>
                  </td>
                  {overview.data?.costs && (
                    <td className="px-3 py-2 tabular-nums">
                      {t.cost_usd === undefined
                        ? "—"
                        : Number(t.cost_usd) === 0 && (t.unpriced_tokens ?? 0) > 0
                          ? <span className="text-muted-foreground">Not priced</span>
                          : usd(t.cost_usd)}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Dialog open={detail !== null} onOpenChange={(open) => !open && setDetail(null)}>
        <DialogContent className="sm:max-w-2xl">
          {detail && (
            <>
              <DialogHeader>
                <DialogTitle>{detail.display_name}</DialogTitle>
                <DialogDescription className="font-mono text-xs">{detail.slug}</DialogDescription>
              </DialogHeader>

              <div className="grid gap-4 sm:grid-cols-2">
                <SpendMeter
                  label="Tokens this month"
                  used={detail.used_tokens}
                  limit={detail.max_tokens_per_month}
                  remaining={detail.remaining_tokens}
                  runningLow={detail.running_low}
                />
                <SpendMeter
                  label="Messages today"
                  unit="messages"
                  used={detail.used_messages_today}
                  limit={enforcedDailyLimit(detail)}
                  remaining={detail.remaining_messages_today}
                />
              </div>

              {detail.input_tokens != null && detail.output_tokens != null && (
                <TokenSplit
                  input={detail.input_tokens}
                  output={detail.output_tokens}
                  total={detail.used_tokens}
                />
              )}
              {detail.ingestion_tokens !== undefined && (
                <p className="text-sm text-muted-foreground">
                  Reading documents this month:{" "}
                  <span className="font-medium text-foreground tabular-nums">
                    {detail.ingestion_tokens.toLocaleString()} tokens
                  </span>{" "}
                  (embeddings; not part of the allowance above).
                </p>
              )}

              <div>
                <h3 className="mb-2 text-sm font-medium">By model</h3>
                {detail.models.length === 0 ? (
                  <p className="text-sm text-muted-foreground">
                    No model configurations granted to this tenant. Their answers use the
                    platform default, which has no per-model budget.
                  </p>
                ) : (
                  <ul className="divide-y rounded-lg border">
                    {detail.models.map((m) => (
                      <li
                        key={m.model_configuration_id}
                        className="flex items-center justify-between gap-3 px-3 py-2 text-sm"
                      >
                        <div className="min-w-0">
                          <p className="truncate font-medium">{m.model_name}</p>
                          <p className="text-xs text-muted-foreground">{m.provider}</p>
                        </div>
                        <p className="shrink-0 tabular-nums">
                          {m.used_tokens === null ? "?" : m.used_tokens.toLocaleString()}
                          <span className="text-muted-foreground">
                            {m.token_budget_per_month === null
                              ? " / no limit"
                              : ` / ${m.token_budget_per_month.toLocaleString()}`}
                          </span>
                        </p>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div className="flex justify-end gap-2">
                <Button variant="outline" nativeButton={false} render={<Link href="/platform/entitlements" />}>
                  Adjust limits
                </Button>
              </div>
            </>
          )}
        </DialogContent>
      </Dialog>
    </section>
  );
}
