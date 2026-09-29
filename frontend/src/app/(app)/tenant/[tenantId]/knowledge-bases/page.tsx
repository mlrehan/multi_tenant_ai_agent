"use client";

import { use as usePromise, useState } from "react";
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
  X,
} from "lucide-react";
import { toast } from "sonner";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
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
import { useTenantPlan } from "@/features/chatbot/hooks";
import {
  useCreateKnowledgeBase,
  useKnowledgeBaseDocuments,
  useKnowledgeBases,
  useQueryKnowledgeBase,
  useCreateDataSource,
  useDataSources,
  useChatWidgets,
  useCreateChatWidget,
  useDeleteChatWidget,
  useSetChatWidgetStatus,
  useUpdateChatWidget,
  useRetryDocument,
  useDeleteDocument,
  useDeleteKnowledgeBase,
  useDocumentDetail,
  useResyncDataSource,
} from "@/features/ai-resources/hooks";
import { isApiError } from "@/lib/api-client";
import { AskChat } from "@/features/ai-resources/chat/ask-chat";
import {
  Dropzone,
  IngestionOverview,
  ProgressBar,
  StageStepper,
  StatusIcon,
  documentProgress,
  formatBytes,
  uploadProgress,
  useUploadQueue,
  type LocalUpload,
} from "@/features/ai-resources/ingestion";
import type {
  ChatWidget,
  CrawlMode,
  DataSource,
  KnowledgeBase,
  KnowledgeBaseDocument,
  KnowledgeBaseQueryHit,
  Visibility,
} from "@/lib/types";

/** Mirrors `_ACCEPTED_EXTENSIONS` in `infrastructure/parsing/dispatcher.py`.
 *
 * This only filters the file picker; the server is the authority and refuses
 * an unsupported type with a 415. But the two must not drift, and they had:
 * the extraction rebuild added HTML, `.eml`, plain text, markdown, EPUB and
 * OpenDocument on the backend while this list stayed as it was, so those
 * formats were accepted by the API and unreachable from the console -- a
 * feature that exists and cannot be used is indistinguishable from one that
 * was never built.
 *
 * Note the server decides from the file's *contents*, not its extension, so a
 * name that passes here can still be refused. That asymmetry is deliberate:
 * this list is a convenience, not a security control. */
