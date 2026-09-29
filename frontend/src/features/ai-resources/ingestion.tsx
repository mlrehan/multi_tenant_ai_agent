"use client";

/**
 * Live ingestion: files appear the moment they are picked, and each shows
 * where it is -- uploading, extracting, chunking, embedding, indexing, ready
 * or failed -- with one 0-100% bar across the whole journey, plus a bar for
 * the batch as a whole.
 *
 * Two sources feed it. The *upload* happens in this browser, so its progress
 * comes from `XMLHttpRequest` upload events. Everything after it happens on a
 * worker, and arrives on the documents list (`stage`, `progress_percent`,
 * `stage_detail`), which polls every 1.5 s while anything is in flight. The
 * bar gives the upload the first 20% and the server pipeline the remaining
 * 80%, so a file's bar never moves backwards when one source hands over to the
 * other.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { AlertCircle, CheckCircle2, Clock3, Loader2, Upload } from "lucide-react";

import { uploadDocumentWithProgress } from "@/features/ai-resources/api";
import { isApiError } from "@/lib/api-client";
import type { IngestionStage, KnowledgeBaseDocument } from "@/lib/types";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Stages and percentages
// ---------------------------------------------------------------------------

export type Step = "upload" | IngestionStage | "done";

export const STEPS: { key: Step; label: string; short: string }[] = [
  { key: "upload", label: "Uploading", short: "Upload" },
  { key: "extracting", label: "Extracting content", short: "Extract" },
  { key: "chunking", label: "Processing & chunking", short: "Chunk" },
  { key: "embedding", label: "Generating embeddings", short: "Embed" },
  { key: "indexing", label: "Saving to search index", short: "Index" },
  { key: "done", label: "Completed", short: "Done" },
];

const STEP_LABEL = Object.fromEntries(STEPS.map((s) => [s.key, s.label])) as Record<Step, string>;

/** The upload's share of one file's bar; the server pipeline has the rest. */
const UPLOAD_SHARE = 20;

export type Tone = "active" | "waiting" | "done" | "warning" | "error";

