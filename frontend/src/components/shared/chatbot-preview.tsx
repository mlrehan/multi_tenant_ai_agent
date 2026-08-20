"use client";

import { Bot, Leaf, Send, Star, ToyBrick } from "lucide-react";
import type { AvatarKey, Personality, ResponseLength } from "@/lib/types";

/** The avatars the widget itself ships, mirrored here so the preview shows the
 *  real thing. Icons, not images from a URL — the backend stores an asset *key*
 *  precisely so a tenant cannot point every visitor's browser at an arbitrary
 *  third-party origin from their own customers' pages. */
const AVATARS: Record<AvatarKey, typeof Bot> = {
  "nursery-default": Bot,
  "nursery-bear": ToyBrick,
  "nursery-star": Star,
  "nursery-leaf": Leaf,
};

export const AVATAR_LABELS: Record<AvatarKey, string> = {
  "nursery-default": "Assistant",
  "nursery-bear": "Bear",
  "nursery-star": "Star",
  "nursery-leaf": "Leaf",
};

export function AvatarGlyph({ avatarKey, className }: { avatarKey: AvatarKey; className?: string }) {
  const Icon = AVATARS[avatarKey] ?? Bot;
  return <Icon className={className} />;
}

/** A sample answer, shaped by the same two enums the backend maps to prompt
 *  instructions. Deliberately *illustrative*, not generated: calling the model
 *  to render a settings preview would spend a tenant's tokens every time they
 *  dragged a toggle. The tooltip in the panel says so. */
function sampleAnswer(personality: Personality, length: ResponseLength): string {
  const opener: Record<Personality, string> = {
    neutral: "We are open 7:30am to 6:00pm, Monday to Friday.",
    friendly: "We're open 7:30am–6:00pm, Monday to Friday — lovely to hear from you!",
    reassuring:
      "Of course, that's a very common question. We're open 7:30am to 6:00pm, Monday to Friday.",
    professional:
      "The nursery operates from 7:30am until 6:00pm, Monday to Friday inclusive.",
  };
  const extra: Record<ResponseLength, string> = {
    concise: "",
    balanced: " Morning and afternoon sessions are both available. [1]",
    detailed:
      " Sessions are available as follows:\n• Morning 7:30am–1:00pm\n• Afternoon 1:00pm–6:00pm\n• Full day 7:30am–6:00pm\n\nFunded hours can be applied to any of these. [1]",
  };
  return opener[personality] + extra[length];
}

export interface PreviewProps {
  chatbotName: string;
  chatbotTitle: string;
  avatarKey: AvatarKey;
  greeting: string | null;
  showQuickReplies: boolean;
  personality: Personality;
  responseLength: ResponseLength;
  allowHandoff: boolean;
  teamNames: string[];
  enabled: boolean;
}

/**
 * A faithful-enough stand-in for the embedded widget.
 *
 * Reuses the widget's *shape* (header with avatar and title, bubbles, quick
 * replies, composer) rather than importing it: the real widget renders into a
 * shadow root on a third-party page with no framework, and mounting that inside
 * the console would mean maintaining two mount paths for one component. What
 * matters for a settings screen is that a change to any field is visible
 * immediately, which this gives.
 */
export function ChatbotPreview(props: PreviewProps) {
  const {
    chatbotName,
    chatbotTitle,
    avatarKey,
    greeting,
    showQuickReplies,
    personality,
    responseLength,
    allowHandoff,
    teamNames,
    enabled,
  } = props;

  return (
    <div className="overflow-hidden rounded-xl border bg-card shadow-sm">
      <div className="flex items-center gap-3 border-b bg-primary/5 px-4 py-3">
        <div className="flex size-9 items-center justify-center rounded-full bg-primary/10 text-primary">
          <AvatarGlyph avatarKey={avatarKey} className="size-5" />
        </div>
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold">{chatbotName || "Assistant"}</div>
          <div className="truncate text-xs text-muted-foreground">{chatbotTitle}</div>
        </div>
      </div>

      <div className="space-y-3 p-4">
        {!enabled ? (
          <div className="rounded-lg bg-muted px-3 py-2 text-sm text-muted-foreground">
            The AI assistant is switched off. Visitors are connected to a person instead —
            conversations still work, they just aren&rsquo;t answered automatically.
          </div>
        ) : (
          <>
            <Bubble side="bot">
              {greeting?.trim() ||
                `Hello! I'm the ${chatbotName || "nursery"} assistant. How can I help?`}
            </Bubble>
            {showQuickReplies && (
              <div className="flex flex-wrap gap-1.5">
                {["Admissions", "Fees & funding", "Opening hours"].map((q) => (
                  <span
                    key={q}
                    className="rounded-full border border-primary/30 bg-primary/5 px-2.5 py-1 text-xs text-primary"
                  >
                    {q}
                  </span>
                ))}
                {allowHandoff && (
                  <span className="rounded-full border border-primary/30 bg-primary/5 px-2.5 py-1 text-xs text-primary">
                    Speak to a person
                  </span>
                )}
              </div>
            )}
            <Bubble side="user">What are your opening hours?</Bubble>
            <Bubble side="bot">
              <span className="whitespace-pre-line">
                {sampleAnswer(personality, responseLength)}
              </span>
            </Bubble>
            {allowHandoff && teamNames.length > 0 && (
              <>
                <Bubble side="user">I&rsquo;d like to talk to a human.</Bubble>
                <Bubble side="bot">
                  Of course. Which team would you like to speak with?
                </Bubble>
                <div className="flex flex-wrap gap-1.5">
                  {teamNames.slice(0, 3).map((t) => (
                    <span
                      key={t}
                      className="rounded-full border border-primary/30 bg-primary/5 px-2.5 py-1 text-xs text-primary"
                    >
                      {t}
                    </span>
                  ))}
                </div>
              </>
            )}
          </>
        )}
      </div>

      <div className="flex items-center gap-2 border-t p-3">
        <div className="flex-1 rounded-full border bg-background px-3 py-1.5 text-sm text-muted-foreground">
          Ask a question…
        </div>
        <div className="flex size-8 items-center justify-center rounded-full bg-primary text-primary-foreground">
          <Send className="size-4" />
        </div>
      </div>
    </div>
  );
}

function Bubble({ side, children }: { side: "bot" | "user"; children: React.ReactNode }) {
  return (
    <div className={side === "user" ? "flex justify-end" : "flex justify-start"}>
      <div
        className={`max-w-[85%] rounded-2xl px-3 py-2 text-sm ${
          side === "user"
            ? "rounded-br-sm bg-primary text-primary-foreground"
            : "rounded-bl-sm bg-muted"
        }`}
      >
        {children}
      </div>
    </div>
  );
}
