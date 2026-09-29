"use client";

import { ArrowDown, BookOpen, SquarePen } from "lucide-react";
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { streamAnswer } from "@/features/ai-resources/api";
import {
  useKnowledgeBaseDocuments,
  useRecordAnswerFeedback,
} from "@/features/ai-resources/hooks";
import { isApiError } from "@/lib/api-client";
import type { AnswerCitation, KnowledgeBase } from "@/lib/types";

import { Composer } from "./composer";
import { AssistantMessage, UserMessage, type ChatTurn } from "./message";

/** How close to the bottom still counts as "following along". */
const STICK_THRESHOLD_PX = 80;

function newId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/**
 * The console's chat with one knowledge base.
 *
 * **The backend contract is unchanged, and so is what it means.** Each
 * question is sent on its own, exactly as the previous single-question panel
 * sent it: the Ask endpoint is stateless for tenants (stored conversations
 * need an assistant, and assistants left the tenant surface). This screen
 * shows the session's questions as a thread because that is easier to read --
 * but it says plainly that follow-ups are not remembered, because a thread
 * that *looks* like it has memory and does not is the failure users notice
 * first ("what about the second one?" answered about nothing).
 *
 * One answer streams at a time. Regenerate replaces the latest answer rather
 * than appending, and costs what the original did: a full answer and one
 * message from the tenant's daily allowance.
 */
