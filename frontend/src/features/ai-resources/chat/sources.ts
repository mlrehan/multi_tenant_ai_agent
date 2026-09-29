import type { AnswerCitation } from "@/lib/types";

/** One source as a reader thinks of it: a document, however many passages
 *  of it the answer was given. */
export interface SourceGroup {
  /** 1-based position, in the order the sources were ranked. */
  index: number;
  documentId: string;
  /** Every citation label that points at this document. */
  labels: string[];
  title: string;
  /** Absolute http(s) URL for a web page; `null` for an uploaded file. */
  url: string | null;
  domain: string | null;
  kind: "web" | "document";
  /** A page or row location, for an uploaded file ("page 5"). */
  location: string | null;
  /** The best-ranked passage's score -- the group's rank is its best one. */
  relevance: number;
  /** Whether the answer cited *any* of this document's passages. */
  cited: boolean;
}

function asWebUrl(value: string | null | undefined): URL | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:" ? url : null;
  } catch {
    return null;
  }
}

/**
 * Collapses passages into one source per document.
 *
 * Retrieval returns *chunks*, and five chunks of one web page is ordinary --
 * shown as they came, the reader saw the same page five times. Grouping is by
 * `document_id`, the one identity that is certain: two uploads can both be
 * "page 5", and one page can be cited under several labels.
 *
 * **Order is first appearance**, which is the reranker's order, so card 1 is
 * the best source and a document's rank is that of its best passage.
 *
 * Returns the groups and a `label -> card number` map, so an inline `[4]`
 * can point at the card that actually holds passage 4.
 */
export function groupSources(
  citations: AnswerCitation[],
  cited: ReadonlySet<string>,
  documentNames: ReadonlyMap<string, string>,
): { groups: SourceGroup[]; numberOf: Map<string, string> } {
  const byDoc = new Map<string, SourceGroup>();
  const numberOf = new Map<string, string>();

  for (const c of citations) {
    let group = byDoc.get(c.document_id);
    if (!group) {
      const web = asWebUrl(c.source_url) ?? asWebUrl(c.source_location);
      group = {
        index: byDoc.size + 1,
        documentId: c.document_id,
        labels: [],
        title:
          c.title ??
          documentNames.get(c.document_id) ??
          (web ? web.hostname.replace(/^www\./, "") : `Source ${byDoc.size + 1}`),
        url: web ? web.toString() : null,
        domain: web ? web.hostname.replace(/^www\./, "") : null,
        kind: web ? "web" : "document",
        location: web ? null : c.source_location,
        relevance: c.relevance,
        cited: false,
      };
      byDoc.set(c.document_id, group);
    }
    group.labels.push(c.label);
    group.relevance = Math.max(group.relevance, c.relevance);
    if (cited.has(c.label)) group.cited = true;
    numberOf.set(c.label, String(group.index));
  }
  return { groups: [...byDoc.values()], numberOf };
}
