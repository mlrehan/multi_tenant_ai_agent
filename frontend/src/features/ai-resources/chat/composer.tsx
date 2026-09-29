"use client";

import { ArrowUp, Square } from "lucide-react";
import { useId, useLayoutEffect, useRef, useState } from "react";

import { cn } from "@/lib/utils";

/** Tallest the input grows before it scrolls -- about eight lines. */
const MAX_HEIGHT_PX = 200;

/**
 * The message input: one line by default, growing with its content.
 *
 * - **Enter sends, Shift+Enter adds a line.** Except mid-composition: for
 *   Japanese, Chinese or Korean input, Enter *confirms a character* in the
 *   IME, and treating that as "send" fires a half-typed question.
 * - **Sized in JavaScript, not CSS `field-sizing`**, which Firefox does not
 *   support; the shared `Textarea` relies on it and would stay one line there.
 * - **Empty or whitespace-only input cannot be sent** -- by key or by button.
 * - **While an answer streams, the send button becomes Stop.** The text box
 *   stays editable, so the next question can be drafted during the wait, but
 *   it cannot be sent until the current answer ends or is stopped.
 */
export function Composer({
  onSend,
  onStop,
  busy,
  disabled = false,
  placeholder = "Ask a question…",
  autoFocus = false,
}: {
  onSend: (text: string) => void;
  onStop: () => void;
  busy: boolean;
  disabled?: boolean;
  placeholder?: string;
  autoFocus?: boolean;
}) {
  const [value, setValue] = useState("");
  const inputId = useId();
  const ref = useRef<HTMLTextAreaElement>(null);
  const canSend = !busy && !disabled && value.trim().length > 0;

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT_PX)}px`;
    el.style.overflowY = el.scrollHeight > MAX_HEIGHT_PX ? "auto" : "hidden";
  }, [value]);

  function send() {
    if (!canSend) return;
    onSend(value.trim());
    setValue("");
    ref.current?.focus();
  }

  return (
    <div className="px-4 pb-4">
      <div
        className={cn(
          "flex items-end gap-2 rounded-2xl border border-input bg-background px-3 py-2 shadow-xs transition-colors",
          "focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/30",
          disabled && "opacity-60",
        )}
      >
        <label htmlFor={inputId} className="sr-only">
          Your question
        </label>
        <textarea
          id={inputId}
          ref={ref}
          rows={1}
          value={value}
          disabled={disabled}
          autoFocus={autoFocus}
          placeholder={placeholder}
          maxLength={4000}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              send();
            }
          }}
          className="max-h-[200px] min-h-6 flex-1 resize-none self-center bg-transparent py-1 text-sm leading-6 outline-none placeholder:text-muted-foreground disabled:cursor-not-allowed"
        />
        {busy ? (
          <button
            type="button"
            onClick={onStop}
            aria-label="Stop generating"
            title="Stop generating"
            className="mb-0.5 inline-flex size-8 shrink-0 items-center justify-center rounded-full bg-foreground text-background transition-opacity hover:opacity-85 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
          >
            <Square className="size-3 fill-current" />
          </button>
        ) : (
          <button
            type="button"
            onClick={send}
            disabled={!canSend}
            aria-label="Send question"
            title="Send (Enter)"
            className="mb-0.5 inline-flex size-8 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground transition-all hover:opacity-90 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none disabled:cursor-not-allowed disabled:bg-muted disabled:text-muted-foreground"
          >
            <ArrowUp className="size-4" />
          </button>
        )}
      </div>
      <p className="mt-1.5 px-1 text-center text-[0.7rem] text-muted-foreground">
        <kbd className="font-sans">Enter</kbd> to send · <kbd className="font-sans">Shift</kbd>+
        <kbd className="font-sans">Enter</kbd> for a new line
      </p>
    </div>
  );
}
