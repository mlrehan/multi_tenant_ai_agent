"use client";

import { useCallback, useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "@/features/chatbot/api";
import type {
  ChatbotSettings,
  Personality,
  ResponseLength,
  TenantEntitlements,
} from "@/lib/types";

// ---- Platform ----

export function useTenantEntitlements(enabled = true) {
  return useQuery({
    queryKey: ["tenant-entitlements"],
    queryFn: api.listTenantEntitlements,
    enabled,
  });
}

export function useSetTenantEntitlements() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (args: {
      tenantId: string;
      body: Omit<TenantEntitlements, "tenant_id" | "updated_at">;
    }) => api.setTenantEntitlements(args.tenantId, args.body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["tenant-entitlements"] }),
  });
}

export function useAiProviders(enabled = true) {
  return useQuery({ queryKey: ["ai-providers"], queryFn: api.listAiProviders, enabled });
}

// ---- Tenant ----

export function useTenantPlan(tenantId: string | null) {
  return useQuery({
    queryKey: ["tenant-plan", tenantId],
    queryFn: () => api.getTenantPlan(tenantId!),
    enabled: Boolean(tenantId),
  });
}

export function useChatbotSettings(tenantId: string | null) {
  return useQuery({
    queryKey: ["chatbot-settings", tenantId],
    queryFn: () => api.getChatbotSettings(tenantId!),
    enabled: Boolean(tenantId),
  });
}

export function useUpdateChatbotSettings(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (
      body: Omit<
        ChatbotSettings,
        // Server-derived, never sent back: the effective limit is the clamped
        // one, and the defaults are what the server falls back to rather than
        // anything the tenant is setting.
        "updated_at" | "effective_daily_message_limit" | "default_role" | "default_avoid"
      >,
    ) => api.updateChatbotSettings(tenantId, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["chatbot-settings", tenantId] });
      // The plan carries the *enforced* daily limit, which this write can
      // change — refetching only the settings would leave the plan card
      // showing yesterday's ceiling.
      queryClient.invalidateQueries({ queryKey: ["tenant-plan", tenantId] });
    },
  });
}

export function useTeams(tenantId: string | null, activeOnly = false) {
  return useQuery({
    queryKey: ["teams", tenantId, activeOnly],
    queryFn: () => api.listTeams(tenantId!, activeOnly),
    enabled: Boolean(tenantId),
  });
}

export function useSaveTeam(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (args: {
      teamId?: string;
      name: string;
      description: string | null;
      isActive: boolean;
      memberIds: string[];
    }) =>
      api.saveTeam(
        tenantId,
        {
          name: args.name,
          description: args.description,
          isActive: args.isActive,
          memberIds: args.memberIds,
        },
        args.teamId,
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["teams", tenantId] }),
  });
}

export function useUpdateAssistantBehaviour(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (args: {
      assistantId: string;
      roleInstructions: string;
      avoidInstructions: string;
      personality: Personality;
      responseLength: ResponseLength;
    }) => api.updateAssistantBehaviour(tenantId, args.assistantId, args),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["assistants", tenantId] }),
  });
}

export function useWidgetPresentation(tenantId: string | null, widgetId: string | null) {
  return useQuery({
    queryKey: ["widget-presentation", tenantId, widgetId],
    queryFn: () => api.getWidgetPresentation(tenantId!, widgetId!),
    enabled: Boolean(tenantId && widgetId),
  });
}

export function useUpdateWidgetPresentation(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (args: {
      widgetId: string;
      chatbotName: string;
      chatbotTitle: string;
      avatarKey: string;
      greeting: string | null;
      showQuickReplySuggestions: boolean;
      assistantId: string | null;
    }) => api.updateWidgetPresentation(tenantId, args.widgetId, args),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["widget-presentation", tenantId] }),
  });
}

// ---- Handoff inbox ----

export function useUnassignedInbox(tenantId: string | null, enabled = true) {
  return useQuery({
    queryKey: ["unassigned", tenantId],
    queryFn: () => api.listUnassigned(tenantId!),
    enabled: Boolean(tenantId) && enabled,
  });
}

export function useClaimConversation(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (conversationId: string) => api.claimConversation(tenantId, conversationId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["unassigned", tenantId] }),
  });
}

export function useHandOffConversation(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (args: {
      conversationId: string;
      teamId: string | null;
      reason: string | null;
    }) => api.handOffConversation(tenantId, args.conversationId, args),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["unassigned", tenantId] }),
  });
}

export function useReturnConversationToAi(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (conversationId: string) =>
      api.returnConversationToAi(tenantId, conversationId),
    onSuccess: (_, conversationId) => {
      queryClient.invalidateQueries({ queryKey: ["unassigned", tenantId] });
      queryClient.invalidateQueries({
        queryKey: ["conversation-messages", tenantId, conversationId],
      });
    },
  });
}

/** An agent's reply to the visitor, or a staff-only internal note. Neither
 *  is the streamed AI answer path -- this is a plain write, so the message
 *  list is simply refetched rather than appended optimistically. */
