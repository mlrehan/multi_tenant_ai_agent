"use client";

import { useState } from "react";
import { CircleDollarSign, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { PageHeader } from "@/components/shared/page-header";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/shared/states";
import { useDeleteModelPrice, useModelPrices, useSetModelPrice } from "@/features/platform/hooks";
import { isApiError } from "@/lib/api-client";
import type { ModelInUse, ModelPrice } from "@/lib/types";

/** Prices are stored with 6 decimal places; a trailing run of zeros is noise. */
function perMillion(value: string): string {
  const n = Number(value);
  return Number.isFinite(n) ? `$${n.toLocaleString(undefined, { maximumFractionDigits: 6 })}` : value;
}

/** Effective times are shown in UTC, labelled: costs and the month they fall
 *  in are UTC, and "06:00" in a browser six hours ahead would read as a
 *  different moment from the one the price actually starts. */
function utc(iso: string): string {
  return `${new Intl.DateTimeFormat("en-GB", {
    timeZone: "UTC",
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(iso))} UTC`;
}

function startOfThisMonthUtc(): Date {
  const now = new Date();
  return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1));
}

/**
 * Platform → AI prices.
 *
 * What each model costs per million tokens, entered by the operator. Nothing
 * here is assumed: a model with no price shows its usage as "not priced" on
 * the dashboard rather than as free. Entries are never edited -- a price
 * change is a new entry from a later moment, so a past month keeps the cost
 * it was reported with.
 */
