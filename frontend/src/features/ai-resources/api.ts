import { apiFetch } from "@/lib/api-client";
import type {
  Assistant,
  AccessLevel,
  Conversation,
  CrawlMode,
  ChatWidget,
  DataSource,
  DocumentDetail,
  KnowledgeBase,
  KnowledgeBaseDocument,
  KnowledgeBaseQueryHit,
  ConversationThread,
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

/** Re-runs ingestion for a document whose bytes are already stored. 202 --
 *  the work happens in a worker, so this means "queued", not "indexed". */
export function getDocumentDetail(
  tenantId: string,
  knowledgeBaseId: string,
  documentId: string,
  params: { limit?: number; offset?: number } = {},
) {
  const query = new URLSearchParams();
  if (params.limit !== undefined) query.set("limit", String(params.limit));
  if (params.offset !== undefined) query.set("offset", String(params.offset));
  const suffix = query.size > 0 ? `?${query.toString()}` : "";
  return apiFetch<DocumentDetail>(
    `v1/tenants/${tenantId}/knowledge-bases/${knowledgeBaseId}/documents/${documentId}${suffix}`,
    { tenantId },
  );
}

export function retryDocument(
  tenantId: string,
  knowledgeBaseId: string,
  documentId: string,
) {
  return apiFetch<void>(
    `v1/tenants/${tenantId}/knowledge-bases/${knowledgeBaseId}/documents/${documentId}/retry`,
    { method: "POST", tenantId },
  );
}

/** Removes the document, its chunks, its vectors and its stored bytes. */
export function deleteDocument(
  tenantId: string,
  knowledgeBaseId: string,
  documentId: string,
) {
  return apiFetch<void>(
    `v1/tenants/${tenantId}/knowledge-bases/${knowledgeBaseId}/documents/${documentId}`,
    { method: "DELETE", tenantId },
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

export function resyncDataSource(
  tenantId: string,
  knowledgeBaseId: string,
  dataSourceId: string,
) {
  return apiFetch<void>(
    `v1/tenants/${tenantId}/knowledge-bases/${knowledgeBaseId}/data-sources/${dataSourceId}/resync`,
    { method: "POST", tenantId },
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
 *
 * `assistantId` is optional. Supplied, the answer uses that assistant's own
 * model and persona instead of the platform default -- omitted, behaviour is
 * unchanged from before this parameter existed.
 */
export async function* streamAnswer(
  tenantId: string,
  knowledgeBaseId: string,
  question: string,
  signal?: AbortSignal,
  assistantId?: string,
  conversationId?: string,
): AsyncGenerator<{ event: string; data: Record<string, unknown> }> {
  const response = await fetch(
    `/api/backend/v1/tenants/${tenantId}/knowledge-bases/${knowledgeBaseId}/answer`,
    {
      method: "POST",
      headers: { "content-type": "application/json", "x-tenant-id": tenantId },
      // Built by spreading rather than by branching: three optional fields
      // produce eight branches, and the previous two-branch version already
      // had to be rewritten once to add one.
      body: JSON.stringify({
        question,
        ...(assistantId ? { assistant_id: assistantId } : {}),
        ...(conversationId ? { conversation_id: conversationId } : {}),
      }),
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

/** Bring-your-own-key: bill this tenant's own provider account for one model.
 *  Pass null to detach and return the model to the platform's key. */
export function setModelCredential(
  tenantId: string,
  modelConfigurationId: string,
  providerCredentialId: string | null,
) {
  return apiFetch<void>(
    `v1/tenants/${tenantId}/model-configurations/${modelConfigurationId}/credential`,
    {
      method: "PUT",
      body: { provider_credential_id: providerCredentialId },
      tenantId,
    },
  );
}

export function getConversationMessages(
  tenantId: string,
  conversationId: string,
  limit?: number,
) {
  // The server returns the *newest* `limit` turns. Growing the window is how
  // this screen pages backwards -- see `useConversationMessages` for why that
  // beats accumulating separate pages on the client.
  const query = limit ? `?limit=${limit}` : "";
  return apiFetch<ConversationThread>(
    `v1/tenants/${tenantId}/conversations/${conversationId}/messages${query}`,
    { tenantId },
  );
}

export function renameConversation(tenantId: string, conversationId: string, title: string) {
  return apiFetch<void>(`v1/tenants/${tenantId}/conversations/${conversationId}`, {
    method: "PATCH",
    body: { title },
    tenantId,
  });
}

export function deleteConversation(tenantId: string, conversationId: string) {
  return apiFetch<void>(`v1/tenants/${tenantId}/conversations/${conversationId}`, {
    method: "DELETE",
    tenantId,
  });
}

export function searchConversations(tenantId: string, q: string, allMembers = false) {
  const params = new URLSearchParams({ q, all_members: String(allMembers) });
  return apiFetch<{ conversations: Conversation[] }>(
    `v1/tenants/${tenantId}/conversations/search?${params}`,
    { tenantId },
  );
}

/** Every conversation in the tenant. Requires `tenant.conversations.view`;
 *  metadata only -- opening one audits the read. */
export function listTenantConversations(tenantId: string) {
  return apiFetch<{ conversations: Conversation[] }>(
    `v1/tenants/${tenantId}/conversations/all`,
    { tenantId },
  );
}

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
