"use client";

import { useCallback, useEffect, useRef } from "react";
import { usePathname } from "next/navigation";
import { toast } from "sonner";
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
import { chimeForHandoff, handoffSoundEnabled } from "@/features/chatbot/handoff-sound";

const AGENT_PERMISSION = "tenant.conversations.view";

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
 * Rendered as `null`: this is behaviour, not UI. The visible parts are the
 * toast, the tab badge, and the system notification.
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

    if (handoffSoundEnabled()) chimeForHandoff();
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

  useEffect(() => {
    // Clearing when access is lost matters as much as setting it: a stale
    // "(3)" must not outlive the session that earned it.
    const count = canWork ? waiting : 0;
    setQueueBadge(count);
    // Order matters: the badge writes the count into the title first, and the
    // blink alternates against what it wrote. Reversed, the first flash would
    // carry the previous count.
    setQueueBlink(count);
    // Stopping on unmount as well as on an empty queue: navigating away from
    // the console must not leave an interval flashing a title for ever.
    return () => setQueueBlink(0);
  }, [canWork, waiting]);

  return null;
}