export default function ModelPricesPage() {
  const catalogue = useModelPrices();
  const [editing, setEditing] = useState<{ model: string; kind: string; firstPrice: boolean } | null>(
    null,
  );
  const remove = useDeleteModelPrice();
  const data = catalogue.data;
  const unpricedModels = (data?.models_in_use ?? []).filter((m) => m.current === null);

  return (
    <div className="space-y-6">
      <PageHeader
        title="AI prices"
        description="What each model costs per million tokens. The dashboard's cost is an estimate from these prices and the usage it records — not your provider's invoice."
        actions={
          <Button size="sm" onClick={() => setEditing({ model: "", kind: "chat", firstPrice: true })}>
            <Plus />
            Add a price
          </Button>
        }
      />

      {catalogue.isLoading && <TableSkeleton rows={3} columns={4} />}
      {catalogue.error && <ErrorState error={catalogue.error} resource="AI prices" scope="platform" />}

      {data && unpricedModels.length > 0 && (
        <Alert>
          <CircleDollarSign className="size-4" />
          <AlertTitle>
            {unpricedModels.length === 1
              ? `${unpricedModels[0].model} has no price`
              : `${unpricedModels.length} models in use have no price`}
          </AlertTitle>
          <AlertDescription>
            Their usage is counted but not costed, so this month&rsquo;s estimate is incomplete.
            Enter the price from your provider&rsquo;s price list.
          </AlertDescription>
        </Alert>
      )}

      {data && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Models used this month</CardTitle>
            <CardDescription>
              Every model that this month&rsquo;s usage paid for, with the price in force now.
            </CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            {data.models_in_use.length === 0 ? (
              <p className="px-6 pb-6 text-sm text-muted-foreground">
                No usage with a recorded model yet this month.
              </p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Model</TableHead>
                    <TableHead>Used for</TableHead>
                    <TableHead className="text-right">Tokens this month</TableHead>
                    <TableHead>Price per million tokens</TableHead>
                    <TableHead className="text-right" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.models_in_use.map((m) => (
                    <InUseRow
                      key={`${m.model}-${m.kind}`}
                      model={m}
                      onSet={() =>
                        setEditing({ model: m.model, kind: m.kind, firstPrice: m.current === null })
                      }
                    />
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      )}

      {data && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Price history</CardTitle>
            <CardDescription>
              Each cost is worked out with the entry in force when the usage happened. Delete an
              entry only if it was a mistake; a price change is a new entry.
            </CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            {data.prices.length === 0 ? (
              <div className="px-6 pb-6">
                <EmptyState
                  icon={CircleDollarSign}
                  title="No prices yet"
                  description="Until you add one, the dashboard counts usage but shows no cost."
                />
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Model</TableHead>
                    <TableHead>Input</TableHead>
                    <TableHead>Output</TableHead>
                    <TableHead>From</TableHead>
                    <TableHead className="text-right" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.prices.map((p) => (
                    <HistoryRow
                      key={p.id}
                      price={p}
                      busy={remove.isPending}
                      onDelete={() =>
                        remove
                          .mutateAsync(p.id)
                          .then(() => toast.success("Price entry deleted."))
                          .catch((error: unknown) =>
                            toast.error(isApiError(error) ? error.message : "Could not delete it."),
                          )
                      }
                    />
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      )}

      <Dialog open={editing !== null} onOpenChange={(open) => !open && setEditing(null)}>
        {editing && <PriceDialog {...editing} onDone={() => setEditing(null)} />}
      </Dialog>
    </div>
  );
}

function InUseRow({ model, onSet }: { model: ModelInUse; onSet: () => void }) {
  return (
    <TableRow>
      <TableCell className="font-mono text-sm">{model.model}</TableCell>
      <TableCell>
        <Badge variant="secondary">{model.kind === "chat" ? "Answers" : "Embeddings"}</Badge>
      </TableCell>
      <TableCell className="text-right tabular-nums">
        {model.tokens_this_month.toLocaleString()}
      </TableCell>
      <TableCell>
        {model.current ? (
          <span className="tabular-nums">
            {perMillion(model.current.input_usd_per_million)} in
            {model.kind === "chat" && <> · {perMillion(model.current.output_usd_per_million)} out</>}
          </span>
        ) : (
          <span className="font-medium text-amber-700 dark:text-amber-400">Not priced</span>
        )}
      </TableCell>
      <TableCell className="text-right">
        <Button size="xs" variant="outline" onClick={onSet}>
          {model.current ? "Change price" : "Set price"}
        </Button>
      </TableCell>
    </TableRow>
  );
}

function HistoryRow({
  price,
  busy,
  onDelete,
}: {
  price: ModelPrice;
  busy: boolean;
  onDelete: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  return (
    <TableRow>
      <TableCell className="font-mono text-sm">{price.model_name}</TableCell>
      <TableCell className="tabular-nums">{perMillion(price.input_usd_per_million)}</TableCell>
      <TableCell className="tabular-nums">{perMillion(price.output_usd_per_million)}</TableCell>
      <TableCell className="tabular-nums">
        {utc(price.effective_from)}
      </TableCell>
      <TableCell className="text-right">
        {confirming ? (
          <span className="inline-flex gap-2">
            <Button size="xs" variant="destructive" disabled={busy} onClick={onDelete}>
              Delete
            </Button>
            <Button size="xs" variant="outline" onClick={() => setConfirming(false)}>
              Keep
            </Button>
          </span>
        ) : (
          <Button
            size="xs"
            variant="ghost"
            aria-label={`Delete the ${price.model_name} price entry`}
            onClick={() => setConfirming(true)}
          >
            <Trash2 />
          </Button>
        )}
      </TableCell>
    </TableRow>
  );
}

function PriceDialog({
  model,
  kind,
  firstPrice,
  onDone,
}: {
  model: string;
  kind: string;
  firstPrice: boolean;
  onDone: () => void;
}) {
  const save = useSetModelPrice();
  const [name, setName] = useState(model);
  const [input, setInput] = useState("");
  const [output, setOutput] = useState(kind === "embedding" ? "0" : "");
  // A model's first price usually needs to cover usage already recorded this
  // month; a change to an existing price usually applies from now.
  const [from, setFrom] = useState<"month" | "now">(firstPrice ? "month" : "now");
  const monthStart = startOfThisMonthUtc();

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    try {
      await save.mutateAsync({
        model_name: name.trim(),
        input_usd_per_million: input.trim(),
        output_usd_per_million: output.trim() || "0",
        effective_from: from === "month" ? monthStart.toISOString() : null,
      });
      toast.success(`Price saved for ${name.trim()}. Costs on the dashboard now use it.`);
      onDone();
    } catch (error) {
      toast.error(isApiError(error) ? error.message : "Could not save the price.");
    }
  }

  return (
    <DialogContent>
      <form onSubmit={submit} className="space-y-4">
        <DialogHeader>
          <DialogTitle>{model ? `Price for ${model}` : "Add a price"}</DialogTitle>
          <DialogDescription>
            In US dollars per million tokens, as your provider publishes it.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-1.5">
          <Label htmlFor="price-model">Model</Label>
          <Input
            id="price-model"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="gpt-5.5"
            required
            readOnly={Boolean(model)}
            className="font-mono"
          />
          {!model && (
            <p className="text-xs text-muted-foreground">
              Exactly as the provider spells it; the dashboard matches usage on this name.
            </p>
          )}
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="price-input">Input, $ per million</Label>
            <Input
              id="price-input"
              inputMode="decimal"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="1.25"
              required
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="price-output">Output, $ per million</Label>
            <Input
              id="price-output"
              inputMode="decimal"
              value={output}
              onChange={(e) => setOutput(e.target.value)}
              placeholder={kind === "embedding" ? "0" : "10"}
              disabled={kind === "embedding"}
            />
            {kind === "embedding" && (
              <p className="text-xs text-muted-foreground">Embeddings are billed on input only.</p>
            )}
          </div>
        </div>
        <fieldset className="space-y-2">
          <legend className="text-sm font-medium">Applies to</legend>
          <label className="flex items-start gap-2 text-sm">
            <input
              type="radio"
              name="price-from"
              checked={from === "month"}
              onChange={() => setFrom("month")}
              className="mt-1"
            />
            <span>
              Usage from {utc(monthStart.toISOString())} onwards
              <span className="block text-xs text-muted-foreground">
                Prices what this month has already recorded. Use for a model&rsquo;s first price.
              </span>
            </span>
          </label>
          <label className="flex items-start gap-2 text-sm">
            <input
              type="radio"
              name="price-from"
              checked={from === "now"}
              onChange={() => setFrom("now")}
              className="mt-1"
            />
            <span>
              Usage from now on
              <span className="block text-xs text-muted-foreground">
                For a price change. Earlier usage keeps the price it had.
              </span>
            </span>
          </label>
        </fieldset>
        <DialogFooter>
          <Button type="submit" disabled={save.isPending || !name.trim() || !input.trim()}>
            {save.isPending ? "Saving…" : "Save price"}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
