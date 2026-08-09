import { apiFetch } from "@/lib/api-client";
import type {
  Assistant,
  AccessLevel,
  Conversation,
  CrawlMode,
  ChatWidget,
  DataSource,
  KnowledgeBase,
  KnowledgeBaseDocument,
  KnowledgeBaseQueryHit,
  ModelConfiguration,
  ProviderCredential,
  Visibility,
} from "@/lib/types";

// ---- Assistants ----

export function listAssistants(tenantId: string) {
  return apiFetch<{ assistants: Assistant[] }>(`v1/tenants/${tenantId}/assistants`, { tenantId });
}

export function getAssistant(tenantId: string, assistantId: string) {
  return apiFetch<Assistant>(`v1/tenants/${tenantId}/assistants/${assistantId}`, { tenantId });
}

export function createAssistant(
  tenantId: string,
  body: {
    name: string;
    description: string | null;
    modelConfigurationId: string;
    visibility: Visibility;
    departmentId: string | null;
    teamId: string | null;
    systemPrompt: string | null;
  },
) {
  return apiFetch<{ id: string }>(`v1/tenants/${tenantId}/assistants`, {
    method: "POST",
    tenantId,
    body: {
      name: body.name,
      description: body.description,
      model_configuration_id: body.modelConfigurationId,
      visibility: body.visibility,
      department_id: body.departmentId,
      team_id: body.teamId,
      system_prompt: body.systemPrompt,
    },
  });
}

export function updateAssistant(
  tenantId: string,
  assistantId: string,
  body: {
    name: string;
    description: string | null;
    modelConfigurationId: string;
    systemPrompt: string | null;
  },
) {
  return apiFetch<void>(`v1/tenants/${tenantId}/assistants/${assistantId}`, {
    method: "PATCH",
    tenantId,
    body: {
      name: body.name,
      description: body.description,
      model_configuration_id: body.modelConfigurationId,
      system_prompt: body.systemPrompt,
    },
  });
}

export function archiveAssistant(tenantId: string, assistantId: string) {
  return apiFetch<void>(`v1/tenants/${tenantId}/assistants/${assistantId}/archive`, {
    method: "POST",
    tenantId,
  });
}

export function listModelConfigurations(tenantId: string) {
  return apiFetch<{ model_configurations: ModelConfiguration[] }>(
    `v1/tenants/${tenantId}/model-configurations`,
    { tenantId },
  );
}

export function publishAssistant(tenantId: string, assistantId: string) {
  return apiFetch<void>(`v1/tenants/${tenantId}/assistants/${assistantId}/publish`, {
    method: "POST",
    tenantId,
  });
}

export function changeAssistantVisibility(
  tenantId: string,
  assistantId: string,
  body: { visibility: Visibility; departmentId: string | null; teamId: string | null },
) {
  return apiFetch<void>(`v1/tenants/${tenantId}/assistants/${assistantId}/visibility`, {
    method: "PUT",
    tenantId,
    body: { visibility: body.visibility, department_id: body.departmentId, team_id: body.teamId },
  });
}

export function grantAssistantAccess(
  tenantId: string,
  assistantId: string,
  membershipId: string,
  accessLevel: AccessLevel,
) {
  return apiFetch<{ id: string }>(`v1/tenants/${tenantId}/assistants/${assistantId}/members`, {
    method: "POST",
    tenantId,
    body: { membership_id: membershipId, access_level: accessLevel },
  });
}

export function revokeAssistantAccess(tenantId: string, assistantId: string, membershipId: string) {
  return apiFetch<void>(`v1/tenants/${tenantId}/assistants/${assistantId}/members/${membershipId}`, {
    method: "DELETE",
    tenantId,
  });
}

// ---- Knowledge bases ----

export function listKnowledgeBases(tenantId: string) {
  return apiFetch<{ knowledge_bases: KnowledgeBase[] }>(`v1/tenants/${tenantId}/knowledge-bases`, {
    tenantId,
  });
}

export function createKnowledgeBase(
  tenantId: string,
  body: { name: string; description: string | null; visibility: Visibility; departmentId: string | null; teamId: string | null },
) {
  return apiFetch<{ id: string }>(`v1/tenants/${tenantId}/knowledge-bases`, {
    method: "POST",
    tenantId,
    body: {
      name: body.name,
      description: body.description,
      visibility: body.visibility,
      department_id: body.departmentId,
      team_id: body.teamId,
    },
  });
}

export function uploadDocument(tenantId: string, knowledgeBaseId: string, file: File) {
  // Only the file is sent: filename, content type and size come from it, and
  // the checksum is computed server-side. A checksum the client chose would
  // attest to nothing, so there is deliberately no field for one.
  const formData = new FormData();
  formData.append("file", file);

  return apiFetch<{ id: string }>(
    `v1/tenants/${tenantId}/knowledge-bases/${knowledgeBaseId}/documents`,
    { method: "POST", tenantId, formData },
  );
}

export function listDocuments(tenantId: string, knowledgeBaseId: string) {
  return apiFetch<{ documents: KnowledgeBaseDocument[] }>(
    `v1/tenants/${tenantId}/knowledge-bases/${knowledgeBaseId}/documents`,
    { tenantId },
  );
}