export interface FileProgress {
  step: Step;
  percent: number;
  tone: Tone;
  /** "Generating embeddings", "Queued", "Failed", ... */
  label: string;
  /** "120 of 466 passages", a failure reason, ... */
  detail: string | null;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Where a server-side document is, on the same scale as a local upload. */
export function documentProgress(doc: KnowledgeBaseDocument): FileProgress {
  if (doc.status === "ready") {
    const empty = doc.chunk_count === 0;
    return {
      step: "done",
      percent: 100,
      tone: empty ? "warning" : "done",
      label: empty ? "Ready — nothing to search" : "Ready",
      detail: empty
        ? "Indexed, but no searchable text was found, so this file can't answer questions."
        : `${doc.chunk_count} passage${doc.chunk_count === 1 ? "" : "s"} searchable`,
    };
  }
  if (doc.status === "failed") {
    const stage = doc.stage ?? null;
    return {
      step: stage ?? "extracting",
      percent: pipelinePercent(doc.progress_percent ?? 0),
      tone: "error",
      label: stage ? `Failed while ${STEP_LABEL[stage].toLowerCase()}` : "Failed",
      detail: doc.failure_reason ?? "Ingestion failed.",
    };
  }
  if (!doc.stage) {
    // Uploaded, and waiting for a worker to pick it up.
    return {
      step: "extracting",
      percent: UPLOAD_SHARE,
      tone: "waiting",
      label: "Queued",
      detail: "Waiting for a worker to start processing",
    };
  }
  return {
    step: doc.stage,
    percent: pipelinePercent(doc.progress_percent ?? 0),
    tone: "active",
    label: STEP_LABEL[doc.stage],
    detail: doc.stage_detail ?? null,
  };
}

function pipelinePercent(serverPercent: number): number {
  return Math.round(UPLOAD_SHARE + (Math.max(0, Math.min(100, serverPercent)) * (100 - UPLOAD_SHARE)) / 100);
}

// ---------------------------------------------------------------------------
// The upload queue
// ---------------------------------------------------------------------------

export type UploadPhase = "waiting" | "uploading" | "uploaded" | "error";

export interface LocalUpload {
  key: string;
  file: File;
  phase: UploadPhase;
  loaded: number;
  error?: string;
  /** Set once the server has accepted it; the list then shows the document. */
  documentId?: string;
}

export function uploadProgress(upload: LocalUpload): FileProgress {
  const total = upload.file.size || 1;
  if (upload.phase === "error") {
    return {
      step: "upload",
      percent: 0,
      tone: "error",
      label: "Upload failed",
      detail: upload.error ?? "The upload didn't complete.",
    };
  }
  if (upload.phase === "waiting") {
    return { step: "upload", percent: 0, tone: "waiting", label: "Waiting to upload", detail: null };
  }
  const fraction = Math.min(1, upload.loaded / total);
  return {
    step: "upload",
    percent: Math.round(fraction * UPLOAD_SHARE),
    tone: "active",
    label: upload.phase === "uploaded" ? "Uploaded" : `Uploading · ${Math.round(fraction * 100)}%`,
    detail: `${formatBytes(Math.min(upload.loaded, total))} of ${formatBytes(total)}`,
  };
}

/** Two at a time: fast for a batch, without one tenant flooding the queue. */
const CONCURRENT_UPLOADS = 2;

/**
 * Uploads files as soon as they are added, reporting byte-level progress.
 *
 * Each file is sent once and only once -- `started` guards against React
 * StrictMode running the pump effect twice. A cancelled file is removed; a
 * failed one stays, with its reason, until retried or dismissed.
 */
export function useUploadQueue(tenantId: string, knowledgeBaseId: string) {
  const queryClient = useQueryClient();
  const [uploads, setUploads] = useState<LocalUpload[]>([]);
  const started = useRef(new Set<string>());
  const controllers = useRef(new Map<string, AbortController>());

  const patch = useCallback((key: string, change: Partial<LocalUpload>) => {
    setUploads((current) => current.map((u) => (u.key === key ? { ...u, ...change } : u)));
  }, []);

  const start = useCallback(
    (upload: LocalUpload) => {
      started.current.add(upload.key);
      const controller = new AbortController();
      controllers.current.set(upload.key, controller);
      patch(upload.key, { phase: "uploading", loaded: 0, error: undefined });

      uploadDocumentWithProgress(
        tenantId,
        knowledgeBaseId,
        upload.file,
        (loaded) => patch(upload.key, { loaded }),
        controller.signal,
      )
        .then(({ id }) => {
          patch(upload.key, { phase: "uploaded", loaded: upload.file.size, documentId: id });
          // Refetch now rather than waiting for the next poll, so the file
          // moves straight on to "Queued" in the list.
          void queryClient.invalidateQueries({
            queryKey: ["kb-documents", tenantId, knowledgeBaseId],
          });
        })
        .catch((err: unknown) => {
          if (err instanceof DOMException && err.name === "AbortError") {
            setUploads((current) => current.filter((u) => u.key !== upload.key));
            return;
          }
          patch(upload.key, {
            phase: "error",
            error: isApiError(err) ? err.message : "The upload didn't complete.",
          });
        })
        .finally(() => controllers.current.delete(upload.key));
    },
    [knowledgeBaseId, patch, queryClient, tenantId],
  );

  // The pump: whenever the list changes, start waiting files up to the limit.
  useEffect(() => {
    const inFlight = uploads.filter((u) => u.phase === "uploading").length;
    const ready = uploads.filter((u) => u.phase === "waiting" && !started.current.has(u.key));
    for (const upload of ready.slice(0, Math.max(0, CONCURRENT_UPLOADS - inFlight))) {
      start(upload);
    }
  }, [start, uploads]);

  // Leaving the dialog cancels anything still sending.
  useEffect(() => {
    const active = controllers.current;
    return () => {
      for (const controller of active.values()) controller.abort();
    };
  }, []);

  const add = useCallback((files: { file: File; key: string }[]) => {
    setUploads((current) => {
      const present = new Set(current.map((u) => u.key));
      const fresh = files
        .filter((f) => !present.has(f.key))
        .map((f) => ({ key: f.key, file: f.file, phase: "waiting" as const, loaded: 0 }));
      return fresh.length ? [...fresh, ...current] : current;
    });
  }, []);

  const cancel = useCallback((key: string) => {
    const controller = controllers.current.get(key);
    if (controller) controller.abort();
    else setUploads((current) => current.filter((u) => u.key !== key));
  }, []);

  const retry = useCallback((key: string) => {
    started.current.delete(key);
    setUploads((current) =>
      current.map((u) => (u.key === key ? { ...u, phase: "waiting", loaded: 0, error: undefined } : u)),
    );
  }, []);

  const dismiss = useCallback((key: string) => {
    setUploads((current) => current.filter((u) => u.key !== key));
  }, []);

  return { uploads, add, cancel, retry, dismiss };
}

// ---------------------------------------------------------------------------
// Presentation
// ---------------------------------------------------------------------------

const TONE_BAR: Record<Tone, string> = {
  active: "bg-primary",
  waiting: "bg-muted-foreground/40",
  done: "bg-emerald-500",
  warning: "bg-amber-500",
  error: "bg-destructive",
};

export function ProgressBar({
  percent,
  tone,
  label,
  className,
}: {
  percent: number;
  tone: Tone;
  label: string;
  className?: string;
}) {
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={percent}
      className={cn("h-1.5 w-full overflow-hidden rounded-full bg-muted", className)}
    >
      <div
        className={cn(
          "h-full rounded-full transition-[width] duration-500 ease-out",
          TONE_BAR[tone],
          tone === "active" && "motion-safe:animate-pulse",
        )}
        style={{ width: `${Math.max(tone === "waiting" ? 0 : 2, percent)}%` }}
      />
    </div>
  );
}

