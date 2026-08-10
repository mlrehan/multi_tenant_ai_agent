"use client";

import { use as usePromise, useRef, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  Database,
  FileText,
  Code2,
  Globe,
  Loader2,
  Plus,
  MessageSquare,
  RefreshCw,
  Search,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
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
import { IdentityChip } from "@/components/shared/identity-chip";
import { FieldError } from "@/components/shared/field-error";
import {
  useCreateKnowledgeBase,
  useKnowledgeBaseDocuments,
  useKnowledgeBases,
  useQueryKnowledgeBase,
  useUploadDocument,
  useCreateDataSource,
  useDataSources,
  useChatWidgets,
  useCreateChatWidget,
  useSetChatWidgetStatus,
  useRetryDocument,
  useDeleteDocument,
} from "@/features/ai-resources/hooks";
import { isApiError } from "@/lib/api-client";
import { streamAnswer } from "@/features/ai-resources/api";
import type {
  AnswerCitation,
  ChatWidget,
  CrawlMode,
  DataSource,
  KnowledgeBase,
  KnowledgeBaseDocument,
  KnowledgeBaseQueryHit,
  Visibility,
} from "@/lib/types";

/** Mirrors what the backend's parser dispatcher actually accepts (docling for
 * rich formats, dedicated parsers for CSV/JSON/XML). Kept in step with
 * `infrastructure/parsing/` -- this only filters the file picker, the server
 * still refuses an unsupported type with a 415. */
const ACCEPTED_FILE_TYPES = [
  ".pdf",
  ".docx",
  ".doc",
  ".xlsx",
  ".xls",
  ".pptx",
  ".ppt",
  ".csv",
  ".tsv",
  ".json",
  ".xml",
  ".png",
  ".jpg",
  ".jpeg",
  ".tiff",
  ".tif",
  ".bmp",
  ".webp",
].join(",");

/** Matches `MAX_UPLOAD_BYTES` in api/v1/assistants/router.py. Checked here
 * only to fail fast; the server's limit is the one that counts. */
const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;

/** The same list as `ACCEPTED_FILE_TYPES`, as a set for checking a dropped
 * file. The `accept` attribute only filters the *browse* dialog — a
 * drag-and-drop bypasses it entirely, so without this the two entry points
 * would validate differently and a `.zip` could reach the server on one path
 * and not the other. */
const ACCEPTED_EXTENSIONS = new Set(ACCEPTED_FILE_TYPES.split(","));

function extensionOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot === -1 ? "" : name.slice(dot).toLowerCase();
}

type StagedFile = {
  file: File;
  /** Identity for de-duplication and React keys. Name alone would reject a
   * legitimately different file that happens to share a name; name+size+mtime
   * is what a person means by "the same file". */
  key: string;
  status: "ready" | "uploading" | "done" | "error";
  error?: string;
};

function stagedKey(file: File): string {
  return `${file.name}:${file.size}:${file.lastModified}`;
}

const createSchema = z.object({
  name: z.string().min(1, "Enter a name.").max(200),
  description: z.string().optional(),
});
type CreateForm = z.infer<typeof createSchema>;