const ACCEPTED_FILE_TYPES = [
  ".pdf",
  ".docx",
  ".doc",
  ".xlsx",
  ".xls",
  ".pptx",
  ".ppt",
  ".odt",
  ".ods",
  ".odp",
  ".epub",
  ".csv",
  ".tsv",
  ".txt",
  ".text",
  ".log",
  ".md",
  ".markdown",
  ".json",
  ".jsonl",
  ".ndjson",
  ".xml",
  ".html",
  ".htm",
  ".eml",
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

const VISIBILITY_LABELS: Record<Visibility, string> = {
  tenant: "Tenant — everyone",
  department: "Department",
  team: "Team",
  restricted: "Restricted",
};

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
  const plan = useTenantPlan(tenantId);
  const [open, setOpen] = useState(false);
  // The knowledge base just created, whose documents dialog opens by itself:
  // a new knowledge base is empty, and adding sources is the obvious next step.
  const [justCreated, setJustCreated] = useState<string | null>(null);

  const knowledgeBases = data?.knowledge_bases;

  // The plan's ceiling, against the count we already have loaded. The list is
  // used rather than `plan.knowledge_bases_used` because it refreshes the
  // instant one is created, so the button disables without waiting for a
  // second query to catch up.
  //
  // **`null` means uncapped and is deliberately not `0`.** Collapsing them
  // would make an unset limit read as "none allowed" and lock every tenant out.
  const kbLimit = plan.data?.max_knowledge_bases ?? null;
  //
  // **Fails toward enabled.** This button is a hint; the server is the gate
  // (`guard_knowledge_base_quota` answers 409 and cannot be bypassed by an API
  // call). So a plan query that is loading or has failed leaves the button
  // usable rather than locking a tenant out of their own console over an
  // unavailable read -- they get a clear refusal instead of a dead control.
  const atKbLimit =
    kbLimit !== null && knowledgeBases !== undefined && knowledgeBases.length >= kbLimit;

  return (
    <div>
      <PageHeader
        title="Knowledge bases"
        description="The documents and web pages your chatbot answers from. Each knowledge base is searched in isolation, never mixed with another tenant's content."
        actions={
          <div className="flex items-center gap-3">
            {atKbLimit && (
              <span className="text-xs text-muted-foreground">
                Plan limit reached ({knowledgeBases?.length}/{kbLimit}). Ask your
                platform administrator to raise it.
              </span>
            )}
            <Dialog open={open} onOpenChange={setOpen}>
              <DialogTrigger render={<Button size="sm" disabled={atKbLimit} />}>
                <Plus />
                New knowledge base
              </DialogTrigger>
              <CreateKnowledgeBaseDialog
                tenantId={tenantId}
                onDone={(id) => {
                  setOpen(false);
                  setJustCreated(id);
                }}
              />
            </Dialog>
          </div>
        }
      />

      {isLoading && <TableSkeleton rows={3} columns={4} />}
      {error && <ErrorState error={error} resource="knowledge bases" />}

      {knowledgeBases && knowledgeBases.length === 0 && (
        <EmptyState
          icon={Database}
          title="No knowledge bases yet"
          description="Create one to give your chatbot something to answer from."
          action={
            <Button size="sm" disabled={atKbLimit} onClick={() => setOpen(true)}>
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
                  <KnowledgeBaseRow
                    key={kb.id}
                    tenantId={tenantId}
                    knowledgeBase={kb}
                    openDocumentsOnMount={kb.id === justCreated}
                  />
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
  openDocumentsOnMount = false,
}: {
  tenantId: string;
  knowledgeBase: KnowledgeBase;
  openDocumentsOnMount?: boolean;
}) {
  const [open, setOpen] = useState(false);
  // A row mounts for the first time when its knowledge base has just been
  // created, which is exactly when this should start open.
  const [documentsOpen, setDocumentsOpen] = useState(openDocumentsOnMount);
  const [askOpen, setAskOpen] = useState(false);
  const [embedOpen, setEmbedOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);

  return (
    <TableRow>
      {/* `whitespace-normal`: TableCell sets nowrap, so a long description
          stretched the table sideways and pushed the actions out of view. */}
      <TableCell className="max-w-md whitespace-normal">
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
          <Button
            size="xs"
            variant="ghost"
            aria-label={`Delete ${knowledgeBase.name}`}
            className="text-muted-foreground hover:text-destructive"
            onClick={() => setDeleteOpen(true)}
          >
            <Trash2 />
          </Button>
          <AlertDialog open={deleteOpen} onOpenChange={setDeleteOpen}>
            {/* Mounted only while open: it reads the document list to say
                exactly what will be removed. */}
            {deleteOpen && (
              <DeleteKnowledgeBaseDialog
                tenantId={tenantId}
                knowledgeBase={knowledgeBase}
                onDone={() => setDeleteOpen(false)}
              />
            )}
          </AlertDialog>
        </div>
      </TableCell>
    </TableRow>
  );
}

/** Deleting a knowledge base: what goes, what stays, and a typed confirmation.
 *
 * Typing the name is deliberate friction -- this removes every document and
 * passage in it at once, and cannot be undone. A refusal from the server (a
 * chatbot still answers from it, or something is still processing) is shown
 * here, in the dialog, because that is where the person is looking. */
function DeleteKnowledgeBaseDialog({
  tenantId,
  knowledgeBase,
  onDone,
}: {
  tenantId: string;
  knowledgeBase: KnowledgeBase;
  onDone: () => void;
}) {
  const remove = useDeleteKnowledgeBase(tenantId);
  const { data } = useKnowledgeBaseDocuments(tenantId, knowledgeBase.id);
  const [typed, setTyped] = useState("");
  const [refusal, setRefusal] = useState<string | null>(null);

  const documents = data?.documents;
  const passages = documents?.reduce((total, d) => total + d.chunk_count, 0);
  const confirmed = typed.trim() === knowledgeBase.name;

  async function handleDelete() {
    setRefusal(null);
    try {
      await remove.mutateAsync(knowledgeBase.id);
      toast.success(`${knowledgeBase.name} deleted.`);
      onDone();
    } catch (err) {
      setRefusal(isApiError(err) ? err.message : "Couldn't delete the knowledge base.");
    }
  }

  return (
    <AlertDialogContent>
      <AlertDialogHeader>
        <AlertDialogTitle>Delete “{knowledgeBase.name}”?</AlertDialogTitle>
        <AlertDialogDescription>
          This permanently removes the knowledge base and everything your chatbot could
          find in it. It cannot be undone.
        </AlertDialogDescription>
      </AlertDialogHeader>

      <ul className="space-y-1.5 rounded-md border border-border bg-muted/30 p-3 text-sm">
        <li>
          {documents === undefined ? (
            "Counting documents…"
          ) : (
            <>
              <span className="font-medium">
                {documents.length} document{documents.length === 1 ? "" : "s"}
              </span>{" "}
              and <span className="font-medium">{passages} searchable passage{passages === 1 ? "" : "s"}</span>{" "}
              will be removed from search, and the stored files deleted.
            </>
          )}
        </li>
        <li className="text-muted-foreground">
          Web sources added to it stop syncing. Answer ratings and the audit trail are kept.
        </li>
      </ul>

      <div>
        <Label htmlFor={`confirm-${knowledgeBase.id}`}>
          Type <span className="font-semibold">{knowledgeBase.name}</span> to confirm
        </Label>
        <Input
          id={`confirm-${knowledgeBase.id}`}
          className="mt-1.5"
          autoComplete="off"
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && confirmed && !remove.isPending && void handleDelete()}
        />
      </div>

      {refusal && (
        <p role="alert" className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {refusal}
        </p>
      )}

      <AlertDialogFooter>
        <AlertDialogCancel disabled={remove.isPending}>Cancel</AlertDialogCancel>
        <Button
          variant="destructive"
          size="sm"
          disabled={!confirmed || remove.isPending}
          onClick={() => void handleDelete()}
        >
          {remove.isPending && <Loader2 className="animate-spin" />}
          Delete knowledge base
        </Button>
      </AlertDialogFooter>
    </AlertDialogContent>
  );
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

/** Upload and ingestion, live.
 *
 * Files join the list the moment they are picked and start uploading at once
 * (two at a time), each with its own byte-level progress. Once the server has
 * them, the same row carries on through the worker's stages -- extracting,
 * chunking, embedding, indexing -- from the documents list, which polls every
 * 1.5 s while anything is in flight. An overall bar sums up the batch.
 *
 * Validation stays here, not in the queue: a `.zip` is refused identically
 * whether it was browsed or dropped, because the `accept` attribute only
 * filters the browse dialog and a drop bypasses it. */
function DocumentsDialog({
  tenantId,
  knowledgeBase,
}: {
  tenantId: string;
  knowledgeBase: KnowledgeBase;
}) {
  const { data, isLoading, error } = useKnowledgeBaseDocuments(tenantId, knowledgeBase.id);
  const queue = useUploadQueue(tenantId, knowledgeBase.id);
  const documents = data?.documents;

  // Every document seen in flight while this dialog is open -- including one
  // started with Retry, or already processing when it opened -- so the batch
  // summary keeps counting it after it finishes. Updated during render (the
  // documented pattern for deriving state from changing props), only when a
  // new id appears.
  const [tracked, setTracked] = useState<string[]>([]);
  const newlyInFlight = (documents ?? [])
    .filter((d) => d.status === "processing" && !tracked.includes(d.id))
    .map((d) => d.id);
  if (newlyInFlight.length > 0) setTracked([...tracked, ...newlyInFlight]);

  function addFiles(incoming: FileList | File[]) {
    // Validation runs here, not inside a state updater. React defers an
    // updater to the render phase, so rejections collected in there were still
    // empty when the toasts ran and every "unsupported file type" message was
    // silently dropped once before.
    const rejected: string[] = [];
    const accepted: { file: File; key: string }[] = [];
    const seen = new Set(queue.uploads.map((u) => u.key));

    for (const file of Array.from(incoming)) {
      const key = uploadKey(file);
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
      accepted.push({ file, key });
    }

    if (accepted.length) queue.add(accepted);
    for (const message of rejected) toast.error(message);
  }

  // A local upload is shown until the server's list contains the document it
  // became; from then on the document row carries the file.
  const listedIds = new Set(documents?.map((d) => d.id));
  const localRows = queue.uploads.filter((u) => !(u.documentId && listedIds.has(u.documentId)));
  const sessionIds = new Set(queue.uploads.flatMap((u) => (u.documentId ? [u.documentId] : [])));

  // Newest first, so a file just added is at the top, next to its upload.
  const sortedDocuments = documents
    ? [...documents].sort((a, b) => b.created_at.localeCompare(a.created_at))
    : undefined;

  // The batch: everything uploaded in this dialog, plus everything seen in
  // flight while it has been open.
  const batch = [
    ...localRows.map(uploadProgress),
    ...(sortedDocuments ?? [])
      .filter((d) => sessionIds.has(d.id) || tracked.includes(d.id) || d.status === "processing")
      .map(documentProgress),
  ];

  const readyCount = documents?.filter((d) => d.status === "ready").length ?? 0;

  return (
    <DialogContent className="sm:max-w-3xl">
      <DialogHeader>
        <DialogTitle>{knowledgeBase.name}</DialogTitle>
        <DialogDescription>
          Add files or a website. Each file is uploaded, read, split into passages and
          indexed for search — you can follow every step below.
        </DialogDescription>
      </DialogHeader>

      <div className="space-y-4 py-2">
        <Dropzone
          onFiles={addFiles}
          accept={ACCEPTED_FILE_TYPES}
          hint="PDF, Word, Excel, PowerPoint, HTML, email, CSV, JSON, XML, text or images — up to 50 MB each."
        />

        <IngestionOverview items={batch} />

        {/* The list comes straight after the upload, so a file is seen the
            moment it is added; the web source is the secondary way in. */}
        <div>
          <div className="mb-2 flex items-baseline justify-between">
            <h3 className="text-sm font-medium">Documents</h3>
            {documents && (
              <span className="text-xs text-muted-foreground">
                {readyCount} of {documents.length} ready
              </span>
            )}
          </div>

          {isLoading && <TableSkeleton rows={2} columns={3} />}
          {error && <ErrorState error={error} resource="documents" />}

          {documents && documents.length === 0 && localRows.length === 0 && (
            <p className="rounded-md border border-dashed border-border py-6 text-center text-sm text-muted-foreground">
              No documents yet. Drop a file above to get started.
            </p>
          )}

          {(localRows.length > 0 || (sortedDocuments && sortedDocuments.length > 0)) && (
            <ul className="max-h-[26rem] divide-y divide-border overflow-y-auto rounded-md border border-border">
              {localRows.map((upload) => (
                <LocalUploadRow
                  key={upload.key}
                  upload={upload}
                  onCancel={() => queue.cancel(upload.key)}
                  onRetry={() => queue.retry(upload.key)}
                  onDismiss={() => queue.dismiss(upload.key)}
                />
              ))}
              {sortedDocuments?.map((doc) => (
                <DocumentRow
                  key={doc.id}
                  tenantId={tenantId}
                  knowledgeBaseId={knowledgeBase.id}
                  document={doc}
                />
              ))}
            </ul>
          )}
        </div>

        <CrawlSection tenantId={tenantId} knowledgeBaseId={knowledgeBase.id} />
      </div>
    </DialogContent>
  );
}

function uploadKey(file: File): string {
  // Name alone would refuse a different file that shares a name; name, size
  // and modification time is what a person means by "the same file".
  return `${file.name}:${file.size}:${file.lastModified}`;
}

/** A file still in this browser: waiting, sending, or refused by the server. */
function LocalUploadRow({
  upload,
  onCancel,
  onRetry,
  onDismiss,
}: {
  upload: LocalUpload;
  onCancel: () => void;
  onRetry: () => void;
  onDismiss: () => void;
}) {
  const progress = uploadProgress(upload);
  return (
    <li className="px-3 py-2.5">
      <div className="flex items-start gap-3">
        <StatusIcon tone={progress.tone} className="mt-0.5 shrink-0" />
        <div className="min-w-0 flex-1 space-y-1.5">
          <div className="flex items-center gap-2">
            <p className="min-w-0 flex-1 truncate text-sm font-medium">{upload.file.name}</p>
            {progress.tone !== "error" && (
              <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                {progress.percent}%
              </span>
            )}
          </div>
          <p className={`text-xs ${progress.tone === "error" ? "text-destructive" : "text-muted-foreground"}`}>
            {progress.label}
            {progress.detail && ` · ${progress.detail}`}
          </p>
          <StageStepper progress={progress} />
          {progress.tone !== "error" && (
            <ProgressBar percent={progress.percent} tone={progress.tone} label={`${upload.file.name} progress`} />
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {upload.phase === "error" ? (
            <>
              <Button size="xs" variant="ghost" aria-label={`Retry ${upload.file.name}`} onClick={onRetry}>
                <RefreshCw />
              </Button>
              <Button size="xs" variant="ghost" aria-label={`Dismiss ${upload.file.name}`} onClick={onDismiss}>
                <X />
              </Button>
            </>
          ) : upload.phase !== "uploaded" ? (
            <Button size="xs" variant="ghost" aria-label={`Cancel ${upload.file.name}`} onClick={onCancel}>
              <X />
            </Button>
          ) : null}
        </div>
      </div>
    </li>
  );
}

/** One document on the server, from queued to ready or failed.
 *
 * In flight it shows its stage, the stage's own detail ("120 of 466
 * passages") and its bar; once finished it collapses to one line. The chunk
 * count stays visible when ready, because `Ready` with 0 passages is the case
 * that used to look like success and could answer nothing. */
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
  const [inspecting, setInspecting] = useState(false);

  const progress = documentProgress(document);
  const inFlight = document.status === "processing";
  const failed = document.status === "failed";

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
    <li className="px-3 py-2.5">
      <div className="flex items-start gap-3">
        <StatusIcon tone={progress.tone} className="mt-0.5 shrink-0" />
        <div className="min-w-0 flex-1 space-y-1.5">
          <div className="flex items-center gap-2">
            {/* The filename opens the inspector: the indexed passages, which
                is how a tenant sees *what* was read, not only that it was. */}
            <button
              type="button"
              className="min-w-0 flex-1 truncate text-left text-sm font-medium underline-offset-4 hover:underline"
              onClick={() => setInspecting(true)}
            >
              {document.filename}
            </button>
            {inFlight && (
              <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                {progress.percent}%
              </span>
            )}
          </div>
          <p className="text-xs text-muted-foreground">
            {formatBytes(document.size_bytes)} ·{" "}
            <span
              className={
                failed
                  ? "font-medium text-destructive"
                  : progress.tone === "warning"
                    ? "text-amber-600 dark:text-amber-500"
                    : progress.tone === "done"
                      ? "text-emerald-700 dark:text-emerald-400"
                      : ""
              }
            >
              {progress.label}
            </span>
            {!failed && progress.detail && ` · ${progress.detail}`}
          </p>
          {failed && (
            <p className="rounded-md bg-destructive/5 px-2 py-1.5 text-xs text-destructive">
              {progress.detail}
            </p>
          )}
          {(inFlight || failed) && <StageStepper progress={progress} />}
          {inFlight && (
            <ProgressBar percent={progress.percent} tone={progress.tone} label={`${document.filename} progress`} />
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Button
            size="xs"
            variant={failed ? "outline" : "ghost"}
            aria-label={`Re-ingest ${document.filename}`}
            disabled={retry.isPending || inFlight}
            onClick={() => void handleRetry()}
          >
            {retry.isPending ? <Loader2 className="animate-spin" /> : <RefreshCw />}
            {failed && "Retry"}
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
      </div>

      <Dialog open={inspecting} onOpenChange={setInspecting}>
        <DocumentInspector
          tenantId={tenantId}
          knowledgeBaseId={knowledgeBaseId}
          document={document}
          open={inspecting}
        />
      </Dialog>

      {/* Destructive and irreversible -- the vectors and the stored file go
          too -- so it asks first. */}
      <AlertDialog open={confirming} onOpenChange={setConfirming}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this document?</AlertDialogTitle>
            <AlertDialogDescription>
              <span className="font-medium">{document.filename}</span> and everything
              indexed from it will be removed, including its searchable chunks and the
              stored file. Your chatbot will stop finding it. This cannot be undone.
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
    </li>
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
          Check which pages your chatbot would draw on for a question.
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
  onDone: (knowledgeBaseId: string) => void;
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
      const created = await createKb.mutateAsync({
        name: values.name,
        description: values.description?.trim() || null,
        visibility,
        departmentId: visibility === "department" ? departmentId.trim() || null : null,
        teamId: visibility === "team" ? teamId.trim() || null : null,
      });
      toast.success(`Created ${values.name} — now add documents or a website.`);
      reset();
      onDone(created.id);
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
            Name it, then add files or a website in the next step. Storage and the search
            index are set up automatically.
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
                {/* Render-prop form, as for the crawl mode below: before the
                    list is first opened no items are mounted, so a bare
                    <SelectValue /> showed the raw value "tenant". */}
                <SelectValue>{(value: string) => VISIBILITY_LABELS[value as Visibility] ?? value}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                {(Object.keys(VISIBILITY_LABELS) as Visibility[]).map((v) => (
                  <SelectItem key={v} value={v}>
                    {VISIBILITY_LABELS[v]}
                  </SelectItem>
                ))}
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
            {createKb.isPending ? "Creating…" : "Create and add documents"}
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
          {/* Render-prop form: Base UI resolves a label from the mounted items,
              and before the list is first opened there are none -- so the
              trigger showed the raw value "url_list". */}
          <SelectValue>
            {(value: string) =>
              value === "site"
                ? "Entire website — follow links from one page"
                : "Specific URLs — fetch exactly these pages"
            }
          </SelectValue>
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
            <DataSourceRow
              key={source.id}
              tenantId={tenantId}
              knowledgeBaseId={knowledgeBaseId}
              source={source}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

/** What was actually extracted from one document.
 *
 * This exists because status and chunk count answer "did it work?" and
 * nothing answered "why not?". A scanned page that OCR'd into noise, a
 * spreadsheet whose rows ran together, a crawled page that captured the
 * cookie banner instead of the article — all look identical in the list and
 * are obvious the moment you read the text.
 *
 * Chunk text is rendered in a `<p>`, never as markup: it is untrusted content
 * from a tenant's own uploads, and the console is not the place to find out
 * what happens when a document contains a `<script>` tag.
 */
function DocumentInspector({
  tenantId,
  knowledgeBaseId,
  document,
  open,
}: {
  tenantId: string;
  knowledgeBaseId: string;
  document: KnowledgeBaseDocument;
  open: boolean;
}) {
  const { data, isLoading, error } = useDocumentDetail(
    tenantId,
    knowledgeBaseId,
    open ? document.id : null,
  );

  return (
    <DialogContent className="sm:max-w-3xl">
      <DialogHeader>
        <DialogTitle className="truncate">{document.filename}</DialogTitle>
        <DialogDescription>
          The text this document contributed to the knowledge base. If a question
          isn&rsquo;t being answered from it, this is what the assistant had to work
          with.
        </DialogDescription>
      </DialogHeader>

      <div className="space-y-4 py-2">
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-4">
          <div>
            <dt className="text-xs text-muted-foreground">Status</dt>
            <dd className="mt-0.5">
              <DocumentStatusBadge document={document} />
            </dd>
          </div>
          <div>
            <dt className="text-xs text-muted-foreground">Chunks</dt>
            <dd className="mt-0.5 tabular-nums">{data?.chunk_count ?? document.chunk_count}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted-foreground">Size</dt>
            <dd className="mt-0.5 tabular-nums">{formatBytes(document.size_bytes)}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted-foreground">Type</dt>
            <dd className="mt-0.5 truncate">{document.content_type}</dd>
          </div>
        </dl>

        {data?.source_url && (
          <p className="truncate text-xs text-muted-foreground">
            Crawled from{" "}
            <a
              href={data.source_url}
              target="_blank"
              rel="noreferrer noopener"
              className="underline underline-offset-4"
            >
              {data.source_url}
            </a>
          </p>
        )}

        {document.status === "failed" && document.failure_reason && (
          <p className="rounded-md bg-destructive/10 px-3 py-2 text-xs whitespace-normal text-destructive">
            {document.failure_reason}
          </p>
        )}

        {isLoading && <TableSkeleton rows={3} columns={1} />}
        {error && <ErrorState error={error} resource="document content" />}

        {data && data.chunks.length === 0 && (
          <EmptyState
            icon={FileText}
            title="Nothing was extracted"
            description="No searchable text came out of this file, so it cannot answer questions. Scanned pages are the usual cause — re-export the original if you can, then re-ingest."
          />
        )}

        {data && data.chunks.length > 0 && (
          <div className="space-y-2">
            <ul className="max-h-[45vh] space-y-2 overflow-y-auto pr-1">
              {data.chunks.map((chunk) => (
                <li key={chunk.id} className="rounded-md border border-border p-3">
                  <div className="mb-1 flex items-center justify-between gap-3 text-xs text-muted-foreground">
                    <span className="shrink-0 tabular-nums">
                      Chunk {chunk.chunk_index + 1}
                    </span>
                    <span className="flex min-w-0 items-center gap-2">
                      {/* A crawled chunk's location is a full URL, which is
                          long enough to widen the dialog on its own. */}
                      {chunk.source_location && (
                        <span className="truncate">{chunk.source_location}</span>
                      )}
                      <span className="shrink-0 tabular-nums">
                        {chunk.token_count} tokens
                      </span>
                    </span>
                  </div>
                  {/* `break-words` is load-bearing: `whitespace-pre-wrap`
                      preserves the source's line breaks but will not break a
                      long unbroken token, and crawled markdown is full of
                      them (image and asset URLs). Without it the chunk list
                      scrolls sideways. */}
                  <p className="text-sm break-words whitespace-pre-wrap text-muted-foreground">
                    {chunk.text}
                  </p>
                </li>
              ))}
            </ul>
            {data.chunk_count > data.chunks.length && (
              <p className="text-xs text-muted-foreground">
                Showing the first {data.chunks.length} of {data.chunk_count} chunks.
              </p>
            )}
          </div>
        )}
      </div>
    </DialogContent>
  );
}

/** One crawl source, with a re-run button.
 *
 * Re-syncing enqueues the same job that created the source. That job updates
 * a page's existing document rather than inserting a second one, so this
 * refreshes changed pages and picks up new ones without duplicating anything
 * — which is why it needs no confirmation the way Delete does. Pages that
 * have since vanished from the site are deliberately left alone.
 */
function DataSourceRow({
  tenantId,
  knowledgeBaseId,
  source,
}: {
  tenantId: string;
  knowledgeBaseId: string;
  source: DataSource;
}) {
  const resync = useResyncDataSource(tenantId, knowledgeBaseId);
  const syncing = source.sync_status === "syncing";

  async function handleResync() {
    try {
      await resync.mutateAsync(source.id);
      toast.success("Re-crawling this source.");
    } catch (err) {
      toast.error(isApiError(err) ? err.message : "Couldn't start the re-crawl.");
    }
  }

  return (
    <li className="flex items-start justify-between gap-3">
      <div className="min-w-0">
        <p className="truncate text-sm">{source.urls.join(", ")}</p>
        {source.sync_status === "error" && source.failure_reason && (
          <p className="mt-0.5 text-xs whitespace-normal text-destructive">
            {source.failure_reason}
          </p>
        )}
        {source.last_synced_at && !syncing && (
          <p className="mt-0.5 text-xs text-muted-foreground">
            Last crawled {new Date(source.last_synced_at).toLocaleString()}
          </p>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-1.5">
        <SyncStatusBadge source={source} />
        <Button
          size="xs"
          variant="ghost"
          aria-label={`Re-crawl ${source.urls.join(", ")}`}
          disabled={resync.isPending || syncing}
          onClick={() => void handleResync()}
        >
          {resync.isPending ? <Loader2 className="animate-spin" /> : <RefreshCw />}
        </Button>
      </div>
    </li>
  );
}

/** Ask the knowledge base questions and watch each answer stream in.
 *
 * The chat itself lives in `features/ai-resources/chat/` -- markdown
 * rendering, source cards, copy / regenerate / feedback and the composer.
 * Sources still render before the first token: the backend sends them first,
 * so a reader sees what an answer may draw on even if generation fails.
 *
 * There is no "answer as" picker: assistant management left the tenant
 * surface, so every question here uses the platform's model with this
 * tenant's own brief from the AI Chatbot screen. */
function AskDialog({
  tenantId,
  knowledgeBase,
}: {
  tenantId: string;
  knowledgeBase: KnowledgeBase;
}) {
  return (
    <DialogContent className="flex h-[min(88dvh,820px)] flex-col gap-0 overflow-hidden p-0 sm:max-w-3xl">
      {/* Kept for screen readers -- a dialog must be labelled -- but hidden,
          because the chat's own header already names the knowledge base. */}
      <DialogHeader className="sr-only">
        <DialogTitle>Ask {knowledgeBase.name}</DialogTitle>
        <DialogDescription>
          Answers are generated only from documents in this knowledge base, with citations.
        </DialogDescription>
      </DialogHeader>
      <AskChat tenantId={tenantId} knowledgeBase={knowledgeBase} />
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
  // The plan caps widgets across the whole tenant, not per knowledge base --
  // hence every widget is counted, not `mine`. Same "fails toward enabled"
  // rule as the knowledge-base button: the server's 409 is the real gate.
  const plan = useTenantPlan(tenantId);
  const widgetLimit = plan.data?.max_chat_widgets ?? null;
  const widgetCount = widgets.data?.chat_widgets.length;
  const atWidgetLimit =
    widgetLimit !== null && widgetCount !== undefined && widgetCount >= widgetLimit;
  const tenantDailyLimit = plan.data?.effective_daily_message_limit ?? null;
  const ceiling = plan.data?.max_messages_per_day ?? null;

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
        // Never above the plan's ceiling -- the server refuses that, and the
        // form's default of 500 is above many plans.
        daily_question_limit: ceiling === null ? limit : Math.min(limit, ceiling),
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
          from this knowledge base — no sign-in. Only add sites you control. You can also
          manage this, and check that it&rsquo;s really on your site, from{" "}
          <strong>AI Chatbot → On your website</strong>.
        </DialogDescription>
      </DialogHeader>

      <div className="space-y-4">
        {mine.length > 0 && (
          <div className="space-y-3">
            {mine.map((widget) => (
              <WidgetCard
                key={widget.id}
                tenantId={tenantId}
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

        {atWidgetLimit ? (
          <p className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
            Plan limit reached ({widgetCount}/{widgetLimit} chatbot
            {widgetLimit === 1 ? "" : "s"}). Ask your platform administrator to raise it, or
            edit the existing one above.
          </p>
        ) : (
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
              max={ceiling ?? 100000}
              value={ceiling === null ? limit : Math.min(limit, ceiling)}
              onChange={(e) => setLimit(Number(e.target.value))}
            />
            <p className="text-xs text-muted-foreground">
              Caps what this widget can cost you in a day. Once reached, it stops
              answering until tomorrow.
              {/* Both caps apply and the lower one wins; saying so stops a
                  "500" here being read as the real allowance. */}
              {tenantDailyLimit !== null &&
                ` Your organisation's overall limit of ${tenantDailyLimit.toLocaleString()} messages a day applies too — whichever is lower is reached first.`}
            </p>
          </div>
          <Button type="submit" size="sm" disabled={createWidget.isPending}>
            {createWidget.isPending && <Loader2 className="animate-spin" />}
            Create widget
          </Button>
        </form>
        )}
      </div>
    </DialogContent>
  );
}

function WidgetCard({
  tenantId,
  widget,
  onToggle,
  busy,
}: {
  tenantId: string;
  widget: ChatWidget;
  onToggle: (enabled: boolean) => void;
  busy: boolean;
}) {
  const update = useUpdateChatWidget(tenantId);
  const remove = useDeleteChatWidget(tenantId);
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(widget.name);
  const [origins, setOrigins] = useState(widget.allowed_origins.join("\n"));
  const [limit, setLimit] = useState(widget.daily_question_limit);

  async function save() {
    const parsed = origins
      .split(/[\n,]/)
      .map((o) => o.trim())
      .filter(Boolean);
    if (parsed.length === 0) {
      toast.error("Add at least one website address where this may be embedded.");
      return;
    }
    try {
      await update.mutateAsync({
        widgetId: widget.id,
        name,
        allowed_origins: parsed,
        daily_question_limit: limit,
      });
      setEditing(false);
      toast.success("Widget updated.");
    } catch (error) {
      toast.error(isApiError(error) ? error.message : "Could not update the widget.");
    }
  }

  async function destroy() {
    try {
      await remove.mutateAsync(widget.id);
      toast.success("Widget deleted.");
    } catch (error) {
      // A 409 here is the server refusing to delete a widget that has
      // conversations, and its message names the count and says to disable
      // instead -- so it is shown as-is rather than replaced with a generic one.
      toast.error(isApiError(error) ? error.message : "Could not delete the widget.");
    }
  }
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
            <Button size="xs" variant="outline" onClick={() => setEditing((v) => !v)}>
              {editing ? "Cancel" : "Edit"}
            </Button>
            <Button
              size="xs"
              variant="destructive"
              disabled={remove.isPending}
              onClick={() => void destroy()}
            >
              Delete
            </Button>
          </div>
        </div>

        {editing && (
          <div className="space-y-3 rounded-lg border p-3">
            <div className="space-y-1.5">
              <Label htmlFor={`w-name-${widget.id}`} className="text-xs">
                Name
              </Label>
              <Input
                id={`w-name-${widget.id}`}
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={`w-origins-${widget.id}`} className="text-xs">
                Allowed websites
              </Label>
              <Textarea
                id={`w-origins-${widget.id}`}
                rows={3}
                value={origins}
                onChange={(e) => setOrigins(e.target.value)}
              />
              <p className="text-xs text-muted-foreground">
                One per line, including <code>https://</code>. Enter the site address
                only &mdash; a browser never tells us which page is asking, so
                <code> https://example.com/page.html</code> is stored as
                <code> https://example.com</code>.
              </p>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={`w-limit-${widget.id}`} className="text-xs">
                Questions per day
              </Label>
              <Input
                id={`w-limit-${widget.id}`}
                type="number"
                min={1}
                value={limit}
                onChange={(e) => setLimit(Number(e.target.value))}
              />
            </div>
            <Button size="sm" disabled={update.isPending} onClick={() => void save()}>
              {update.isPending ? "Saving…" : "Save changes"}
            </Button>
          </div>
        )}
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