export function StatusIcon({ tone, className }: { tone: Tone; className?: string }) {
  if (tone === "done") return <CheckCircle2 className={cn("size-4 text-emerald-600 dark:text-emerald-400", className)} />;
  if (tone === "warning") return <AlertCircle className={cn("size-4 text-amber-600 dark:text-amber-500", className)} />;
  if (tone === "error") return <AlertCircle className={cn("size-4 text-destructive", className)} />;
  if (tone === "waiting") return <Clock3 className={cn("size-4 text-muted-foreground", className)} />;
  return <Loader2 className={cn("size-4 animate-spin text-primary", className)} />;
}

/** Upload → Extract → Chunk → Embed → Index → Done, with where this file is. */
export function StageStepper({ progress }: { progress: FileProgress }) {
  const currentIndex = STEPS.findIndex((s) => s.key === progress.step);
  return (
    <ol className="flex flex-wrap items-center gap-x-1 gap-y-1 text-[11px]" aria-label="Ingestion stages">
      {STEPS.map((step, index) => {
        const finished = progress.tone === "done" || progress.tone === "warning" || index < currentIndex;
        const current = index === currentIndex && !finished;
        const failed = current && progress.tone === "error";
        return (
          <li key={step.key} className="flex items-center gap-1">
            <span
              className={cn(
                "inline-flex items-center gap-1 rounded-full px-1.5 py-0.5",
                finished && "text-emerald-700 dark:text-emerald-400",
                current && !failed && "bg-primary/10 font-medium text-primary",
                failed && "bg-destructive/10 font-medium text-destructive",
                !finished && !current && "text-muted-foreground",
              )}
              aria-current={current ? "step" : undefined}
            >
              <span
                className={cn(
                  "size-1.5 rounded-full",
                  finished ? "bg-emerald-500" : failed ? "bg-destructive" : current ? "bg-primary" : "bg-muted-foreground/30",
                  current && !failed && progress.tone === "active" && "motion-safe:animate-pulse",
                )}
              />
              {step.short}
            </span>
            {index < STEPS.length - 1 && <span className="text-muted-foreground/40">›</span>}
          </li>
        );
      })}
    </ol>
  );
}

/** The batch at a glance: how many files, how many done, one bar for all. */
export function IngestionOverview({ items }: { items: FileProgress[] }) {
  const summary = useMemo(() => {
    const total = items.length;
    const done = items.filter((i) => i.tone === "done" || i.tone === "warning").length;
    const failed = items.filter((i) => i.tone === "error").length;
    const inFlight = total - done - failed;
    const percent = total ? Math.round(items.reduce((sum, i) => sum + (i.tone === "error" ? 100 : i.percent), 0) / total) : 0;
    return { total, done, failed, inFlight, percent };
  }, [items]);

  if (summary.total === 0) return null;

  const tone: Tone = summary.inFlight > 0 ? "active" : summary.failed > 0 ? "error" : "done";
  const headline =
    summary.inFlight > 0
      ? `Processing ${summary.total} file${summary.total === 1 ? "" : "s"}`
      : summary.failed > 0
        ? `Finished with ${summary.failed} failure${summary.failed === 1 ? "" : "s"}`
        : summary.total === 1
          ? "Your file is ready to answer questions"
          : `All ${summary.total} files ready to answer questions`;

  return (
    <div className="rounded-lg border border-border bg-muted/30 p-3" aria-live="polite">
      <div className="flex items-center gap-2">
        <StatusIcon tone={tone} />
        <p className="text-sm font-medium">{headline}</p>
        <span className="ml-auto text-sm font-medium tabular-nums">{summary.percent}%</span>
      </div>
      <ProgressBar percent={summary.percent} tone={tone} label="Overall ingestion progress" className="mt-2 h-2" />
      <p className="mt-1.5 text-xs text-muted-foreground">
        {summary.done} ready · {summary.inFlight} in progress
        {summary.failed > 0 && <span className="text-destructive"> · {summary.failed} failed</span>}
      </p>
    </div>
  );
}

export function Dropzone({
  onFiles,
  accept,
  hint,
}: {
  onFiles: (files: FileList) => void;
  accept: string;
  hint: string;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        if (e.dataTransfer.files.length > 0) onFiles(e.dataTransfer.files);
      }}
      className={cn(
        "flex flex-col items-center gap-2 rounded-lg border-2 border-dashed px-4 py-7 text-center transition-colors",
        dragging ? "border-primary bg-primary/5" : "border-border hover:border-primary/40",
      )}
    >
      <div className="rounded-full bg-primary/10 p-2.5">
        <Upload className="size-5 text-primary" />
      </div>
      <p className="text-sm">
        <span className="font-medium">Drop files here</span>, or{" "}
        <button
          type="button"
          className="font-medium text-primary underline-offset-4 hover:underline"
          onClick={() => inputRef.current?.click()}
        >
          browse
        </button>
      </p>
      <p className="text-xs text-muted-foreground">{hint}</p>
      <p className="text-xs text-muted-foreground">Uploads start straight away — you can cancel any file while it&rsquo;s sending.</p>
      <input
        ref={inputRef}
        type="file"
        multiple
        accept={accept}
        className="hidden"
        onChange={(e) => {
          if (e.target.files?.length) onFiles(e.target.files);
          // Reset so re-picking the same file fires `change` again.
          e.target.value = "";
        }}
      />
    </div>
  );
}