export function createDataSource(
  tenantId: string,
  knowledgeBaseId: string,
  body: { urls: string[]; mode: CrawlMode },
) {
  // No depth/page/timeout fields: those are platform limits, not tenant
  // input -- they bound what this platform spends on a tenant's behalf, so
  // letting the tenant set them would defeat their only purpose.
  return apiFetch<{ id: string }>(
    `v1/tenants/${tenantId}/knowledge-bases/${knowledgeBaseId}/data-sources`,
    { method: "POST", tenantId, body },
  );
}

export function listDataSources(tenantId: string, knowledgeBaseId: string) {
  return apiFetch<{ data_sources: DataSource[] }>(
    `v1/tenants/${tenantId}/knowledge-bases/${knowledgeBaseId}/data-sources`,
    { tenantId },
  );
}

export function listChatWidgets(tenantId: string) {
  return apiFetch<{ chat_widgets: ChatWidget[] }>(
    `v1/tenants/${tenantId}/chat-widgets`,
    { tenantId },
  );
}

export function createChatWidget(
  tenantId: string,
  body: {
    knowledge_base_id: string;
    name: string;
    allowed_origins: string[];
    daily_question_limit: number;
  },
) {
  return apiFetch<ChatWidget>(`v1/tenants/${tenantId}/chat-widgets`, {
    method: "POST",
    tenantId,
    body,
  });
}

/** The off switch. The origin allowlist only binds browsers and the daily cap
 *  only limits spend after the fact, so this is what an operator reaches for
 *  when a widget is being abused. */
export function setChatWidgetStatus(
  tenantId: string,
  widgetId: string,
  enabled: boolean,
) {
  return apiFetch<ChatWidget>(
    `v1/tenants/${tenantId}/chat-widgets/${widgetId}/status`,
    { method: "POST", tenantId, body: { enabled } },
  );
}

/** Streams a grounded answer as Server-Sent Events.
 *
 * Deliberately not routed through `apiFetch`: that helper reads the whole body
 * and JSON-parses it, which would buffer the stream and defeat the point. The
 * BFF proxy passes `text/event-stream` through unbuffered; this reads it
 * frame by frame.
 */
export async function* streamAnswer(
  tenantId: string,
  knowledgeBaseId: string,
  question: string,
  signal?: AbortSignal,
): AsyncGenerator<{ event: string; data: Record<string, unknown> }> {
  const response = await fetch(
    `/api/backend/v1/tenants/${tenantId}/knowledge-bases/${knowledgeBaseId}/answer`,
    {
      method: "POST",
      headers: { "content-type": "application/json", "x-tenant-id": tenantId },
      body: JSON.stringify({ question }),
      signal,
    },
  );
  if (!response.ok || !response.body) {
    throw new Error(`the answer could not be started (${response.status})`);
  }

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += value;
    // SSE frames are separated by a blank line. Anything after the last
    // separator is a partial frame and must stay buffered -- parsing it would
    // truncate a token mid-word.
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      let event = "message";
      let data = "";
      for (const line of frame.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7);
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      if (data) yield { event, data: JSON.parse(data) };
    }
  }
}

export function queryKnowledgeBase(
  tenantId: string,
  knowledgeBaseId: string,
  queryText: string,
  topK = 10,
) {
  return apiFetch<{ hits: KnowledgeBaseQueryHit[] }>(
    `v1/tenants/${tenantId}/knowledge-bases/${knowledgeBaseId}/query`,
    { method: "POST", tenantId, body: { query_text: queryText, top_k: topK } },
  );
}

// ---- Conversations ----

export function listConversations(tenantId: string) {
  return apiFetch<{ conversations: Conversation[] }>(`v1/tenants/${tenantId}/conversations`, {
    tenantId,
  });
}

export function getConversation(tenantId: string, conversationId: string) {
  return apiFetch<Conversation>(`v1/tenants/${tenantId}/conversations/${conversationId}`, { tenantId });
}

export function startConversation(tenantId: string, assistantId: string, title: string | null) {
  return apiFetch<{ id: string }>(`v1/tenants/${tenantId}/conversations`, {
    method: "POST",
    tenantId,
    body: { assistant_id: assistantId, title },
  });
}

// ---- Provider credentials ----

export function listProviderCredentials(tenantId: string) {
  return apiFetch<{ credentials: ProviderCredential[] }>(`v1/tenants/${tenantId}/provider-credentials`, {
    tenantId,
  });
}

export function storeProviderCredential(tenantId: string, provider: string, secret: string) {
  return apiFetch<ProviderCredential>(`v1/tenants/${tenantId}/provider-credentials`, {
    method: "POST",
    tenantId,
    body: { provider, secret },
  });
}

export function rotateProviderCredential(tenantId: string, credentialId: string, newSecret: string) {
  return apiFetch<ProviderCredential>(
    `v1/tenants/${tenantId}/provider-credentials/${credentialId}/rotate`,
    { method: "POST", tenantId, body: { new_secret: newSecret } },
  );
}

export function revokeProviderCredential(tenantId: string, credentialId: string) {
  return apiFetch<void>(`v1/tenants/${tenantId}/provider-credentials/${credentialId}`, {
    method: "DELETE",
    tenantId,
  });
}