export function AskChat({
  tenantId,
  knowledgeBase,
}: {
  tenantId: string;
  knowledgeBase: KnowledgeBase;
}) {
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [atBottom, setAtBottom] = useState(true);
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const feedback = useRecordAnswerFeedback(tenantId, knowledgeBase.id);
  const documents = useKnowledgeBaseDocuments(tenantId, knowledgeBase.id);

  // document_id -> filename, so a source card names the document rather than
  // showing an id. From the list this screen already loads -- no new request.
  const documentNames = useMemo(
    () => new Map((documents.data?.documents ?? []).map((d) => [d.id, d.filename])),
    [documents.data],
  );

  const busy = turns.some((t) => t.status === "thinking" || t.status === "streaming");

  // A stream must not outlive the dialog: closing it aborts the request, so
  // nothing keeps generating (and billing) for a screen nobody is looking at.
  useEffect(() => () => abortRef.current?.abort(), []);

  // Follow the answer as it streams -- unless the reader has scrolled up to
  // read something, in which case yanking them back down is the worst thing
  // a chat can do. "Jump to latest" appears instead.
  useEffect(() => {
    const el = scrollRef.current;
    if (el && atBottom) el.scrollTop = el.scrollHeight;
  }, [turns, atBottom]);

  const update = useCallback((id: string, patch: Partial<ChatTurn> | ((t: ChatTurn) => Partial<ChatTurn>)) => {
    setTurns((prev) =>
      prev.map((t) => (t.id === id ? { ...t, ...(typeof patch === "function" ? patch(t) : patch) } : t)),
    );
  }, []);

  const run = useCallback(
    async (id: string, question: string) => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setAtBottom(true);

      try {
        for await (const frame of streamAnswer(tenantId, knowledgeBase.id, question, controller.signal)) {
          if (frame.event === "sources") {
            update(id, { citations: (frame.data.citations as AnswerCitation[]) ?? [] });
          } else if (frame.event === "token") {
            const text = String(frame.data.text ?? "");
            update(id, (t) => ({ answer: t.answer + text, status: "streaming" }));
          } else if (frame.event === "done") {
            update(id, { cited: (frame.data.cited as string[]) ?? [], status: "done" });
          } else if (frame.event === "error") {
            update(id, {
              status: "error",
              error: String(frame.data.detail ?? "The answer could not be completed."),
            });
          }
        }
        // A stream that ends without `done` or `error` still ends: never leave
        // a turn spinning for ever.
        update(id, (t) =>
          t.status === "thinking" || t.status === "streaming"
            ? t.answer
              ? { status: "done" }
              : { status: "error", error: "No answer came back. Please try again." }
            : {},
        );
      } catch (err) {
        if (controller.signal.aborted) {
          update(id, (t) =>
            t.status === "thinking" || t.status === "streaming" ? { status: "stopped" } : {},
          );
        } else {
          update(id, {
            status: "error",
            error: isApiError(err) ? err.message : "The answer could not be started.",
          });
        }
      }
    },
    [tenantId, knowledgeBase.id, update],
  );

  function ask(question: string) {
    const id = newId();
    setTurns((prev) => [
      ...prev,
      { id, question, answer: "", citations: [], cited: [], status: "thinking" },
    ]);
    void run(id, question);
  }

  function regenerate(turn: ChatTurn) {
    // A fresh id resets that answer's feedback: a rating belongs to the text
    // it was given about, not to whatever replaces it.
    const id = newId();
    setTurns((prev) =>
      prev.map((t) =>
        t.id === turn.id
          ? { id, question: t.question, answer: "", citations: [], cited: [], status: "thinking" }
          : t,
      ),
    );
    void run(id, turn.question);
  }

  function newChat() {
    abortRef.current?.abort();
    setTurns([]);
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-2.5 pr-12">
        <div className="flex min-w-0 items-center gap-2">
          <BookOpen className="size-4 shrink-0 text-muted-foreground" aria-hidden />
          <span className="truncate text-sm font-medium">{knowledgeBase.name}</span>
        </div>
        {turns.length > 0 && (
          <button
            type="button"
            onClick={newChat}
            className="inline-flex shrink-0 items-center gap-1.5 rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <SquarePen className="size-3.5" /> New chat
          </button>
        )}
      </div>

      <div className="relative min-h-0 flex-1">
        <div
          ref={scrollRef}
          onScroll={(e) => {
            const el = e.currentTarget;
            setAtBottom(el.scrollHeight - el.scrollTop - el.clientHeight < STICK_THRESHOLD_PX);
          }}
          className="h-full overflow-y-auto overscroll-contain"
        >
          {turns.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center px-6 py-10 text-center">
              <div className="mb-4 flex size-11 items-center justify-center rounded-full bg-primary/10 text-primary">
                <BookOpen className="size-5" />
              </div>
              <h3 className="text-base font-semibold">Ask {knowledgeBase.name}</h3>
              <p className="mt-1.5 max-w-sm text-sm text-muted-foreground">
                Answers come only from this knowledge base&rsquo;s documents, with the sources they
                rely on. If nothing here covers the question, it says so instead of guessing.
              </p>
            </div>
          ) : (
            <div className="mx-auto flex max-w-3xl flex-col gap-6 px-4 py-5" role="log" aria-label="Conversation">
              {turns.map((turn, i) => (
                <Fragment key={turn.id}>
                  <UserMessage text={turn.question} />
                  <AssistantMessage
                    turn={turn}
                    isLatest={i === turns.length - 1 && !busy}
                    documentNames={documentNames}
                    onRegenerate={() => regenerate(turn)}
                    onFeedback={async (rating, comment) => {
                      await feedback.mutateAsync({
                        rating,
                        question: turn.question,
                        answer: turn.answer,
                        comment,
                      });
                    }}
                  />
                </Fragment>
              ))}
            </div>
          )}
        </div>

        {!atBottom && turns.length > 0 && (
          <button
            type="button"
            onClick={() => setAtBottom(true)}
            aria-label="Jump to latest"
            className="absolute bottom-3 left-1/2 inline-flex size-8 -translate-x-1/2 items-center justify-center rounded-full border border-border bg-background text-muted-foreground shadow-md hover:text-foreground"
          >
            <ArrowDown className="size-4" />
          </button>
        )}
      </div>

      <div className="border-t border-border pt-3">
        {turns.length > 0 && (
          <p className="mb-2 px-4 text-center text-[0.7rem] text-muted-foreground">
            Each question is answered on its own &mdash; follow-ups don&rsquo;t remember earlier
            questions, so include the details you need.
          </p>
        )}
        <Composer
          onSend={ask}
          onStop={() => abortRef.current?.abort()}
          busy={busy}
          autoFocus
          placeholder={`Ask ${knowledgeBase.name}…`}
        />
      </div>
    </div>
  );
}
