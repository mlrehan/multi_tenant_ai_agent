"use client";

import { use as usePromise, useState } from "react";
import { MessagesSquare, Pencil, Search, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
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
import { StatusBadge } from "@/components/shared/status-badge";
import {
  type AskerLabel,
  ConversationTurn,
} from "@/components/shared/conversation-turn";
import {
  useConversationMessages,
  useConversationSearch,
  useConversations,
  useDeleteConversation,
  useRenameConversation,
  useTenantConversations,
} from "@/features/ai-resources/hooks";
import { useHasTenantPermission } from "@/features/rbac/hooks";
import { isApiError } from "@/lib/api-client";
import type { Conversation, ConversationState } from "@/lib/types";

/** The permission that turns this screen into an oversight surface. Opening
 *  someone else's conversation is audited server-side; the UI says so rather
 *  than letting an admin read a colleague's chat without realising it leaves a
 *  record. */
const VIEW_ANY = "tenant.conversations.view";

export default function ConversationsPage({
  params,
}: {
  params: Promise<{ tenantId: string }>;
}) {
  const { tenantId } = usePromise(params);
  const canViewAll = useHasTenantPermission(tenantId, VIEW_ANY);

  const [query, setQuery] = useState("");
  const [allMembers, setAllMembers] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);

  const mine = useConversations(tenantId);
  const everyones = useTenantConversations(tenantId, Boolean(canViewAll) && allMembers);
  const search = useConversationSearch(tenantId, query, allMembers && Boolean(canViewAll));

  const searching = query.trim().length > 1;
  // Precedence: an active search wins, then the scope toggle. Written as one
  // expression so the table can never show a list that disagrees with the
  // controls above it.
  const active = searching ? search : allMembers && canViewAll ? everyones : mine;
  const conversations = active.data?.conversations;

  return (
    <div>
      <PageHeader
        title="Conversations"
        description="Chats from your website chatbot, and any handled by your team after a handoff."
      />

      <div className="mb-4 flex flex-wrap items-end gap-3">
        <div className="min-w-[260px] flex-1">
          <Label htmlFor="conversation-search">Search</Label>
          <div className="relative mt-1.5">
            <Search className="absolute top-2.5 left-2.5 size-4 text-muted-foreground" />
            <Input
              id="conversation-search"
              className="pl-8"
              placeholder="Search what was said…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
        </div>
        {canViewAll && (
          <Button
            variant={allMembers ? "default" : "outline"}
            size="sm"
            onClick={() => setAllMembers((v) => !v)}
          >
            {allMembers ? "All conversations" : "Only mine"}
          </Button>
        )}
      </div>

      {canViewAll && allMembers && (
        <Alert className="mb-4">
          <MessagesSquare className="size-4" />
          <AlertTitle>Viewing every conversation in this tenant</AlertTitle>
          <AlertDescription>
            Members&apos; threads and website visitors&apos; chatbot conversations, including
            anything handled by your team after a handoff. Opening one records an audit entry
            naming you and the conversation. You can read them; renaming and deleting stay
            with the person who owns the thread.
          </AlertDescription>
        </Alert>
      )}

      {active.isLoading && <TableSkeleton rows={4} columns={4} />}
      {active.error && <ErrorState error={active.error} resource="conversations" />}

      {conversations && conversations.length === 0 && (
        <EmptyState
          icon={MessagesSquare}
          title={
            searching
              ? "Nothing matched"
              : allMembers && canViewAll
                ? "No conversations yet"
                : "No conversations of your own"
          }
          // The old copy pointed at "a published assistant" -- a feature tenants
          // no longer have -- and never said where conversations actually are.
          // The Ask panel keeps no threads, so for a tenant member "Only mine"
          // is normally empty; website visitors' chats live under "All".
          description={
            searching
              ? "No conversation contains that text."
              : allMembers && canViewAll
                ? "Chats from your website chatbot appear here as soon as a visitor asks something."
                : canViewAll
                  ? "Questions asked in the Ask panel aren't kept as threads. Your website visitors' chats are under All conversations."
                  : "Questions asked in the Ask panel aren't kept as threads."
          }
          action={
            !searching && !allMembers && canViewAll ? (
              <Button size="sm" onClick={() => setAllMembers(true)}>
                Show all conversations
              </Button>
            ) : undefined
          }
        />
      )}

      {conversations && conversations.length > 0 && (
        <Card>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Title</TableHead>
                  <TableHead>Who</TableHead>
                  <TableHead>Handled by</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Last message</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {conversations.map((c) => (
                  <ConversationRow
                    key={c.id}
                    tenantId={tenantId}
                    conversation={c}
                    onOpen={() => setOpenId(c.id)}
                  />
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      <ThreadDialog
        tenantId={tenantId}
        conversationId={openId}
        onClose={() => setOpenId(null)}
      />
    </div>
  );
}

function ConversationRow({
  tenantId,
  conversation,
  onOpen,
}: {
  tenantId: string;
  conversation: Conversation;
  onOpen: () => void;
}) {
  const rename = useRenameConversation(tenantId);
  const remove = useDeleteConversation(tenantId);
  const [renameOpen, setRenameOpen] = useState(false);
  const [title, setTitle] = useState(conversation.title ?? "");

  async function handleRename() {
    try {
      await rename.mutateAsync({ conversationId: conversation.id, title });
      setRenameOpen(false);
      toast.success("Conversation renamed.");
    } catch (err) {
      toast.error(isApiError(err) ? err.message : "Couldn't rename this conversation.");
    }
  }

  async function handleDelete() {
    // Deliberately confirmed: the turns go with it (ON DELETE CASCADE) and
    // there is no undo, by design -- a "deleted" thread whose content is still
    // readable is not a deletion.
    if (!window.confirm("Delete this conversation and everything said in it?")) return;
    try {
      await remove.mutateAsync(conversation.id);
      toast.success("Conversation deleted.");
    } catch (err) {
      toast.error(isApiError(err) ? err.message : "Couldn't delete this conversation.");
    }
  }

  return (
    <TableRow>
      <TableCell>
        <button
          type="button"
          className="text-left font-medium hover:underline"
          onClick={onOpen}
        >
          {conversation.title ?? "Untitled conversation"}
        </button>
      </TableCell>
      <TableCell>
        {/* A widget thread has no membership -- the `exactly_one_owner` CHECK
            makes that the reliable way to tell the two apart, rather than
            guessing from the title. Worth a column of its own: "who was the
            chatbot talking to?" is the first thing an admin scanning this
            roster wants, and it was not answerable from it at all. */}
        <Badge variant={conversation.membership_id ? "secondary" : "outline"}>
          {conversation.membership_id ? "Member" : "Website visitor"}
        </Badge>
      </TableCell>
      <TableCell>
        {/* Replaces an "Assistant" column that read "—" on every row once
            assistants were withdrawn from tenants. Who replies next is what
            someone scanning this list actually needs. */}
        <HandledBy state={conversation.state} />
      </TableCell>
      <TableCell>
        <StatusBadge status={conversation.status} />
      </TableCell>
      <TableCell className="text-sm text-muted-foreground">
        {conversation.last_message_at
          ? new Date(conversation.last_message_at).toLocaleString()
          : "—"}
      </TableCell>
      <TableCell className="text-right">
        <div className="flex items-center justify-end gap-1.5">
          <Button size="sm" variant="outline" onClick={onOpen}>
            Open
          </Button>
          <Dialog open={renameOpen} onOpenChange={setRenameOpen}>
            <Button
              size="icon"
              variant="ghost"
              aria-label="Rename"
              onClick={() => setRenameOpen(true)}
            >
              <Pencil className="size-4" />
            </Button>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Rename conversation</DialogTitle>
                <DialogDescription>
                  Only the person who owns a conversation can rename it.
                </DialogDescription>
              </DialogHeader>
              <div>
                <Label htmlFor="new-title">Title</Label>
                <Input
                  id="new-title"
                  className="mt-1.5"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                />
              </div>
              <DialogFooter>
                <Button variant="outline" onClick={() => setRenameOpen(false)}>
                  Cancel
                </Button>
                <Button onClick={() => void handleRename()} disabled={!title.trim()}>
                  Save
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
          <Button
            size="icon"
            variant="ghost"
            aria-label="Delete"
            onClick={() => void handleDelete()}
          >
            <Trash2 className="size-4 text-destructive" />
          </Button>
        </div>
      </TableCell>
    </TableRow>
  );
}

/** The thread itself.
 *
 *  Rendered with `textContent` semantics -- React escapes by default and there
 *  is deliberately no markdown renderer here: this is model output built from
 *  tenant-uploaded documents, and treating it as markup is how a poisoned
 *  document becomes script execution in an admin's browser. */
function ThreadDialog({
  tenantId,
  conversationId,
  onClose,
}: {
  tenantId: string;
  conversationId: string | null;
  onClose: () => void;
}) {
  const { data, isLoading, error } = useConversationMessages(tenantId, conversationId);

  // Who the `user` turns belong to. A visitor thread has no membership at all,
  // so calling those turns "You" -- as this dialog did for every non-assistant
  // role -- told an admin they had written a stranger's questions.
  const asker: AskerLabel = data?.is_owner
    ? "You"
    : data?.conversation.membership_id
      ? "Member"
      : "Visitor";

  return (
    <Dialog open={Boolean(conversationId)} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>
            {data?.conversation.title ?? "Conversation"}
          </DialogTitle>
          <DialogDescription>
            {data && !data.is_owner
              ? data.conversation.membership_id
                ? "You are reading another member's conversation. This view was recorded in the audit log."
                : "A website visitor's conversation, including any handoff to your team. This view was recorded in the audit log."
              : "The full thread, oldest first."}
          </DialogDescription>
        </DialogHeader>

        {isLoading && <TableSkeleton rows={3} columns={1} />}
        {error && <ErrorState error={error} resource="conversation" />}

        {data && data.messages.length === 0 && (
          <p className="text-sm text-muted-foreground">
            This conversation has no messages yet.
          </p>
        )}

        <div className="max-h-[60vh] space-y-3 overflow-y-auto">
          {data?.messages.map((m) => (
            <ConversationTurn key={m.id} message={m} asker={asker} />
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}

const HANDLED_BY: Record<ConversationState, { label: string; tone: string }> = {
  ai_active: { label: "AI chatbot", tone: "text-muted-foreground" },
  handoff_requested: { label: "Waiting for a person", tone: "text-amber-700 dark:text-amber-400" },
  unassigned: { label: "Waiting for a person", tone: "text-amber-700 dark:text-amber-400" },
  assigned: { label: "Team member", tone: "text-foreground" },
  human_active: { label: "Team member", tone: "text-foreground" },
  resolved: { label: "Resolved", tone: "text-muted-foreground" },
};

function HandledBy({ state }: { state: ConversationState | null | undefined }) {
  const entry = state ? HANDLED_BY[state] : undefined;
  if (!entry) return <span className="text-sm text-muted-foreground">—</span>;
  return <span className={`text-sm ${entry.tone}`}>{entry.label}</span>;
}
