import { apiFetch } from "@/lib/api-client";
import type {
  AssistantBehaviour,
  ChatbotSettings,
  Personality,
  ProviderCapability,
  ResponseLength,
  Team,
  TenantEntitlements,
  TenantPlan,
  UnassignedConversation,
  WidgetPresentation,
} from "@/lib/types";

// ---- Platform: entitlements and provider catalogue ----

export function listTenantEntitlements() {
  return apiFetch<{ entitlements: TenantEntitlements[] }>(
    "v1/platform/tenant-entitlements",
  );
}

export function setTenantEntitlements(
  tenantId: string,
  body: Omit<TenantEntitlements, "tenant_id" | "updated_at">,
) {
  // PUT, not PATCH: the eight fields are one policy decision. A partial update
  // would let an operator raise a token cap while believing they had also
  // tightened a flag they never sent.
  return apiFetch<TenantEntitlements>(
    `v1/platform/tenants/${tenantId}/entitlements`,
    { method: "PUT", body },
  );
}

export function listAiProviders() {
  return apiFetch<{ providers: ProviderCapability[] }>("v1/platform/ai-providers");
}

// ---- Tenant: plan, chatbot settings, teams ----

export function getTenantPlan(tenantId: string) {
  return apiFetch<TenantPlan>(`v1/tenants/${tenantId}/plan`, { tenantId });
}

export function getChatbotSettings(tenantId: string) {
  return apiFetch<ChatbotSettings>(`v1/tenants/${tenantId}/chatbot-settings`, {
    tenantId,
  });
}

export function updateChatbotSettings(
  tenantId: string,
  body: Omit<ChatbotSettings, "updated_at" | "effective_daily_message_limit">,
) {
  return apiFetch<ChatbotSettings>(`v1/tenants/${tenantId}/chatbot-settings`, {
    method: "PUT",
    tenantId,
    body,
  });
}

export function listTeams(tenantId: string, activeOnly = false) {
  return apiFetch<{ teams: Team[] }>(
    `v1/tenants/${tenantId}/teams${activeOnly ? "?active_only=true" : ""}`,
    { tenantId },
  );
}

export function saveTeam(
  tenantId: string,
  body: { name: string; description: string | null; isActive: boolean; memberIds: string[] },
  teamId?: string,
) {
  const payload = {
    name: body.name,
    description: body.description,
    is_active: body.isActive,
    member_ids: body.memberIds,
  };
  return teamId
    ? apiFetch<Team>(`v1/tenants/${tenantId}/teams/${teamId}`, {
        method: "PUT",
        tenantId,
        body: payload,
      })
    : apiFetch<Team>(`v1/tenants/${tenantId}/teams`, {
        method: "POST",
        tenantId,
        body: payload,
      });
}

// ---- Assistant behaviour and widget presentation ----

export function updateAssistantBehaviour(
  tenantId: string,
  assistantId: string,
  body: {
    roleInstructions: string;
    avoidInstructions: string;
    personality: Personality;
    responseLength: ResponseLength;
  },
) {
  return apiFetch<AssistantBehaviour>(
    `v1/tenants/${tenantId}/assistants/${assistantId}/behaviour`,
    {
      method: "PUT",
      tenantId,
      body: {
        role_instructions: body.roleInstructions,
        avoid_instructions: body.avoidInstructions,
        personality: body.personality,
        response_length: body.responseLength,
      },
    },
  );
}

export function getWidgetPresentation(tenantId: string, widgetId: string) {
  return apiFetch<WidgetPresentation>(
    `v1/tenants/${tenantId}/chat-widgets/${widgetId}/presentation`,
    { tenantId },
  );
}

export function updateWidgetPresentation(
  tenantId: string,
  widgetId: string,
  body: {
    chatbotName: string;
    chatbotTitle: string;
    avatarKey: string;
    greeting: string | null;
    showQuickReplySuggestions: boolean;
    assistantId: string | null;
  },
) {
  return apiFetch<WidgetPresentation>(
    `v1/tenants/${tenantId}/chat-widgets/${widgetId}/presentation`,
    {
      method: "PUT",
      tenantId,
      body: {
        chatbot_name: body.chatbotName,
        chatbot_title: body.chatbotTitle,
        avatar_key: body.avatarKey,
        greeting: body.greeting,
        show_quick_reply_suggestions: body.showQuickReplySuggestions,
        assistant_id: body.assistantId,
      },
    },
  );
}

// ---- Handoff inbox ----

export function listUnassigned(tenantId: string) {
  return apiFetch<{ conversations: UnassignedConversation[] }>(
    `v1/tenants/${tenantId}/conversations/unassigned`,
    { tenantId },
  );
}

export function claimConversation(tenantId: string, conversationId: string) {
  return apiFetch<void>(
    `v1/tenants/${tenantId}/conversations/${conversationId}/claim`,
    { method: "POST", tenantId },
  );
}

export function handOffConversation(
  tenantId: string,
  conversationId: string,
  body: { teamId: string | null; reason: string | null },
) {
  return apiFetch<void>(
    `v1/tenants/${tenantId}/conversations/${conversationId}/handoff`,
    { method: "POST", tenantId, body: { team_id: body.teamId, reason: body.reason } },
  );
}

export function postAgentMessage(
  tenantId: string,
  conversationId: string,
  body: { content: string; internal: boolean },
) {
  return apiFetch<void>(
    `v1/tenants/${tenantId}/conversations/${conversationId}/messages`,
    { method: "POST", tenantId, body },
  );
}

/** Heartbeat: this agent is composing a reply, or has stopped.
 *
 *  Not a message and not stored as one -- the server keeps it in a cache key
 *  that lapses on its own, so a closed tab ends the indicator without having
 *  to report anything. */
export function setAgentTyping(
  tenantId: string,
  conversationId: string,
  typing: boolean,
) {
  return apiFetch<void>(
    `v1/tenants/${tenantId}/conversations/${conversationId}/typing`,
    { method: "POST", tenantId, body: { typing } },
  );
}

export function returnConversationToAi(tenantId: string, conversationId: string) {
  return apiFetch<void>(
    `v1/tenants/${tenantId}/conversations/${conversationId}/return-to-ai`,
    { method: "POST", tenantId },
  );
}

export interface PushPublicKey {
  enabled: boolean;
  public_key: string | null;
}

export function fetchPushPublicKey(tenantId: string) {
  return apiFetch<PushPublicKey>(`v1/tenants/${tenantId}/push/public-key`, { tenantId });
}

export function subscribeToPushApi(
  tenantId: string,
  body: { endpoint: string; p256dh_key: string; auth_key: string },
) {
  return apiFetch<void>(`v1/tenants/${tenantId}/push/subscriptions`, {
    method: "POST",
    tenantId,
    body,
  });
}

export function unsubscribeFromPushApi(tenantId: string, endpoint: string) {
  return apiFetch<void>(`v1/tenants/${tenantId}/push/subscriptions`, {
    method: "DELETE",
    tenantId,
    body: { endpoint },
  });
}
