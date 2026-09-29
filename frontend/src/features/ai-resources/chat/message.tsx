"use client";

import {
  AlertTriangle,
  Check,
  Copy,
  RotateCcw,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
} from "lucide-react";
import { useId, useMemo, useState, type ReactNode } from "react";

import { isApiError } from "@/lib/api-client";
import type { AnswerCitation } from "@/lib/types";
import { cn } from "@/lib/utils";

import { copyText } from "./copy";
import { Markdown } from "./markdown";
import { SourceCards } from "./source-cards";
import { groupSources } from "./sources";

export type AssistantStatus = "thinking" | "streaming" | "done" | "stopped" | "error";

export interface ChatTurn {
  id: string;
  question: string;
  answer: string;
  citations: AnswerCitation[];
  cited: string[];
  status: AssistantStatus;
  error?: string;
}

export function UserMessage({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      {/* `whitespace-pre-wrap` here, and only here: this is what the person
          typed, shown as typed. It is never parsed as markdown -- nobody's
          own question should be reformatted back at them. */}
      <div className="max-w-[85%] rounded-2xl rounded-br-md bg-muted px-4 py-2.5 text-sm leading-6 whitespace-pre-wrap break-words text-foreground">
        {text}
      </div>
    </div>
  );
}

function ActionButton({
  label,
  onClick,
  disabled,
  active,
  children,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  active?: boolean;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      aria-pressed={active}
      title={label}
      className={cn(
        "inline-flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors",
        "hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none",
        "disabled:pointer-events-none disabled:opacity-40",
        active && "text-foreground",
      )}
    >
      {children}
    </button>
  );
}

type FeedbackState =
  | { kind: "idle" }
  | { kind: "asking-why" }
  | { kind: "sending"; rating: "up" | "down" }
  | { kind: "sent"; rating: "up" | "down" };

/** Thumbs up/down, saved to the server.
 *
 *  **Once sent, the choice is locked.** Feedback is stored append-only -- it
 *  is a record of what someone thought when they read the answer -- so the
 *  buttons do not pretend a second click changes it. A failure is shown and
 *  the buttons re-enabled, so nothing reports success it did not have. */
function FeedbackControls({
  onSubmit,
}: {
  onSubmit: (rating: "up" | "down", comment: string | null) => Promise<void>;
}) {
  const [state, setState] = useState<FeedbackState>({ kind: "idle" });
  const [comment, setComment] = useState("");
  const [failed, setFailed] = useState<string | null>(null);
  const whyId = useId();

  async function submit(rating: "up" | "down", why: string | null) {
    setFailed(null);
    setState({ kind: "sending", rating });
    try {
      await onSubmit(rating, why);
      setState({ kind: "sent", rating });
    } catch (err) {
      setState({ kind: "idle" });
      setFailed(isApiError(err) ? err.message : "Feedback could not be sent.");
    }
  }

  const chosen = state.kind === "sent" || state.kind === "sending" ? state.rating : null;
  const locked = state.kind === "sent" || state.kind === "sending";

  return (
    <>
      <ActionButton
        label={chosen === "up" ? "Rated helpful" : "Helpful"}
        onClick={() => submit("up", null)}
        disabled={locked}
        active={chosen === "up"}
      >
        <ThumbsUp className={cn("size-3.5", chosen === "up" && "fill-current")} />
      </ActionButton>
      <ActionButton
        label={chosen === "down" ? "Rated not helpful" : "Not helpful"}
        onClick={() => setState({ kind: "asking-why" })}
        disabled={locked}
        active={chosen === "down" || state.kind === "asking-why"}
      >
        <ThumbsDown className={cn("size-3.5", chosen === "down" && "fill-current")} />
      </ActionButton>
      {state.kind === "sent" && (
        <span role="status" className="ml-1 text-[0.7rem] text-muted-foreground">
          Thanks for the feedback
        </span>
      )}
      {failed && (
        <span role="alert" className="ml-1 text-[0.7rem] text-destructive">
          {failed}
        </span>
      )}
      {state.kind === "asking-why" && (
        <div className="mt-2 basis-full rounded-lg border border-border bg-muted/30 p-2.5">
          <label className="text-xs font-medium text-foreground" htmlFor={whyId}>
            What was wrong with this answer? <span className="text-muted-foreground">(optional)</span>
          </label>
          <textarea
            id={whyId}
            autoFocus
            rows={2}
            maxLength={1000}
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder="e.g. out of date, wrong document, didn't answer the question"
            className="mt-1.5 w-full resize-none rounded-md border border-input bg-background px-2 py-1.5 text-xs outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/30"
          />
          <div className="mt-1.5 flex justify-end gap-1.5">
            <button
              type="button"
              onClick={() => setState({ kind: "idle" })}
              className="rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-muted"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => submit("down", comment.trim() || null)}
              className="rounded-md bg-primary px-2.5 py-1 text-xs font-medium text-primary-foreground hover:opacity-90"
            >
              Send feedback
            </button>
          </div>
        </div>
      )}
    </>
  );
}

