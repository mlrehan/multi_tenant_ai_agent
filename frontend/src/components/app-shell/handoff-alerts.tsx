"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import Link from "next/link";
import { toast } from "sonner";
import { BellRing } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useConversationEvents, useUnassignedInbox } from "@/features/chatbot/hooks";
import {
  setQueueBadge,
  setQueueBlink,
  showHandoffNotification,
  vibrateForHandoff,
} from "@/features/chatbot/notifications";
import { useMyMemberships } from "@/features/tenancy/hooks";
import { useHasTenantPermission } from "@/features/rbac/hooks";
import { useTenantStore } from "@/stores/tenant-store";
import { extractTenantIdFromPath } from "@/lib/route-tenant";
import {
  handoffSoundEnabled,
  snoozeHandoffAlarm,
  startHandoffAlarm,
  stopHandoffAlarm,
  subscribeToAlarm,
} from "@/features/chatbot/handoff-sound";

const AGENT_PERMISSION = "tenant.conversations.view";
const SNOOZE_MINUTES = 2;

/**
 * Handoff alerting for the whole console, not just the Inbox screen.
 *
 * **Mounted in the shell because a waiting visitor is not the Inbox's news,
 * it is the agent's.** The subscription used to live on the Inbox page, so an
 * agent reading Members, editing an assistant, or simply sitting on the
 * dashboard was told nothing at all — the one alert the product has, silent
 * for every screen but the one you are already watching.
 *
 * There is exactly one subscription in the app, here. `useConversationEvents`
 * invalidates the unassigned query on every event, and the query client is
 * shared, so the Inbox table still refreshes itself without subscribing
 * separately — two subscriptions would mean two connections and two chimes for
 * one arrival.
 *
 * The visible parts are the toast, the tab badge and blink, the system
 * notification, and — while the alarm is sounding — the banner below, which is
 * the only way to stop it.
 */
export function HandoffAlerts() {
  const pathname = usePathname();
  const routeTenantId = extractTenantIdFromPath(pathname);
  const storedTenantId = useTenantStore((s) => s.currentTenantId);
  const { data: memberships } = useMyMemberships();

  // Same resolution as the sidebar's: the tenant in the URL, else the one the
  // agent last chose, else their default membership. Without the fallback an
  // agent on a non-tenant-scoped screen (their own account page, say) would
  // drop off the alerting entirely -- which is exactly the "on another page"
  // case this component exists for.
  const activeMemberships = memberships?.filter((m) => m.status === "active") ?? [];
  const tenantId =
    routeTenantId ??
    storedTenantId ??
    activeMemberships.find((m) => m.is_default)?.tenant_id ??
    activeMemberships[0]?.tenant_id ??
    null;

  const canWork = useHasTenantPermission(tenantId, AGENT_PERMISSION);

  // Conversations already announced. Without it the SSE event and the refetch
  // it triggers would alert twice for one arrival, and a reconnect would
  // re-announce the entire queue.
  const announced = useRef<Set<string>>(new Set());

  const onEvent = useCallback((event: { event: string; conversation_id?: string }) => {
    if (event.event !== "conversation.unassigned" || !event.conversation_id) return;
    if (announced.current.has(event.conversation_id)) return;
    announced.current.add(event.conversation_id);

    // The repeating alarm, not a single chime: one burst is missed by anyone
    // who stepped away for thirty seconds. It sounds until acknowledged --
    // see `handoff-sound.ts` for what stops it.
    startHandoffAlarm();
    // Both no-op on a visible page, by their own checks: someone watching the
    // screen has the toast, and does not need their phone buzzing or an OS
    // panel over the work they are doing.
    vibrateForHandoff();
    void showHandoffNotification();
    toast.info("A visitor is waiting for a colleague.", {
      description: "Open the Inbox to claim the conversation.",
    });
  }, []);

  useConversationEvents(canWork ? tenantId : null, onEvent);

  // The queue itself, so the tab title and taskbar icon carry the count on
  // *every* screen. This lived on the Inbox, which meant the one signal
  // designed to be read from another tab — "(3) IAM Control Center" in the tab
  // strip — was only ever set while the agent was looking at the queue anyway.
  //
  // Driven by the queue length rather than by counting events: it is then
  // correct after a reload, after a colleague claims one, and for a tab that
  // was closed when the handoff happened. The SSE subscription above
  // invalidates this query, so it re-reads without polling.
  const inbox = useUnassignedInbox(tenantId, Boolean(canWork));
  const waiting = inbox.data?.conversations.length ?? 0;

  // Read from the module rather than mirrored into state on every render: the
  // alarm is not React state, and a second copy of "is it sounding" is how the
  // banner ends up disagreeing with the sound.
  const [alarmActive, setAlarmActive] = useState(false);
  useEffect(() => subscribeToAlarm(setAlarmActive), []);

  useEffect(() => {
    // Clearing when access is lost matters as much as setting it: a stale
    // "(3)" must not outlive the session that earned it.
    const count = canWork ? waiting : 0;
    setQueueBadge(count);
    // Order matters: the badge writes the count into the title first, and the
    // blink alternates against what it wrote. Reversed, the first flash would
    // carry the previous count.
    setQueueBlink(count);

    // An empty queue stops the alarm on its own. A colleague claiming the
    // conversation is the commonest way an alert stops being relevant, and
    // continuing to sound for work already being handled is precisely how
    // people learn to ignore an alarm.
    if (count === 0) stopHandoffAlarm();

    // Stopping on unmount as well as on an empty queue: navigating away from
    // the console must not leave an interval flashing a title -- or sounding a
    // chime -- for ever.
    return () => {
      setQueueBlink(0);
      stopHandoffAlarm();
    };
  }, [canWork, waiting]);

  if (!alarmActive || !canWork) return null;

  return (
    <div
      // `alert` rather than `status`: this is an interruption that needs
      // acknowledging, and screen readers should treat it as one.
      role="alert"
      className="fixed inset-x-0 bottom-0 z-50 flex flex-wrap items-center justify-center gap-3 border-t border-destructive/40 bg-destructive/10 px-4 py-3 backdrop-blur"
    >
      <span className="flex items-center gap-2 text-sm font-medium text-destructive">
        <BellRing className="size-4 animate-pulse" />
        {waiting === 1
          ? "A visitor is waiting to speak to someone"
          : `${waiting} visitors are waiting to speak to someone`}
      </span>
      <div className="flex items-center gap-2">
        {tenantId && (
          <Button
            size="sm"
            // Opening the Inbox is the actual response, so it silences the
            // alarm too -- an agent who has gone to deal with it should not
            // then have to also find a button to stop the noise.
            onClick={() => stopHandoffAlarm()}
            render={<Link href={`/tenant/${tenantId}/inbox`} />}
          >
            Open Inbox
          </Button>
        )}
        <Button
          size="sm"
          variant="outline"
          onClick={() =>
            snoozeHandoffAlarm(
              SNOOZE_MINUTES,
              // Re-checked when the snooze expires rather than captured now:
              // by then a colleague may have claimed it, and re-sounding for
              // an empty queue is the alarm crying wolf.
              () => (inbox.data?.conversations.length ?? 0) > 0,
            )
          }
        >
          Snooze {SNOOZE_MINUTES}m
        </Button>
        <Button size="sm" variant="ghost" onClick={() => stopHandoffAlarm()}>
          Dismiss
        </Button>
      </div>
      {!handoffSoundEnabled() && (
        <span className="text-xs text-muted-foreground">
          Sound is off — enable it on the Inbox screen.
        </span>
      )}
    </div>
  );
}
