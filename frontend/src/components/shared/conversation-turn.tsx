"use client";

import { MessageSquareText } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type { ConversationMessage, MessageRole } from "@/lib/types";

/**
 * One turn of a conversation, labelled by who actually said it.
 *
 * **Shared because the label is a correctness question, not styling.** Both
 * thread views -- the agent's, working a live handoff, and the admin's, reading
 * history -- render the same five `MessageRole` values, and the admin's used to
 * collapse every non-visitor turn to "Assistant". A support agent's reply and
 * the AI's answer then looked identical, so a tenant admin auditing a
 * handed-off conversation could not tell which half a human had written -- the
 * exact thing they open the thread to find out.
 *
 * `internal_comment` is staff-only and never reaches a visitor (the public read
 * filters on `MessageRole.visible_to_visitor`); here it is shown, and marked,
 * because a tenant admin is staff and the note is part of the record.
 */

/** Who the `user` role refers to depends on whose thread this is: the reader's
 *  own, another member's, or an anonymous website visitor's. Passing it in
 *  rather than guessing keeps "You" from being shown to someone reading a
 *  stranger's conversation. */
export type AskerLabel = "You" | "Member" | "Visitor";

const ROLE_LABELS: Record<Exclude<MessageRole, "user">, string> = {
  assistant: "AI",
  agent_message: "Support agent",
  internal_comment: "Internal note",
  system_event: "",
};

export function ConversationTurn({
  message,
  asker,
}: {
  message: ConversationMessage;
  asker: AskerLabel;
}) {
  const { role, content } = message;

  // Transfers and hand-backs are markers, not speech. Centred and quiet so a
  // thread reads as a conversation with events in it rather than as a
  // participant who keeps announcing itself.
  if (role === "system_event") {
    return (
      <p className="flex items-center justify-center gap-1.5 text-center text-xs text-muted-foreground">
        <MessageSquareText className="size-3.5" /> {content}
      </p>
    );
  }

  const internal = role === "internal_comment";
  const label = role === "user" ? asker : ROLE_LABELS[role];

  return (
    <div
      className={
        internal ? "rounded-md border border-amber-500/30 bg-amber-500/10 p-2" : undefined
      }
    >
      <Badge
        variant={
          role === "user" ? "secondary" : role === "agent_message" ? "default" : "outline"
        }
      >
        {label}
      </Badge>
      {/* No markdown renderer, deliberately: this is model output built from
          tenant-uploaded documents plus free text written by strangers, and
          treating it as markup is how a poisoned document becomes script
          execution in an admin's browser. React escapes by default.

          `break-words` because answers routinely carry long unbroken tokens
          (URLs, ids) that `whitespace-pre-wrap` alone will not break. */}
      <p className="mt-1 text-sm whitespace-pre-wrap break-words">{content}</p>
      {message.citations.length > 0 && (
        <p className="mt-1 text-xs text-muted-foreground">
          Sources:{" "}
          {message.citations
            .map((c) => `[${c.label}] ${c.source_location ?? "document"}`)
            .join(" · ")}
          {message.token_count > 0
            ? ` — ${message.token_count.toLocaleString()} tokens`
            : ""}
        </p>
      )}
    </div>
  );
}
