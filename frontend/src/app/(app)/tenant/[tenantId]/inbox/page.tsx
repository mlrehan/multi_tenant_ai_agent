"use client";

import { use as usePromise, useEffect, useRef, useState } from "react";
import {
  Bell,
  BellOff,
  Inbox,
  Plus,
  Sparkles,
  Users,
  Volume2,
  VolumeX,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
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
import {
  useClaimConversation,
  usePostAgentMessage,
  usePushPublicKey,
  useReturnConversationToAi,
  useSaveTeam,
  useTeams,
  useUnassignedInbox,
} from "@/features/chatbot/hooks";
import { subscribeToPushApi, unsubscribeFromPushApi } from "@/features/chatbot/api";
import {
  type PushState,
  pushIsSupported,
  readPushState,
  subscribeToPush,
  unsubscribeFromPush,
} from "@/features/chatbot/notifications";
import { useConversationMessages } from "@/features/ai-resources/hooks";
import { useAgentTyping } from "@/features/chatbot/hooks";
import { useHandoffSound } from "@/features/chatbot/handoff-sound";
import { ConversationTurn } from "@/components/shared/conversation-turn";
import { useHasTenantPermission } from "@/features/rbac/hooks";
import { isApiError } from "@/lib/api-client";
import type { Team } from "@/lib/types";

const VIEW_ANY = "tenant.conversations.view";

export default function InboxPage({ params }: { params: Promise<{ tenantId: string }> }) {
  const { tenantId } = usePromise(params);
  // `undefined` while loading. Showing the access-denied page then would
  // flash "you don't have access" at an agent who does, every single load.
  const canWork = useHasTenantPermission(tenantId, VIEW_ANY);
  const knownDenied = canWork === false;

  const inbox = useUnassignedInbox(tenantId, Boolean(canWork));
  const teams = useTeams(tenantId);
  const claim = useClaimConversation(tenantId);

  // Stored, not component state: the shell is what plays the chime, so the
  // toggle and the player have to read the same value.
  const [soundOn, setSoundOn] = useHandoffSound();
  const [teamDialog, setTeamDialog] = useState<Team | "new" | null>(null);
  // The conversation just claimed, opened straight into its thread. There is
  // no "my active conversations" list yet -- once claimed, a row leaves the
  // unassigned queue, so this is the only way back to it after claiming.
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);

  // The event subscription, the chime and the toast now live in the shell
  // (`HandoffAlerts`), so they fire on every screen rather than only this one.
  // Nothing is subscribed here: `useConversationEvents` invalidates the
  // unassigned query on each event and the query client is shared, so this
  // table still refreshes itself — while a second subscription would mean two
  // connections and two chimes for one waiting visitor.

  // The tab/taskbar badge is set by the shell (`HandoffAlerts`) from the same
  // query, so it stays correct on every screen rather than only this one. Two
  // writers would fight each other on navigation.

  // --- push ------------------------------------------------------------------
  const pushKey = usePushPublicKey(tenantId, Boolean(canWork));
  const [pushState, setPushState] = useState<PushState>("unsupported");
  const [pushBusy, setPushBusy] = useState(false);

  useEffect(() => {
    if (pushKey.isLoading) return;
    let cancelled = false;
    void readPushState(pushKey.data?.public_key ?? null).then((state) => {
      if (!cancelled) setPushState(state);
    });
    return () => {
      cancelled = true;
    };
  }, [pushKey.isLoading, pushKey.data?.public_key]);

  async function togglePush() {
    const key = pushKey.data?.public_key;
    if (!key) return;
    setPushBusy(true);
    try {
      if (pushState === "subscribed") {
        await unsubscribeFromPush((endpoint) => unsubscribeFromPushApi(tenantId, endpoint));
        setPushState("prompt");
        toast.success("Notifications turned off for this browser.");
        return;
      }
      // The permission prompt is raised here, inside a click handler --
      // browsers penalise origins that ask on page load.
      const result = await subscribeToPush(key, (body) => subscribeToPushApi(tenantId, body));
      setPushState(result.state);
      if (result.state === "subscribed") {
        toast.success("Notifications on. You'll be alerted with the console closed.");
      } else if (result.state === "denied") {
        toast.error("Your browser is blocking notifications for this site.");
      }
    } catch (err) {
      toast.error(isApiError(err) ? err.message : "Couldn't change notifications.");
    } finally {
      setPushBusy(false);
    }
  }

  const teamName = (id: string | null) =>
    teams.data?.teams.find((t) => t.id === id)?.name ?? "Unassigned team";

  if (knownDenied) {
    return (
      <div>
        <PageHeader title="Inbox" description="Conversations waiting for a colleague." />
        <Alert>
          <Inbox className="size-4" />
          <AlertTitle>You don&rsquo;t have inbox access</AlertTitle>
          <AlertDescription>
            Working the handoff queue needs the &ldquo;{VIEW_ANY}&rdquo; permission.
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title="Inbox"
        description="Conversations a visitor asked to escalate. Updates arrive live — no refresh needed."
      />

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Badge variant="outline" className="gap-1.5">
          <span className="relative flex size-2">
            <span className="absolute inline-flex size-full animate-ping rounded-full bg-emerald-400 opacity-75" />
            <span className="relative inline-flex size-2 rounded-full bg-emerald-500" />
          </span>
          Live
        </Badge>
        <Button
          variant="outline"
          size="sm"
          onClick={() => setSoundOn(!soundOn)}
          aria-pressed={soundOn}
        >
          {soundOn ? <Volume2 className="mr-1.5 size-4" /> : <VolumeX className="mr-1.5 size-4" />}
          {soundOn ? "Sound on" : "Sound off"}
        </Button>
        {/* Rendered only where it can actually do something. A button that is
            permanently disabled because the deployment has no VAPID keypair,
            or because the browser has no Push API, is a worse answer than no
            button — it invites an agent to keep clicking it. */}
        {pushIsSupported() && pushKey.data?.enabled && pushState !== "denied" && (
          <Button
            variant="outline"
            size="sm"
            onClick={togglePush}
            disabled={pushBusy}
            aria-pressed={pushState === "subscribed"}
          >
            {pushState === "subscribed" ? (
              <Bell className="mr-1.5 size-4" />
            ) : (
              <BellOff className="mr-1.5 size-4" />
            )}
            {pushState === "subscribed" ? "Alerts on" : "Enable alerts"}
          </Button>
        )}
        {pushState === "denied" && (
          <span className="text-xs text-muted-foreground">
            Notifications blocked in your browser settings
          </span>
        )}
        <div className="flex-1" />
        <Button variant="outline" size="sm" onClick={() => setTeamDialog("new")}>
          <Plus className="mr-1.5 size-4" /> New team
        </Button>
      </div>

      {inbox.isLoading && <TableSkeleton rows={3} columns={4} />}
      {inbox.error && <ErrorState error={inbox.error} resource="the inbox" />}

      {inbox.data?.conversations.length === 0 && (
        <EmptyState
          icon={Inbox}
          title="Nothing waiting"
          description="Conversations appear here the moment a visitor asks for a person."
        />
      )}

      {inbox.data && inbox.data.conversations.length > 0 && (
        <Card>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Conversation</TableHead>
                  <TableHead>Team</TableHead>
                  <TableHead>Reason</TableHead>
                  <TableHead>Waiting since</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {inbox.data.conversations.map((c) => (
                  <TableRow key={c.id}>
                    <TableCell className="font-medium">
                      {c.title ?? "Untitled conversation"}
                    </TableCell>
                    <TableCell>
                      <Badge variant="secondary">{teamName(c.assigned_team_id)}</Badge>
                    </TableCell>
                    <TableCell className="max-w-[280px] whitespace-normal text-sm text-muted-foreground">
                      {c.handoff_reason ?? "—"}
                      {c.handoff_initiated_by && (
                        <span className="ml-1 text-xs">({c.handoff_initiated_by})</span>
                      )}
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {c.handoff_at ? new Date(c.handoff_at).toLocaleString() : "—"}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        size="sm"
                        disabled={claim.isPending}
                        onClick={async () => {
                          try {
                            await claim.mutateAsync(c.id);
                            toast.success("Claimed — it's yours.");
                            setActiveConversationId(c.id);
                          } catch (err) {
                            // 409 is the honest answer to losing a race, not a
                            // failure to hide: two agents must never both
                            // believe they own one conversation.
                            toast.error(
                              isApiError(err) ? err.message : "Couldn't claim this one.",
                            );
                          }
                        }}
                      >
                        Claim
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      <Card className="mt-8">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Users className="size-4" /> Teams
          </CardTitle>
          <CardDescription>
            The transfer options visitors are offered. These are your teams — nothing is
            hard-coded, and an inactive team stops being offered while keeping its history.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {(teams.data?.teams.length ?? 0) === 0 ? (
            <p className="text-sm text-muted-foreground">
              No teams yet. Without one, the assistant tells visitors a transfer
              isn&rsquo;t available rather than offering a menu that goes nowhere.
            </p>
          ) : (
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {teams.data?.teams.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => setTeamDialog(t)}
                  className="rounded-lg border p-3 text-left transition hover:bg-accent"
                >
                  <div className="flex items-center justify-between">
                    <span className="font-medium">{t.name}</span>
                    <Badge variant={t.is_active ? "secondary" : "outline"}>
                      {t.is_active ? "Active" : "Inactive"}
                    </Badge>
                  </div>
                  {t.description && (
                    <p className="mt-1 text-xs text-muted-foreground">{t.description}</p>
                  )}
                </button>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <TeamDialog
        tenantId={tenantId}
        team={teamDialog}
        onClose={() => setTeamDialog(null)}
      />
      <AgentThreadDialog
        tenantId={tenantId}
        conversationId={activeConversationId}
        onClose={() => setActiveConversationId(null)}
      />
    </div>
  );
}

/** Turns loaded per page in the agent's thread. Small so a long conversation
 *  opens on its newest exchange immediately; scrolling up widens it. */
const THREAD_PAGE = 10;

/** What a claimed conversation looks like from the agent's side: the visitor's
 *  turns and the AI's, plus whatever a human has since said -- a reply to the
 *  visitor, a staff-only note, or the marker left when the AI handed off.
 *  Reused from nowhere else on purpose: this is the one screen where all five
 *  `MessageRole` values can appear together, and each needs a different
 *  presentation and a different audience. */
function AgentThreadDialog({
  tenantId,
  conversationId,
  onClose,
}: {
  tenantId: string;
  conversationId: string | null;
  onClose: () => void;
}) {
  /* How many turns are on screen. Starts at one page and widens as the agent
   * scrolls up -- see `useConversationMessages` for why the window grows rather
   * than older pages being stacked beside a sliding live one.
   *
   * Carries the conversation it belongs to, so opening another one starts at
   * one page again. Derived rather than reset in an effect: the depth of a
   * thread the agent has not opened yet is not state worth storing, and
   * resetting it after render would paint the wrong window for a frame. */
  const [depth, setDepth] = useState<{ id: string | null; size: number }>({
    id: conversationId,
    size: THREAD_PAGE,
  });
  const windowSize = depth.id === conversationId ? depth.size : THREAD_PAGE;
  const { data, isLoading, error } = useConversationMessages(tenantId, conversationId, {
    live: true,
    limit: windowSize,
  });
  const postMessage = usePostAgentMessage(tenantId);
  const typing = useAgentTyping(tenantId, conversationId);
  const returnToAi = useReturnConversationToAi(tenantId);
  const [reply, setReply] = useState("");
  const [noteMode, setNoteMode] = useState(false);

  /* ---------------------------------------------------------- auto-scroll */

  /* The visitor's widget always shows the newest turn; the agent's thread did
   * not, so a reply could arrive below the fold and an agent would answer a
   * question they had not seen yet.
   *
   * **Stick-to-bottom rather than scroll-on-every-render.** Forcing the view
   * down on each poll would yank the page out from under anyone reading back
   * through the thread -- every four seconds, unprompted. So the list follows
   * new content only while it is *already* at the bottom, which is where it
   * starts and where it stays unless the agent deliberately scrolls up. */
  const logRef = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);
  // Which conversation the instant jump has been done for. The dialog is
  // reused across conversations, so opening a second one must land at its
  // bottom rather than inherit the first one's position.
  const jumpedFor = useRef<string | null>(null);

  function scrollToLatest(smooth: boolean) {
    const el = logRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: smooth ? "smooth" : "auto" });
  }

  useEffect(() => {
    // A fresh conversation starts pinned, whatever the previous one was doing.
    stickToBottom.current = true;
    jumpedFor.current = null;
  }, [conversationId]);

  /* Preserving the reading position while older turns load.
   *
   * Widening the window grows the list upward, so an untouched `scrollTop`
   * slides whatever the agent was reading down by exactly the height added.
   * The height is measured when the request is made and the difference applied
   * once the taller list has painted. */
  const heightBeforeGrow = useRef<number | null>(null);
  useEffect(() => {
    const el = logRef.current;
    if (!el || heightBeforeGrow.current === null) return;
    const grew = el.scrollHeight - heightBeforeGrow.current;
    heightBeforeGrow.current = null;
    if (grew > 0) el.scrollTop += grew;
  }, [data]);

  function loadOlder() {
    const el = logRef.current;
    // Nothing above to load, or a widen already in flight.
    if (!el || !data?.has_more || heightBeforeGrow.current !== null) return;
    heightBeforeGrow.current = el.scrollHeight;
    setDepth({ id: conversationId, size: windowSize + THREAD_PAGE });
  }

  const turnCount = data?.messages.length ?? 0;
  useEffect(() => {
    if (!conversationId || !data) return;
    // A widen must never scroll: the agent is reading upward, and following
    // the newest turn would undo the very scroll that asked for older ones.
    if (heightBeforeGrow.current !== null) return;
    const firstPaint = jumpedFor.current !== conversationId;
    if (firstPaint) jumpedFor.current = conversationId;
    if (!firstPaint && !stickToBottom.current) return;
    // Instant on open -- animating a long thread from the top is a distraction,
    // not a transition. Smooth thereafter, so an arriving message reads as
    // movement rather than a jump.
    //
    // Deferred a frame: the rows have not been laid out when this effect runs,
    // so `scrollHeight` is still the previous height and the scroll lands
    // short of the last message. This was the actual failure, not a missing
    // call.
    const frame = requestAnimationFrame(() => scrollToLatest(!firstPaint));
    return () => cancelAnimationFrame(frame);
    // `visitor_typing` is a dependency because the indicator changes the
    // list's height: without it, the newest turn slides under the fold the
    // moment the visitor starts typing.
  }, [conversationId, data, turnCount, data?.visitor_typing]);

  async function send() {
    const content = reply.trim();
    if (!content || !conversationId) return;
    try {
      // Ended before the request, not after: the reply is written and there is
      // nothing left to be composing, whatever the network does next.
      typing.stop();
      // Sending is an explicit "I am at the end of this conversation", so it
      // re-pins even if the agent had scrolled up to re-read something first.
      stickToBottom.current = true;
      await postMessage.mutateAsync({ conversationId, content, internal: noteMode });
      setReply("");
      scrollToLatest(true);
    } catch (err) {
      toast.error(isApiError(err) ? err.message : "Couldn't send that.");
    }
  }

  async function handleReturnToAi() {
    if (!conversationId) return;
    try {
      await returnToAi.mutateAsync(conversationId);
      toast.success("Back with the AI.");
      onClose();
    } catch (err) {
      toast.error(isApiError(err) ? err.message : "Couldn't hand this back to the AI.");
    }
  }

  return (
    <Dialog open={Boolean(conversationId)} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{data?.conversation.title ?? "Conversation"}</DialogTitle>
          <DialogDescription>
            Reply as yourself, leave a note only your team can see, or hand this back to
            the AI once it&rsquo;s resolved.
          </DialogDescription>
        </DialogHeader>

        {isLoading && <TableSkeleton rows={3} columns={1} />}
        {error && <ErrorState error={error} resource="conversation" />}

        {data?.has_more && (
          // Shown rather than left silent: without it, a thread that opens
          // mid-conversation looks like the whole conversation.
          <p className="text-center text-xs text-muted-foreground">
            Scroll up for earlier messages
          </p>
        )}

        {data && data.messages.length === 0 && (
          <p className="text-sm text-muted-foreground">
            This conversation has no messages yet.
          </p>
        )}

        <div
          ref={logRef}
          onScroll={(e) => {
            const el = e.currentTarget;
            // Generous threshold so older turns start loading before the agent
            // reaches a hard stop -- arriving at the top and *then* waiting is
            // what makes an infinite scroll feel broken.
            if (el.scrollTop < 80) loadOlder();
            // A tolerance, not equality: smooth scrolling lands a fraction of
            // a pixel short, and a strict check would unpin the list every
            // time it scrolled itself.
            stickToBottom.current =
              el.scrollHeight - el.scrollTop - el.clientHeight < 60;
          }}
          className="max-h-96 space-y-3 overflow-y-auto"
        >
          {data?.messages.map((m) => (
            // "Visitor" unconditionally: everything reaching this queue is a
            // widget conversation, which by construction has no membership.
            <ConversationTurn key={m.id} message={m} asker="Visitor" />
          ))}
          {data?.visitor_typing && (
            // Rendered outside the message list on purpose: it is not a turn,
            // has no id, and must never be mistaken for one -- it is state
            // that is wrong again a few seconds from now.
            <p className="animate-pulse text-xs text-muted-foreground">
              Visitor is typing…
            </p>
          )}
        </div>

        <DialogFooter className="flex-col items-stretch gap-3 sm:flex-col sm:items-stretch">
          <div className="flex items-center justify-between">
            <Button
              variant={noteMode ? "default" : "outline"}
              size="sm"
              type="button"
              onClick={() => setNoteMode((v) => !v)}
            >
              {noteMode ? "Writing an internal note" : "Reply to visitor"}
            </Button>
            <Button
              variant="outline"
              size="sm"
              type="button"
              onClick={() => void handleReturnToAi()}
              disabled={returnToAi.isPending}
            >
              <Sparkles className="mr-1.5 size-4" /> Return to AI
            </Button>
          </div>
          <Textarea
            placeholder={
              noteMode
                ? "Note for your team -- the visitor will never see this…"
                : "Reply to the visitor…"
            }
            value={reply}
            onChange={(e) => {
              setReply(e.target.value);
              // An internal note is not addressed to the visitor, so telling
              // them a reply is being composed would be a lie -- and the note
              // may well be "this one is a time-waster".
              if (!noteMode) typing.note(e.target.value.trim().length > 0);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void send();
              }
            }}
          />
          <Button
            onClick={() => void send()}
            disabled={postMessage.isPending || !reply.trim()}
            className="self-end"
          >
            {noteMode ? "Add note" : "Send reply"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function TeamDialog({
  tenantId,
  team,
  onClose,
}: {
  tenantId: string;
  team: Team | "new" | null;
  onClose: () => void;
}) {
  const save = useSaveTeam(tenantId);
  const existing = team && team !== "new" ? team : null;
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [active, setActive] = useState(true);
  const [seeded, setSeeded] = useState<string | null>(null);

  // Seed from the selected team once per open. Keyed on the id so reopening a
  // different team re-seeds, while typing is never overwritten mid-edit.
  const key = existing?.id ?? (team === "new" ? "new" : null);
  if (key && seeded !== key) {
    setSeeded(key);
    setName(existing?.name ?? "");
    setDescription(existing?.description ?? "");
    setActive(existing?.is_active ?? true);
  }

  async function handleSave() {
    try {
      await save.mutateAsync({
        teamId: existing?.id,
        name,
        description: description.trim() || null,
        isActive: active,
        memberIds: existing?.member_ids ?? [],
      });
      toast.success(existing ? "Team updated." : "Team created.");
      setSeeded(null);
      onClose();
    } catch (err) {
      toast.error(isApiError(err) ? err.message : "Couldn't save this team.");
    }
  }

  return (
    <Dialog
      open={Boolean(team)}
      onOpenChange={(open) => {
        if (!open) {
          setSeeded(null);
          onClose();
        }
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{existing ? "Edit team" : "New team"}</DialogTitle>
          <DialogDescription>
            Visitors see this name when the assistant offers a transfer.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div>
            <Label htmlFor="team-name">Name</Label>
            <Input
              id="team-name"
              className="mt-1.5"
              maxLength={100}
              placeholder="Admissions enquiries"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div>
            <Label htmlFor="team-description">Description</Label>
            <Input
              id="team-description"
              className="mt-1.5"
              placeholder="Optional — for your own reference"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
          <div className="flex items-center justify-between rounded-lg border p-3">
            <div>
              <div className="text-sm font-medium">Offered to visitors</div>
              <div className="text-xs text-muted-foreground">
                Turning this off keeps existing conversations intact.
              </div>
            </div>
            <Switch checked={active} onCheckedChange={setActive} />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={handleSave} disabled={save.isPending || !name.trim()}>
            {save.isPending ? "Saving…" : "Save team"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