export default function KnowledgeBasesPage({
  params,
}: {
  params: Promise<{ tenantId: string }>;
}) {
  const { tenantId } = usePromise(params);
  const { data, isLoading, error } = useKnowledgeBases(tenantId);
  const [open, setOpen] = useState(false);

  const knowledgeBases = data?.knowledge_bases;

  return (
    <div>
      <PageHeader
        title="Knowledge bases"
        description="Document collections that assistants can search. Each one gets its own isolated vector namespace."
        actions={
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger render={<Button size="sm" />}>
              <Plus />
              New knowledge base
            </DialogTrigger>
            <CreateKnowledgeBaseDialog tenantId={tenantId} onDone={() => setOpen(false)} />
          </Dialog>
        }
      />

      {isLoading && <TableSkeleton rows={3} columns={4} />}
      {error && <ErrorState error={error} resource="knowledge bases" />}

      {knowledgeBases && knowledgeBases.length === 0 && (
        <EmptyState
          icon={Database}
          title="No knowledge bases yet"
          description="Create one to give your assistants source material to draw on."
          action={
            <Button size="sm" onClick={() => setOpen(true)}>
              <Plus />
              New knowledge base
            </Button>
          }
        />
      )}

      {knowledgeBases && knowledgeBases.length > 0 && (
        <Card>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Visibility</TableHead>
                  <TableHead>ID</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {knowledgeBases.map((kb) => (
                  <KnowledgeBaseRow key={kb.id} tenantId={tenantId} knowledgeBase={kb} />
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function KnowledgeBaseRow({
  tenantId,
  knowledgeBase,
}: {
  tenantId: string;
  knowledgeBase: KnowledgeBase;
}) {
  const [open, setOpen] = useState(false);
  const [documentsOpen, setDocumentsOpen] = useState(false);
  const [askOpen, setAskOpen] = useState(false);
  const [embedOpen, setEmbedOpen] = useState(false);

  return (
    <TableRow>
      <TableCell>
        <div className="font-medium">{knowledgeBase.name}</div>
        {knowledgeBase.description && (
          <p className="mt-0.5 text-xs text-muted-foreground">{knowledgeBase.description}</p>
        )}
      </TableCell>
      <TableCell>
        <Badge variant="secondary" className="capitalize">
          {knowledgeBase.visibility}
        </Badge>
      </TableCell>
      <TableCell>
        <IdentityChip value={knowledgeBase.id} label="knowledge base" />
      </TableCell>
      <TableCell className="text-right">
        <div className="flex justify-end gap-2">
          <Dialog open={documentsOpen} onOpenChange={setDocumentsOpen}>
            <DialogTrigger render={<Button size="xs" variant="outline" />}>
              <FileText />
              Documents
            </DialogTrigger>
            {/* Mounted only while open so the polling query in
                `useKnowledgeBaseDocuments` doesn't run for every row of a
                long list the moment the page loads. */}
            {documentsOpen && (
              <DocumentsDialog tenantId={tenantId} knowledgeBase={knowledgeBase} />
            )}
          </Dialog>
          <Dialog open={askOpen} onOpenChange={setAskOpen}>
            <DialogTrigger render={<Button size="xs" variant="outline" />}>
              <MessageSquare />
              Ask
            </DialogTrigger>
            {askOpen && <AskDialog tenantId={tenantId} knowledgeBase={knowledgeBase} />}
          </Dialog>
          <Dialog open={embedOpen} onOpenChange={setEmbedOpen}>
            <DialogTrigger render={<Button size="xs" variant="outline" />}>
              <Code2 />
              Embed
            </DialogTrigger>
            {embedOpen && (
              <EmbedDialog tenantId={tenantId} knowledgeBase={knowledgeBase} />
            )}
          </Dialog>
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger render={<Button size="xs" variant="outline" />}>
              <Search />
              Test search
            </DialogTrigger>
            <QueryDialog tenantId={tenantId} knowledgeBase={knowledgeBase} />
          </Dialog>
        </div>
      </TableCell>
    </TableRow>
  );
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function DocumentStatusBadge({ document }: { document: KnowledgeBaseDocument }) {
  if (document.status === "processing") {
    return (
      <Badge variant="secondary" className="gap-1">
        <Loader2 className="size-3 animate-spin" />
        Processing
      </Badge>
    );
  }
  if (document.status === "ready") {
    return (
      <Badge variant="secondary" className="gap-1 text-emerald-600 dark:text-emerald-400">
        <CheckCircle2 className="size-3" />
        Ready
      </Badge>
    );
  }
  return (
    <Badge variant="destructive" className="gap-1">
      <AlertCircle className="size-3" />
      Failed
    </Badge>
  );
}

/** Upload and ingestion status. Ingestion runs on a Celery worker, so a
 * document lands here as `processing` and settles to `ready` or `failed`
 * later -- the list polls itself until nothing is in flight. */
function DocumentsDialog({
  tenantId,
  knowledgeBase,
}: {
  tenantId: string;
  knowledgeBase: KnowledgeBase;
}) {
  const { data, isLoading, error } = useKnowledgeBaseDocuments(tenantId, knowledgeBase.id);
  const upload = useUploadDocument(tenantId, knowledgeBase.id);
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [staged, setStaged] = useState<StagedFile[]>([]);
  const [sending, setSending] = useState(false);

  const documents = data?.documents;

  /** The single entry point for both browse and drag-and-drop.
   *
   * Files are *staged*, not sent. Uploading on drop gives no chance to notice
   * the wrong file was picked, and no way to undo it once a background job has
   * started parsing it. Validation happens here too, so a `.zip` is refused
   * identically whichever way it arrived — the `accept` attribute only filters
   * the browse dialog and a drop bypasses it entirely.
   */
  function addFiles(incoming: FileList | File[]) {
    // Validation runs here, not inside the `setStaged` updater. React defers
    // an updater to the render phase, so anything it collects is still empty
    // when the code after `setStaged` runs -- an earlier version gathered the
    // rejections in there and every "unsupported file type" toast silently
    // never fired. Keeping the updater pure also means StrictMode's
    // double-invoke can't double a toast.
    const rejected: string[] = [];
    const accepted: StagedFile[] = [];
    const seen = new Set(staged.map((s) => s.key));

    for (const file of Array.from(incoming)) {
      const key = stagedKey(file);
      if (seen.has(key)) {
        rejected.push(`${file.name} is already in the list`);
        continue;
      }
      if (!ACCEPTED_EXTENSIONS.has(extensionOf(file.name))) {
        rejected.push(`${file.name} is not a supported file type`);
        continue;
      }
      if (file.size > MAX_UPLOAD_BYTES) {
        rejected.push(`${file.name} is larger than 50 MB`);
        continue;
      }
      if (file.size === 0) {
        rejected.push(`${file.name} is empty`);
        continue;
      }
      seen.add(key);
      accepted.push({ file, key, status: "ready" });
    }

    if (accepted.length) {
      setStaged((current) => {
        // `seen` came from a snapshot of `staged`; two drops in quick
        // succession can both read the same one, so dedupe once more against
        // what is actually in state.
        const present = new Set(current.map((s) => s.key));
        const fresh = accepted.filter((a) => !present.has(a.key));
        return fresh.length ? [...current, ...fresh] : current;
      });
    }

    for (const message of rejected) toast.error(message);
  }

  function removeStaged(key: string) {
    setStaged((current) => current.filter((s) => s.key !== key));
  }

  /** Sends everything staged, one at a time, keeping per-file status.
   *
   * Sequential rather than parallel: each upload triggers a background parse,
   * and ten at once is how a single tenant saturates the worker queue for
   * everyone else. Successful files leave the list; failures stay, with their
   * reason, so they can be retried without re-picking them.
   */
  async function uploadStaged() {
    setSending(true);
    try {
      for (const item of staged.filter((s) => s.status !== "done")) {
        setStaged((c) =>
          c.map((s) => (s.key === item.key ? { ...s, status: "uploading" } : s)),
        );
        try {
          await upload.mutateAsync(item.file);
          setStaged((c) => c.filter((s) => s.key !== item.key));
          toast.success(`${item.file.name} uploaded — indexing now.`);
        } catch (err) {
          const message = isApiError(err) ? err.message : "Upload failed.";
          setStaged((c) =>
            c.map((s) =>
              s.key === item.key ? { ...s, status: "error", error: message } : s,
            ),
          );
          toast.error(`${item.file.name}: ${message}`);
        }
      }
    } finally {
      setSending(false);
    }
  }

  return (
    <DialogContent className="sm:max-w-2xl">
      <DialogHeader>
        <DialogTitle>{knowledgeBase.name}</DialogTitle>
        <DialogDescription>
          Uploaded files are parsed, chunked and embedded in the background. They become
          searchable once indexing finishes.
        </DialogDescription>
      </DialogHeader>

      <div className="space-y-4 py-2">
        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            if (e.dataTransfer.files.length > 0) addFiles(e.dataTransfer.files);
          }}
          className={`flex flex-col items-center gap-2 rounded-lg border-2 border-dashed px-4 py-8 text-center transition-colors ${
            dragging ? "border-primary bg-primary/5" : "border-border"
          }`}
        >
          <Upload className="size-5 text-muted-foreground" />
          <p className="text-sm">
            Drop files here, or{" "}
            <button
              type="button"
              className="font-medium text-primary underline-offset-4 hover:underline"
              onClick={() => inputRef.current?.click()}
            >
              browse
            </button>
          </p>
          <p className="text-xs text-muted-foreground">
            PDF, Word, Excel, PowerPoint, CSV, JSON, XML or images — up to 50 MB each.
          </p>
          <input
            ref={inputRef}
            type="file"
            multiple
            accept={ACCEPTED_FILE_TYPES}
            className="hidden"
            onChange={(e) => {
              if (e.target.files?.length) addFiles(e.target.files);
              // Reset so re-picking the same file fires `change` again.
              e.target.value = "";
            }}
          />
        </div>

        {staged.length > 0 && (
          <div className="space-y-2 rounded-lg border border-border p-3">
            <div className="flex items-center justify-between gap-3">
              <p className="text-sm font-medium">
                {staged.length} file{staged.length === 1 ? "" : "s"} ready to upload
              </p>
              <div className="flex shrink-0 items-center gap-2">
                <Button
                  size="xs"
                  variant="ghost"
                  disabled={sending}
                  onClick={() => setStaged([])}
                >
                  Clear
                </Button>
                <Button size="xs" disabled={sending} onClick={() => void uploadStaged()}>
                  {sending && <Loader2 className="animate-spin" />}
                  {sending ? "Uploading…" : "Upload"}
                </Button>
              </div>
            </div>
            <ul className="space-y-1">
              {staged.map((item) => (
                <li
                  key={item.key}
                  className="flex items-center justify-between gap-3 rounded-md bg-muted/50 px-2.5 py-1.5"
                >
                  {/* min-w-0 is what lets `truncate` work inside a flex row —
                      without it the item refuses to shrink below its content
                      and a long filename widens the dialog. */}
                  <div className="min-w-0">
                    <p className="truncate text-sm">{item.file.name}</p>
                    {item.error && (
                      <p className="truncate text-xs text-destructive">{item.error}</p>
                    )}
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <span className="text-xs text-muted-foreground tabular-nums">
                      {formatBytes(item.file.size)}
                    </span>
                    {item.status === "uploading" ? (
                      <Loader2 className="size-3.5 animate-spin text-muted-foreground" />
                    ) : (
                      <button
                        type="button"
                        aria-label={`Remove ${item.file.name}`}
                        className="text-muted-foreground hover:text-foreground"
                        disabled={sending}
                        onClick={() => removeStaged(item.key)}
                      >
                        <X className="size-3.5" />
                      </button>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}

        <CrawlSection tenantId={tenantId} knowledgeBaseId={knowledgeBase.id} />

        {isLoading && <TableSkeleton rows={2} columns={3} />}
        {error && <ErrorState error={error} resource="documents" />}

        {documents && documents.length === 0 && (
          <p className="py-4 text-center text-sm text-muted-foreground">
            No documents yet.
          </p>
        )}

        {documents && documents.length > 0 && (
          <div className="max-h-80 overflow-y-auto rounded-md border border-border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>File</TableHead>
                  <TableHead className="w-20">Size</TableHead>
                  <TableHead className="w-20">Chunks</TableHead>
                  <TableHead className="w-28">Status</TableHead>
                  <TableHead className="w-28 text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {documents.map((doc) => (
                  <DocumentRow
                    key={doc.id}
                    tenantId={tenantId}
                    knowledgeBaseId={knowledgeBase.id}
                    document={doc}
                  />
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </div>
    </DialogContent>
  );
}

/** One document, with the actions a tenant admin needs when ingestion went
 * wrong: see why, try again, or remove it.
 *
 * Chunk count sits beside status deliberately. `Ready` answers "did the
 * pipeline finish"; the count answers "is there anything to find" -- and a
 * `Ready` row showing 0 chunks is exactly the case that used to look like
 * success and could answer nothing.
 */
function DocumentRow({
  tenantId,
  knowledgeBaseId,
  document,
}: {
  tenantId: string;
  knowledgeBaseId: string;
  document: KnowledgeBaseDocument;
}) {
  const retry = useRetryDocument(tenantId, knowledgeBaseId);
  const remove = useDeleteDocument(tenantId, knowledgeBaseId);
  const [confirming, setConfirming] = useState(false);

  const emptyButReady = document.status === "ready" && document.chunk_count === 0;

  async function handleRetry() {
    try {
      await retry.mutateAsync(document.id);
      toast.success(`Re-ingesting ${document.filename}.`);
    } catch (err) {
      toast.error(isApiError(err) ? err.message : "Couldn't start re-ingestion.");
    }
  }

  async function handleDelete() {
    try {
      await remove.mutateAsync(document.id);
      toast.success(`${document.filename} deleted.`);
    } catch (err) {
      toast.error(isApiError(err) ? err.message : "Couldn't delete the document.");
    } finally {
      setConfirming(false);
    }
  }

  return (
    <TableRow>
      {/* max-w-0 with w-full is the table-cell idiom for "take the remaining
          space but let truncate work" -- a cell sizes to content otherwise,
          and one long crawled-page title widens the whole dialog. */}
      <TableCell className="w-full max-w-0 align-top">
        <div className="truncate font-medium">{document.filename}</div>
        {/* `whitespace-normal` is load-bearing: TableCell sets
            `whitespace-nowrap`, which is right for the short cells but stops
            these notes wrapping, so they render as one long line straight
            across the columns to their right. */}
        {document.status === "failed" && document.failure_reason && (
          <p className="mt-0.5 text-xs whitespace-normal text-destructive">
            {document.failure_reason}
          </p>
        )}
        {emptyButReady && (
          <p className="mt-0.5 text-xs whitespace-normal text-amber-600 dark:text-amber-500">
            Indexed, but no searchable text was found — this file can&rsquo;t answer
            questions.
          </p>
        )}
      </TableCell>
      <TableCell className="align-top text-sm text-muted-foreground tabular-nums">
        {formatBytes(document.size_bytes)}
      </TableCell>
      <TableCell className="align-top text-sm tabular-nums">
        <span className={emptyButReady ? "text-amber-600 dark:text-amber-500" : ""}>
          {document.chunk_count}
        </span>
      </TableCell>
      <TableCell className="align-top">
        <DocumentStatusBadge document={document} />
      </TableCell>
      <TableCell className="align-top text-right">
        <div className="flex items-center justify-end gap-1">
          <Button
            size="xs"
            variant="ghost"
            aria-label={`Re-ingest ${document.filename}`}
            disabled={retry.isPending || document.status === "processing"}
            onClick={() => void handleRetry()}
          >
            {retry.isPending ? (
              <Loader2 className="animate-spin" />
            ) : (
              <RefreshCw />
            )}
          </Button>
          <Button
            size="xs"
            variant="ghost"
            aria-label={`Delete ${document.filename}`}
            className="text-muted-foreground hover:text-destructive"
            disabled={remove.isPending}
            onClick={() => setConfirming(true)}
          >
            <Trash2 />
          </Button>
        </div>

        {/* Destructive and irreversible -- the vectors and the stored file go
            too -- so it asks first. */}
        <AlertDialog open={confirming} onOpenChange={setConfirming}>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Delete this document?</AlertDialogTitle>
              <AlertDialogDescription>
                <span className="font-medium">{document.filename}</span> and everything
                indexed from it will be removed, including its searchable chunks and the
                stored file. Assistants will stop finding it. This cannot be undone.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel disabled={remove.isPending}>Cancel</AlertDialogCancel>
              <Button
                variant="destructive"
                size="sm"
                disabled={remove.isPending}
                onClick={() => void handleDelete()}
              >
                {remove.isPending && <Loader2 className="animate-spin" />}
                Delete
              </Button>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </TableCell>
    </TableRow>
  );
}

/** Runs a real retrieval query. The vector namespace is never sent by the
 * client -- the server derives it from the knowledge base you were already
 * authorized to read, which is what makes cross-tenant leakage impossible. */
function QueryDialog({
  tenantId,
  knowledgeBase,
}: {
  tenantId: string;
  knowledgeBase: KnowledgeBase;
}) {
  const runQuery = useQueryKnowledgeBase(tenantId, knowledgeBase.id);
  const [queryText, setQueryText] = useState("");
  const [hits, setHits] = useState<KnowledgeBaseQueryHit[] | null>(null);

  async function handleSearch() {
    try {
      const result = await runQuery.mutateAsync({ queryText });
      setHits(result.hits);
    } catch (err) {
      toast.error(isApiError(err) ? err.message : "The search didn't run.");
    }
  }

  return (
    <DialogContent>
      <DialogHeader>
        <DialogTitle>Search {knowledgeBase.name}</DialogTitle>
        <DialogDescription>
          Check what an assistant would retrieve for a given question.
        </DialogDescription>
      </DialogHeader>
      <div className="space-y-4 py-2">
        <div className="flex gap-2">
          <Input
            value={queryText}
            onChange={(e) => setQueryText(e.target.value)}
            placeholder="What is our refund policy?"
            onKeyDown={(e) => e.key === "Enter" && queryText.trim() && handleSearch()}
          />
          <Button size="sm" disabled={!queryText.trim() || runQuery.isPending} onClick={handleSearch}>
            {runQuery.isPending ? "Searching…" : "Search"}
          </Button>
        </div>

        {hits && hits.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No matches. This knowledge base may not have any documents indexed yet.
          </p>
        )}
        {hits && hits.length > 0 && (
          <ul className="space-y-2">
            {hits.map((hit) => (
              <li
                key={hit.document_id}
                className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2"
              >
                <span className="truncate text-sm">{hit.filename}</span>
                <span className="font-mono text-xs text-muted-foreground tabular-nums">
                  {hit.score.toFixed(3)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </DialogContent>
  );
}

function CreateKnowledgeBaseDialog({
  tenantId,
  onDone,
}: {
  tenantId: string;
  onDone: () => void;
}) {
  const createKb = useCreateKnowledgeBase(tenantId);
  const [visibility, setVisibility] = useState<Visibility>("tenant");
  const [departmentId, setDepartmentId] = useState("");
  const [teamId, setTeamId] = useState("");
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<CreateForm>({ resolver: zodResolver(createSchema) });

  async function onSubmit(values: CreateForm) {
    try {
      await createKb.mutateAsync({
        name: values.name,
        description: values.description?.trim() || null,
        visibility,
        departmentId: visibility === "department" ? departmentId.trim() || null : null,
        teamId: visibility === "team" ? teamId.trim() || null : null,
      });
      toast.success(`Created ${values.name}`);
      reset();
      onDone();
    } catch (err) {
      toast.error(isApiError(err) ? err.message : "Couldn't create the knowledge base.");
    }
  }

  return (
    <DialogContent>
      <form onSubmit={handleSubmit(onSubmit)}>
        <DialogHeader>
          <DialogTitle>New knowledge base</DialogTitle>
          <DialogDescription>
            The storage location and vector namespace are assigned automatically.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4 py-4">
          <div>
            <Label htmlFor="kb-name">Name</Label>
            <Input id="kb-name" className="mt-1.5" {...register("name")} />
            <FieldError message={errors.name?.message} />
          </div>
          <div>
            <Label htmlFor="kb-description">Description</Label>
            <Input id="kb-description" className="mt-1.5" {...register("description")} />
          </div>
          <div>
            <Label htmlFor="kb-visibility">Visibility</Label>
            <Select value={visibility} onValueChange={(v) => setVisibility(v as Visibility)}>
              <SelectTrigger id="kb-visibility" className="mt-1.5 w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="tenant">Tenant — everyone</SelectItem>
                <SelectItem value="department">Department</SelectItem>
                <SelectItem value="team">Team</SelectItem>
                <SelectItem value="restricted">Restricted</SelectItem>
              </SelectContent>
            </Select>
          </div>
          {visibility === "department" && (
            <div>
              <Label htmlFor="kb-department">Department ID</Label>
              <Input
                id="kb-department"
                value={departmentId}
                onChange={(e) => setDepartmentId(e.target.value)}
                className="mt-1.5 font-mono text-xs"
              />
            </div>
          )}
          {visibility === "team" && (
            <div>
              <Label htmlFor="kb-team">Team ID</Label>
              <Input
                id="kb-team"
                value={teamId}
                onChange={(e) => setTeamId(e.target.value)}
                className="mt-1.5 font-mono text-xs"
              />
            </div>
          )}
        </div>
        <DialogFooter>
          <Button type="submit" size="sm" disabled={createKb.isPending}>
            {createKb.isPending ? "Creating…" : "Create knowledge base"}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}


function SyncStatusBadge({ source }: { source: DataSource }) {
  if (source.sync_status === "syncing") {
    return (
      <Badge variant="secondary" className="gap-1">
        <Loader2 className="size-3 animate-spin" />
        Crawling
      </Badge>
    );
  }
  if (source.sync_status === "ready") {
    return (
      <Badge variant="secondary" className="gap-1 text-emerald-600 dark:text-emerald-400">
        <CheckCircle2 className="size-3" />
        {source.pages_indexed} of {source.pages_discovered} pages
      </Badge>
    );
  }
  if (source.sync_status === "error") {
    return (
      <Badge variant="destructive" className="gap-1">
        <AlertCircle className="size-3" />
        Failed
      </Badge>
    );
  }
  return <Badge variant="secondary">Queued</Badge>;
}

/** Add web content to a knowledge base: a list of specific URLs, or a whole
 * site crawled from one starting point.
 *
 * Deliberately offers no depth/page/timeout controls. Those are platform
 * limits that bound what this deployment spends on a tenant's behalf — making
 * them tenant-editable would defeat the only reason they exist. */
function CrawlSection({
  tenantId,
  knowledgeBaseId,
}: {
  tenantId: string;
  knowledgeBaseId: string;
}) {
  const { data } = useDataSources(tenantId, knowledgeBaseId);
  const createSource = useCreateDataSource(tenantId, knowledgeBaseId);
  const [urlText, setUrlText] = useState("");
  const [mode, setMode] = useState<CrawlMode>("url_list");

  const urls = urlText
    .split(/[\s,]+/)
    .map((u) => u.trim())
    .filter(Boolean);
  // A site crawl follows links from *a* starting point; several start URLs
  // would be several crawls sharing one status and one page budget. The
  // backend refuses it too — this just says so before the round trip.
  const tooManyForSite = mode === "site" && urls.length > 1;

  async function handleAdd() {
    try {
      await createSource.mutateAsync({ urls, mode });
      toast.success(
        mode === "site" ? "Crawling the site now." : `Fetching ${urls.length} URL(s) now.`,
      );
      setUrlText("");
    } catch (err) {
      toast.error(isApiError(err) ? err.message : "Couldn't start the crawl.");
    }
  }

  return (
    <div className="space-y-3 rounded-lg border border-border p-4">
      <div className="flex items-center gap-2">
        <Globe className="size-4 text-muted-foreground" />
        <h3 className="text-sm font-medium">Add from the web</h3>
      </div>

      <Select value={mode} onValueChange={(v) => setMode(v as CrawlMode)}>
        <SelectTrigger className="w-full">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="url_list">Specific URLs — fetch exactly these pages</SelectItem>
          <SelectItem value="site">Entire website — follow links from one page</SelectItem>
        </SelectContent>
      </Select>

      <Input
        value={urlText}
        onChange={(e) => setUrlText(e.target.value)}
        placeholder={
          mode === "site" ? "https://example.com/docs" : "One or more URLs, space or comma separated"
        }
      />

      {tooManyForSite && (
        <p className="text-xs text-destructive">
          A site crawl takes exactly one starting URL.
        </p>
      )}

      <div className="flex items-center justify-between gap-3">
        <p className="text-xs text-muted-foreground">
          {mode === "site"
            ? "Follows links within the same site, up to the platform's page and depth limits."
            : "Fetches only the pages you list. No links are followed."}
        </p>
        <Button
          size="sm"
          disabled={urls.length === 0 || tooManyForSite || createSource.isPending}
          onClick={handleAdd}
        >
          {createSource.isPending ? "Starting…" : "Start"}
        </Button>
      </div>

      {data && data.data_sources.length > 0 && (
        <ul className="space-y-2 border-t border-border pt-3">
          {data.data_sources.map((source) => (
            <li key={source.id} className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="truncate text-sm">{source.urls.join(", ")}</p>
                {source.sync_status === "error" && source.failure_reason && (
                  <p className="mt-0.5 text-xs text-destructive">{source.failure_reason}</p>
                )}
              </div>
              <SyncStatusBadge source={source} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Ask the knowledge base a question and watch the answer stream in.
 *
 * Sources render before the first token: the backend sends them first because
 * they are known before generation begins. A reader can therefore see what the
 * answer is allowed to draw on even if generation fails partway. */
function AskDialog({
  tenantId,
  knowledgeBase,
}: {
  tenantId: string;
  knowledgeBase: KnowledgeBase;
}) {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [citations, setCitations] = useState<AnswerCitation[]>([]);
  const [cited, setCited] = useState<string[]>([]);
  const [streaming, setStreaming] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  async function ask() {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setAnswer("");
    setCitations([]);
    setCited([]);
    setStreaming(true);
    try {
      for await (const frame of streamAnswer(
        tenantId,
        knowledgeBase.id,
        question,
        controller.signal,
      )) {
        if (frame.event === "sources") {
          setCitations((frame.data.citations as AnswerCitation[]) ?? []);
        } else if (frame.event === "token") {
          setAnswer((prev) => prev + (frame.data.text as string));
        } else if (frame.event === "done") {
          setCited((frame.data.cited as string[]) ?? []);
        } else if (frame.event === "error") {
          toast.error(String(frame.data.detail ?? "The answer could not be completed."));
        }
      }
    } catch (err) {
      if (!controller.signal.aborted) {
        toast.error(isApiError(err) ? err.message : "The answer could not be started.");
      }
    } finally {
      setStreaming(false);
    }
  }

  return (
    <DialogContent className="sm:max-w-2xl">
      <DialogHeader>
        <DialogTitle>Ask {knowledgeBase.name}</DialogTitle>
        <DialogDescription>
          Answers are generated only from documents in this knowledge base, with citations. If
          nothing here covers the question, it says so rather than guessing.
        </DialogDescription>
      </DialogHeader>

      <div className="space-y-4 py-2">
        <div className="flex gap-2">
          <Input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="What is our refund policy?"
            onKeyDown={(e) => e.key === "Enter" && question.trim() && !streaming && ask()}
          />
          <Button size="sm" disabled={!question.trim() || streaming} onClick={ask}>
            {streaming ? <Loader2 className="size-4 animate-spin" /> : "Ask"}
          </Button>
        </div>

        {citations.length > 0 && (
          <div className="rounded-md border border-border p-3">
            <p className="mb-2 text-xs font-medium text-muted-foreground">
              Sources the answer may draw on
            </p>
            <ul className="space-y-1">
              {citations.map((c) => (
                <li key={c.label} className="flex items-center gap-2 text-xs">
                  {/* Dimmed unless the answer actually cited it — "offered" and
                      "used" are different, and conflating them overstates what
                      the answer rests on. */}
                  <Badge variant={cited.includes(c.label) ? "secondary" : "outline"}>
                    [{c.label}]
                  </Badge>
                  <span className="truncate text-muted-foreground">
                    {c.source_location ?? c.document_id}
                  </span>
                  <span className="ml-auto font-mono tabular-nums text-muted-foreground">
                    {c.relevance.toFixed(3)}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {answer && (
          <div className="whitespace-pre-wrap rounded-md bg-muted/40 p-3 text-sm">
            {answer}
            {streaming && <span className="ml-0.5 animate-pulse">▌</span>}
          </div>
        )}
      </div>
    </DialogContent>
  );
}

/** Publishing a knowledge base to the open internet.
 *
 * Deliberately the most explicit dialog on this screen. Everything else here
 * changes what a *tenant's own members* can see; this one makes a slice of the
 * corpus answerable by anonymous strangers, so the copy says so plainly rather
 * than presenting it as one more toggle.
 */
function EmbedDialog({
  tenantId,
  knowledgeBase,
}: {
  tenantId: string;
  knowledgeBase: KnowledgeBase;
}) {
  const widgets = useChatWidgets(tenantId);
  const createWidget = useCreateChatWidget(tenantId);
  const setStatus = useSetChatWidgetStatus(tenantId);
  const [name, setName] = useState("Help widget");
  const [origins, setOrigins] = useState("");
  const [limit, setLimit] = useState(500);

  const mine = (widgets.data?.chat_widgets ?? []).filter(
    (w) => w.knowledge_base_id === knowledgeBase.id,
  );

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const parsed = origins
      .split(/[\n,]/)
      .map((o) => o.trim())
      .filter(Boolean);
    if (parsed.length === 0) {
      toast.error("Add at least one website address where this may be embedded.");
      return;
    }
    try {
      await createWidget.mutateAsync({
        knowledge_base_id: knowledgeBase.id,
        name,
        allowed_origins: parsed,
        daily_question_limit: limit,
      });
      setOrigins("");
      toast.success("Widget created.");
    } catch (error) {
      toast.error(isApiError(error) ? error.message : "Could not create the widget.");
    }
  }

  return (
    <DialogContent className="sm:max-w-2xl">
      <DialogHeader>
        <DialogTitle>Embed “{knowledgeBase.name}” on a website</DialogTitle>
        <DialogDescription>
          A widget lets anyone visiting the listed websites ask questions answered
          from this knowledge base — no sign-in. Only add sites you control.
        </DialogDescription>
      </DialogHeader>

      <div className="space-y-4">
        {mine.length > 0 && (
          <div className="space-y-3">
            {mine.map((widget) => (
              <WidgetCard
                key={widget.id}
                widget={widget}
                onToggle={(enabled) =>
                  setStatus
                    .mutateAsync({ widgetId: widget.id, enabled })
                    .then(() =>
                      toast.success(enabled ? "Widget enabled." : "Widget disabled."),
                    )
                    .catch(() => toast.error("Could not change the widget."))
                }
                busy={setStatus.isPending}
              />
            ))}
          </div>
        )}

        <form onSubmit={submit} className="space-y-3 rounded-lg border p-4">
          <div className="space-y-1.5">
            <Label htmlFor="widget-name">Name</Label>
            <Input
              id="widget-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="widget-origins">Allowed websites</Label>
            <Input
              id="widget-origins"
              placeholder="https://example.com, https://support.example.com"
              value={origins}
              onChange={(e) => setOrigins(e.target.value)}
            />
            {/* Says the quiet part out loud: exact matching is a deliberate
                choice, not a missing feature. `*.example.com` is how origin
                checks get broken -- a naive suffix match also accepts
                `evil-example.com`. */}
            <p className="text-xs text-muted-foreground">
              One address per site, separated by commas. Wildcards are not
              supported — list each subdomain you use.
            </p>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="widget-limit">Questions per day</Label>
            <Input
              id="widget-limit"
              type="number"
              min={1}
              max={100000}
              value={limit}
              onChange={(e) => setLimit(Number(e.target.value))}
            />
            <p className="text-xs text-muted-foreground">
              Caps what this widget can cost you in a day. Once reached, it stops
              answering until tomorrow.
            </p>
          </div>
          <Button type="submit" size="sm" disabled={createWidget.isPending}>
            {createWidget.isPending && <Loader2 className="animate-spin" />}
            Create widget
          </Button>
        </form>
      </div>
    </DialogContent>
  );
}

function WidgetCard({
  widget,
  onToggle,
  busy,
}: {
  widget: ChatWidget;
  onToggle: (enabled: boolean) => void;
  busy: boolean;
}) {
  // Straight from the API. The console cannot build this itself: it has no
  // public backend origin by design (every call goes through a same-origin
  // server-side proxy), so anything assembled here would point at that proxy
  // -- which needs a session and works on no third-party site at all.
  const snippet = widget.embed_snippet;

  return (
    <Card>
      <CardContent className="space-y-3 pt-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="font-medium">{widget.name}</div>
            <p className="text-xs text-muted-foreground">
              {widget.allowed_origins.join(", ")} · {widget.daily_question_limit}/day
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Badge variant={widget.status === "active" ? "default" : "secondary"}>
              {widget.status === "active" ? "Live" : "Off"}
            </Badge>
            <Button
              size="xs"
              variant="outline"
              disabled={busy}
              onClick={() => onToggle(widget.status !== "active")}
            >
              {widget.status === "active" ? "Turn off" : "Turn on"}
            </Button>
          </div>
        </div>
        <div className="space-y-1.5">
          <Label className="text-xs">Paste this into your site&rsquo;s HTML</Label>
          <pre className="overflow-x-auto rounded-md bg-muted p-3 text-xs">
            {snippet}
          </pre>
          <Button
            size="xs"
            variant="outline"
            onClick={() => {
              navigator.clipboard.writeText(snippet);
              toast.success("Embed code copied.");
            }}
          >
            Copy embed code
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
