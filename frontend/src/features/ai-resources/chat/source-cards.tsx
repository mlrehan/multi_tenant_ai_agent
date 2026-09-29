"use client";

import { ChevronDown, ExternalLink, FileText, Globe } from "lucide-react";
import { useState } from "react";

import { cn } from "@/lib/utils";

import type { SourceGroup } from "./sources";

/** "3 web pages", "2 documents", "2 web pages · 1 document". */
export function describeSources(groups: SourceGroup[]): string {
  const web = groups.filter((g) => g.kind === "web").length;
  const docs = groups.length - web;
  const part = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
  return [web ? part(web, "web page", "web pages") : null, docs ? part(docs, "document", "documents") : null]
    .filter(Boolean)
    .join(" · ");
}

/** A letter badge standing in for a site icon.
 *
 *  Not a favicon, deliberately: fetching `domain.com/favicon.ico` (or a
 *  favicon service) would tell that host -- or a third party -- which sources
 *  this console's users read, and it is an image load from outside, the same
 *  channel `Markdown` refuses for images. A letter says enough. */
function SiteBadge({ group, className }: { group: SourceGroup; className?: string }) {
  const letter = (group.domain ?? group.title).replace(/[^a-z0-9]/gi, "").charAt(0).toUpperCase() || "?";
  return (
    <span
      aria-hidden
      className={cn(
        "inline-flex size-5 shrink-0 items-center justify-center rounded-full border border-background bg-muted text-[0.6rem] font-semibold text-muted-foreground",
        className,
      )}
    >
      {group.kind === "web" ? letter : <FileText className="size-3" />}
    </span>
  );
}

function SourceRow({
  group,
  highlighted,
  anchorId,
  showRelevance,
}: {
  group: SourceGroup;
  highlighted: boolean;
  anchorId: string;
  showRelevance: boolean;
}) {
  const body = (
    <>
      <span className="mt-0.5 w-4 shrink-0 text-right text-[0.7rem] font-medium text-muted-foreground tabular-nums">
        {group.index}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[0.8125rem] font-medium text-foreground">{group.title}</span>
        <span className="mt-0.5 flex items-center gap-1.5 text-[0.7rem] text-muted-foreground">
          {group.kind === "web" ? <Globe className="size-3" aria-hidden /> : <FileText className="size-3" aria-hidden />}
          <span className="truncate">
            {group.kind === "web"
              ? group.domain
              : ["Document", group.location].filter(Boolean).join(" · ")}
          </span>
          {group.kind === "web" && <span className="shrink-0">· Web page</span>}
          {group.labels.length > 1 && (
            <span className="shrink-0">· {group.labels.length} passages</span>
          )}
        </span>
      </span>
      {/* Retrieval score: a diagnostic for administrators judging whether the
          right passage was found. Quiet, and only where asked for. */}
      {showRelevance && (
        <span className="mt-0.5 shrink-0 font-mono text-[0.65rem] text-muted-foreground tabular-nums" title="Retrieval relevance">
          {group.relevance.toFixed(2)}
        </span>
      )}
      {group.url && <ExternalLink className="mt-0.5 size-3 shrink-0 text-muted-foreground" aria-hidden />}
    </>
  );

  const className = cn(
    "flex items-start gap-2.5 rounded-lg px-2.5 py-2 transition-colors",
    group.url && "hover:bg-muted/60",
    highlighted && "bg-primary/5 ring-2 ring-primary/25",
  );

  // A web page opens in a new tab, with no handle on this window and no
  // referrer. The full address is the tooltip -- the card itself never shows
  // a long raw URL.
  return group.url ? (
    <a
      id={anchorId}
      href={group.url}
      target="_blank"
      rel="noopener noreferrer nofollow"
      title={group.url}
      className={className}
      aria-label={`Source ${group.index}: ${group.title}, ${group.domain}. Opens in a new tab.`}
    >
      {body}
    </a>
  ) : (
    <div id={anchorId} className={className}>
      {body}
    </div>
  );
}

/**
 * The sources an answer rests on, as a compact pill that expands.
 *
 * **One entry per document, not per passage** -- see `groupSources`. Five
 * chunks of one web page are one source.
 *
 * **Cited first, and "offered" kept visibly distinct from "used".** While the
 * answer streams nothing is cited *yet*, so everything retrieved is shown.
 * Once it finishes, only documents the answer cited are its sources; the rest
 * are folded away as "retrieved, not cited" -- kept, not hidden, because an
 * administrator testing retrieval needs to see what was found and passed
 * over. An answer that cited nothing ("the sources don't cover this") shows no
 * sources at all, only that fold.
 */
export function SourceCards({
  groups,
  finished,
  open,
  onOpenChange,
  highlighted,
  anchorPrefix,
  showDiagnostics = true,
}: {
  groups: SourceGroup[];
  finished: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  highlighted: string | null;
  anchorPrefix: string;
  showDiagnostics?: boolean;
}) {
  const [showUncited, setShowUncited] = useState(false);
  if (groups.length === 0) return null;

  const cited = groups.filter((g) => g.cited);
  const primary = cited.length > 0 ? cited : finished ? [] : groups;
  const secondary = cited.length > 0 ? groups.filter((g) => !g.cited) : finished ? groups : [];

  const row = (g: SourceGroup) => (
    <li key={g.documentId}>
      <SourceRow
        group={g}
        highlighted={highlighted === String(g.index)}
        anchorId={`${anchorPrefix}-${g.index}`}
        showRelevance={showDiagnostics}
      />
    </li>
  );

  return (
    <section aria-label="Sources" className="mt-3">
      {primary.length > 0 && (
        <button
          type="button"
          onClick={() => onOpenChange(!open)}
          aria-expanded={open}
          className="inline-flex items-center gap-2 rounded-full border border-border bg-background py-1 pr-2.5 pl-1.5 text-xs font-medium text-foreground shadow-xs transition-colors hover:bg-muted/60"
        >
          <span className="flex -space-x-1.5">
            {primary.slice(0, 3).map((g) => (
              <SiteBadge key={g.documentId} group={g} />
            ))}
          </span>
          {describeSources(primary)}
          <ChevronDown className={cn("size-3.5 text-muted-foreground transition-transform", open && "rotate-180")} />
        </button>
      )}

      {open && primary.length > 0 && (
        <ol className="mt-2 space-y-0.5 rounded-xl border border-border bg-card p-1.5">{primary.map(row)}</ol>
      )}

      {showDiagnostics && secondary.length > 0 && (
        <div className="mt-2">
          <button
            type="button"
            onClick={() => setShowUncited((v) => !v)}
            aria-expanded={showUncited}
            className="inline-flex items-center gap-1 text-[0.7rem] text-muted-foreground hover:text-foreground"
          >
            <ChevronDown className={cn("size-3 transition-transform", showUncited && "rotate-180")} />
            {cited.length > 0
              ? `${describeSources(secondary)} more retrieved, not cited`
              : `${describeSources(secondary)} retrieved, none cited`}
          </button>
          {showUncited && (
            <ol className="mt-1.5 space-y-0.5 rounded-xl border border-dashed border-border p-1.5">
              {secondary.map(row)}
            </ol>
          )}
        </div>
      )}
    </section>
  );
}