function ThinkingIndicator() {
  return (
    <div className="flex items-center gap-2 py-1 text-sm text-muted-foreground" role="status">
      <span className="flex gap-1" aria-hidden>
        <span className="size-1.5 animate-bounce rounded-full bg-muted-foreground/60 [animation-delay:-0.3s]" />
        <span className="size-1.5 animate-bounce rounded-full bg-muted-foreground/60 [animation-delay:-0.15s]" />
        <span className="size-1.5 animate-bounce rounded-full bg-muted-foreground/60" />
      </span>
      <span>Searching the knowledge base…</span>
    </div>
  );
}

export function AssistantMessage({
  turn,
  isLatest,
  documentNames,
  onRegenerate,
  onFeedback,
}: {
  turn: ChatTurn;
  isLatest: boolean;
  documentNames: ReadonlyMap<string, string>;
  onRegenerate: () => void;
  onFeedback: (rating: "up" | "down", comment: string | null) => Promise<void>;
}) {
  const [copied, setCopied] = useState(false);
  const [highlighted, setHighlighted] = useState<string | null>(null);
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const anchorPrefix = `source-${turn.id}`;
  const finished = turn.status === "done" || turn.status === "stopped";

  // One entry per document, numbered in relevance order; `numberOf` maps
  // each passage label to its card so inline chips point at the right one.
  const { groups, numberOf } = useMemo(
    () => groupSources(turn.citations, new Set(turn.cited), documentNames),
    [turn.citations, turn.cited, documentNames],
  );
  const titles = useMemo(
    () => new Map(groups.map((g) => [String(g.index), g.title])),
    [groups],
  );

  function showSource(cardNumber: string) {
    // The list is collapsed by default, so a chip opens it first and then
    // scrolls to the card once it exists.
    setSourcesOpen(true);
    setHighlighted(cardNumber);
    window.setTimeout(() => {
      document
        .getElementById(`${anchorPrefix}-${cardNumber}`)
        ?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }, 50);
    window.setTimeout(() => setHighlighted((h) => (h === cardNumber ? null : h)), 1800);
  }

  return (
    <div className="flex gap-3">
      <div
        aria-hidden
        className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary"
      >
        <Sparkles className="size-3.5" />
      </div>

      <div className="min-w-0 flex-1">
        {turn.status === "thinking" && <ThinkingIndicator />}

        {turn.answer && (
          <div aria-live={turn.status === "streaming" ? "polite" : undefined}>
            <Markdown
              text={turn.answer}
              citationNumbers={numberOf}
              citationTitles={titles}
              onCitation={showSource}
            />
            {turn.status === "streaming" && (
              <span
                aria-hidden
                className="ml-0.5 inline-block h-4 w-1.5 translate-y-0.5 animate-pulse rounded-sm bg-foreground/60"
              />
            )}
          </div>
        )}

        {turn.status === "stopped" && (
          <p className="mt-2 text-xs text-muted-foreground italic">Stopped before the answer finished.</p>
        )}

        {turn.status === "error" && (
          <div
            role="alert"
            className="mt-1 flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2.5 text-sm"
          >
            <AlertTriangle className="mt-0.5 size-4 shrink-0 text-destructive" />
            <div className="min-w-0 flex-1">
              <p className="text-foreground">{turn.error ?? "The answer could not be completed."}</p>
              {isLatest && (
                <button
                  type="button"
                  onClick={onRegenerate}
                  className="mt-1 inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"
                >
                  <RotateCcw className="size-3" /> Try again
                </button>
              )}
            </div>
          </div>
        )}

        <SourceCards
          groups={groups}
          open={sourcesOpen}
          onOpenChange={setSourcesOpen}
          highlighted={highlighted}
          anchorPrefix={anchorPrefix}
          finished={turn.status !== "thinking" && turn.status !== "streaming"}
        />

        {finished && turn.answer && (
          <div className="mt-2 -ml-1.5 flex flex-wrap items-center gap-0.5">
            <ActionButton
              label={copied ? "Copied" : "Copy answer"}
              onClick={async () => {
                if (await copyText(turn.answer)) {
                  setCopied(true);
                  window.setTimeout(() => setCopied(false), 1500);
                }
              }}
            >
              {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
            </ActionButton>
            {isLatest && (
              <ActionButton label="Regenerate answer" onClick={onRegenerate}>
                <RotateCcw className="size-3.5" />
              </ActionButton>
            )}
            {turn.status === "done" && <FeedbackControls key={turn.id} onSubmit={onFeedback} />}
          </div>
        )}
      </div>
    </div>
  );
}