/**
 * Reports that this agent is typing, throttled.
 *
 * **Not a `useMutation`.** A mutation would put every heartbeat through React
 * Query's pending/error state, re-render the composer on each one, and surface
 * a toast-worthy error for something nobody needs to be told about. This is
 * fire-and-forget by nature: an indicator that fails to appear is invisible,
 * and the server key lapses by itself either way.
 *
 * One request per keystroke would be a write rate set by how fast someone
 * types, so "still typing" is re-asserted at most every three seconds against
 * a key that lives eight -- a single dropped request cannot make a live typist
 * flicker. The stop is sent explicitly rather than left to the key expiring:
 * an indicator still showing under a reply that has already arrived reads as a
 * second reply coming that never does.
 */
export function useAgentTyping(tenantId: string, conversationId: string | null) {
  const lastPing = useRef(0);
  const idleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const stop = useCallback(() => {
    if (idleTimer.current) {
      clearTimeout(idleTimer.current);
      idleTimer.current = null;
    }
    if (!conversationId || lastPing.current === 0) return;
    lastPing.current = 0;
    void api.setAgentTyping(tenantId, conversationId, false).catch(() => undefined);
  }, [tenantId, conversationId]);

  const note = useCallback(
    (hasText: boolean) => {
      if (!conversationId) return;
      // An emptied box is not typing -- the agent deleted what they had.
      if (!hasText) {
        stop();
        return;
      }
      const now = Date.now();
      if (now - lastPing.current > 3000) {
        lastPing.current = now;
        void api.setAgentTyping(tenantId, conversationId, true).catch(() => undefined);
      }
      if (idleTimer.current) clearTimeout(idleTimer.current);
      idleTimer.current = setTimeout(stop, 3500);
    },
    [tenantId, conversationId, stop],
  );

  // Closing the thread, switching conversations, or navigating away all end
  // the indicator. Without this the visitor would watch a ghost "typing" until
  // the key lapsed, having been told a reply was coming that nobody is writing.
  useEffect(() => stop, [stop]);

  return { note, stop };
}

export function usePostAgentMessage(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (args: { conversationId: string; content: string; internal: boolean }) =>
      api.postAgentMessage(tenantId, args.conversationId, {
        content: args.content,
        internal: args.internal,
      }),
    onSuccess: (_, vars) =>
      queryClient.invalidateQueries({
        queryKey: ["conversation-messages", tenantId, vars.conversationId],
      }),
  });
}

/**
 * Live inbox updates over Server-Sent Events.
 *
 * **Not polling, and not a WebSocket.** The platform has no WebSocket layer;
 * SSE already carries streamed answers through the same BFF proxy and
 * reconnects on its own. `EventSource` is used rather than `fetch` here
 * *because* the proxy attaches the bearer token server-side — the browser has
 * no token to set a header with, which is the whole point of the BFF.
 *
 * The stream is an accelerator, not the source of truth: every event simply
 * invalidates the inbox query, so a missed event during a disconnect is
 * corrected by the refetch on reconnect. `onEvent` fires for the caller's own
 * side effects (the notification sound), deliberately separate from cache
 * invalidation so a re-render cannot make a sound play twice.
 */
export function useConversationEvents(
  tenantId: string | null,
  onEvent?: (event: { event: string; conversation_id?: string }) => void,
) {
  const queryClient = useQueryClient();
  // Held in a ref so changing the callback does not tear down and rebuild the
  // stream — which would drop events and, worse, re-fire the "new item"
  // notification on every parent re-render.
  //
  // Assigned in an effect rather than during render: writing to a ref while
  // rendering is a side effect, and React may render without committing.
  const handler = useRef(onEvent);
  useEffect(() => {
    handler.current = onEvent;
  }, [onEvent]);

  useEffect(() => {
    if (!tenantId) return;
    const source = new EventSource(
      `/api/backend/v1/tenants/${tenantId}/conversation-events`,
    );
    source.onmessage = (message) => {
      let payload: { event: string; conversation_id?: string };
      try {
        payload = JSON.parse(message.data);
      } catch {
        return; // A malformed frame must not kill the stream.
      }
      queryClient.invalidateQueries({ queryKey: ["unassigned", tenantId] });
      handler.current?.(payload);
    };
    // No onerror handler that closes: EventSource reconnects by itself, and
    // closing here would turn a transient blip into a permanently dead inbox.
    return () => source.close();
  }, [tenantId, queryClient]);
}

/** The VAPID public key, or `enabled: false` when push is not configured.
 *
 *  Cached indefinitely: a deployment's keypair does not change while the
 *  console is open, and re-fetching it on every Inbox mount would be a request
 *  whose answer is a constant. */
export function usePushPublicKey(tenantId: string, enabled = true) {
  return useQuery({
    queryKey: ["push-public-key", tenantId],
    queryFn: () => api.fetchPushPublicKey(tenantId),
    enabled: Boolean(tenantId) && enabled,
    staleTime: Infinity,
  });
}
