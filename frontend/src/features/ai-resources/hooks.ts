"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "@/features/ai-resources/api";
import type { CrawlMode, Visibility } from "@/lib/types";

// Assistant hooks were removed with the tenant-facing Assistants screen.
// `useModelConfigurations` stays: it is the tenant's read-only view of the
// models the platform granted them, used to show budget and spend.

export function useModelConfigurations(tenantId: string | null) {
  return useQuery({
    queryKey: ["model-configurations", tenantId],
    queryFn: () => api.listModelConfigurations(tenantId!),
    enabled: Boolean(tenantId),
  });
}

// ---- Knowledge bases ----

export function useKnowledgeBases(tenantId: string | null) {
  return useQuery({
    queryKey: ["knowledge-bases", tenantId],
    queryFn: () => api.listKnowledgeBases(tenantId!),
    enabled: Boolean(tenantId),
  });
}

export function useCreateKnowledgeBase(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      name: string;
      description: string | null;
      visibility: Visibility;
      departmentId: string | null;
      teamId: string | null;
    }) => api.createKnowledgeBase(tenantId, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["knowledge-bases", tenantId] }),
  });
}

export function useKnowledgeBaseDocuments(
  tenantId: string | null,
  knowledgeBaseId: string | null,
) {
  return useQuery({
    queryKey: ["kb-documents", tenantId, knowledgeBaseId],
    queryFn: () => api.listDocuments(tenantId!, knowledgeBaseId!),
    enabled: Boolean(tenantId && knowledgeBaseId),
    // Ingestion is asynchronous, so a document sits in `processing` until a
    // worker finishes. Poll while any document is still in flight, and stop
    // once they have all settled -- a fixed interval would keep polling a
    // fully-ingested knowledge base forever.
    refetchInterval: (query) =>
      query.state.data?.documents.some((d) => d.status === "processing") ? 3000 : false,
  });
}

export function useUploadDocument(tenantId: string, knowledgeBaseId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => api.uploadDocument(tenantId, knowledgeBaseId, file),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: ["kb-documents", tenantId, knowledgeBaseId],
      }),
  });
}

export function useDataSources(
  tenantId: string | null,
  knowledgeBaseId: string | null,
) {
  return useQuery({
    queryKey: ["data-sources", tenantId, knowledgeBaseId],
    queryFn: () => api.listDataSources(tenantId!, knowledgeBaseId!),
    enabled: Boolean(tenantId && knowledgeBaseId),
    // A crawl runs for minutes to hours, so poll while one is in flight and
    // stop once everything has settled.
    refetchInterval: (query) =>
      query.state.data?.data_sources.some((s) => s.sync_status === "syncing")
        ? 5000
        : false,
  });
}

export function useCreateDataSource(tenantId: string, knowledgeBaseId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { urls: string[]; mode: CrawlMode }) =>
      api.createDataSource(tenantId, knowledgeBaseId, body),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["data-sources", tenantId, knowledgeBaseId],
      });
      // A crawl creates documents, so the document list is stale too.
      queryClient.invalidateQueries({
        queryKey: ["kb-documents", tenantId, knowledgeBaseId],
      });
    },
  });
}

export function useQueryKnowledgeBase(tenantId: string, knowledgeBaseId: string) {
  return useMutation({
    mutationFn: ({ queryText, topK }: { queryText: string; topK?: number }) =>
      api.queryKnowledgeBase(tenantId, knowledgeBaseId, queryText, topK),
  });
}

// ---- Conversations ----

export function useConversations(tenantId: string | null) {
  return useQuery({
    queryKey: ["conversations", tenantId],
    queryFn: () => api.listConversations(tenantId!),
    enabled: Boolean(tenantId),
  });
}

export function useConversation(tenantId: string | null, conversationId: string | null) {
  return useQuery({
    queryKey: ["conversation", tenantId, conversationId],
    queryFn: () => api.getConversation(tenantId!, conversationId!),
    enabled: Boolean(tenantId && conversationId),
  });
}

/**
 * One conversation's turns, newest-anchored.
 *
 * **`limit` grows to page backwards; older pages are not accumulated
 * separately.** The obvious design -- keep the live newest page and stack
 * fetched older pages beside it -- has a gap that only appears in a busy
 * conversation: the live window slides forward as messages arrive, so after a
 * few new turns the space between the top of the live window and the last
 * older page belongs to neither, and those turns vanish from the thread. That
 * is precisely the "do not skip" failure, and it would be intermittent and
 * near-impossible to reproduce on demand.
 *
 * Widening one contiguous newest-anchored window cannot produce a gap, needs
 * no cursor bookkeeping, and makes duplicates unrepresentable -- the server
 * returns each turn once. The cost is re-reading the loaded window on each
 * poll, which for a support thread is tens of rows.
 */
export function useConversationMessages(
  tenantId: string,
  conversationId: string | null,
  options?: { live?: boolean; limit?: number },
) {
  return useQuery({
    queryKey: ["conversation-messages", tenantId, conversationId, options?.limit],
    queryFn: () => api.getConversationMessages(tenantId, conversationId!, options?.limit),
    // Keeps the previous window on screen while a wider one loads, so
    // scrolling up does not blank the thread and lose the reading position.
    placeholderData: (previous) => previous,
    enabled: Boolean(conversationId),
    // Only the agent thread view opts in: a visitor's next message, or a
    // colleague's reply, must show up without the agent hitting refresh.
    // The read-only history dialog elsewhere has no reason to keep polling
    // a thread that is not receiving new turns.
    refetchInterval: options?.live ? 4000 : false,
  });
}

export function useConversationSearch(
  tenantId: string,
  q: string,
  allMembers: boolean,
) {
  return useQuery({
    queryKey: ["conversation-search", tenantId, q, allMembers],
    queryFn: () => api.searchConversations(tenantId, q, allMembers),
    // Only once there is something to search for: an empty query would return
    // the whole tenant and read as "search is broken".
    enabled: q.trim().length > 1,
  });
}

export function useTenantConversations(tenantId: string, enabled: boolean) {
  return useQuery({
    queryKey: ["tenant-conversations", tenantId],
    queryFn: () => api.listTenantConversations(tenantId),
    enabled,
  });
}

export function useRenameConversation(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (vars: { conversationId: string; title: string }) =>
      api.renameConversation(tenantId, vars.conversationId, vars.title),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["conversations", tenantId] });
      queryClient.invalidateQueries({ queryKey: ["conversation-messages", tenantId] });
    },
  });
}

export function useDeleteConversation(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (conversationId: string) => api.deleteConversation(tenantId, conversationId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["conversations", tenantId] }),
  });
}

export function useStartConversation(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ assistantId, title }: { assistantId: string; title: string | null }) =>
      api.startConversation(tenantId, assistantId, title),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["conversations", tenantId] }),
  });
}

// Provider-credential hooks were removed with the tenant-facing BYOK surface.

export function useChatWidgets(tenantId: string | null) {
  return useQuery({
    queryKey: ["chat-widgets", tenantId],
    queryFn: () => api.listChatWidgets(tenantId!),
    enabled: Boolean(tenantId),
  });
}

export function useCreateChatWidget(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      knowledge_base_id: string;
      name: string;
      allowed_origins: string[];
      daily_question_limit: number;
    }) => api.createChatWidget(tenantId, body),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["chat-widgets", tenantId] }),
  });
}

export function useSetChatWidgetStatus(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ widgetId, enabled }: { widgetId: string; enabled: boolean }) =>
      api.setChatWidgetStatus(tenantId, widgetId, enabled),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["chat-widgets", tenantId] }),
  });
}

/** One document's indexed text. `enabled` so the query only runs while the
 *  inspector is actually open -- a document with hundreds of chunks is not
 *  something to fetch for every row in the list. */
export function useDocumentDetail(
  tenantId: string,
  knowledgeBaseId: string,
  documentId: string | null,
) {
  return useQuery({
    queryKey: ["kb-document-detail", tenantId, knowledgeBaseId, documentId],
    queryFn: () =>
      api.getDocumentDetail(tenantId, knowledgeBaseId, documentId as string),
    enabled: Boolean(documentId),
  });
}

export function useResyncDataSource(tenantId: string, knowledgeBaseId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (dataSourceId: string) =>
      api.resyncDataSource(tenantId, knowledgeBaseId, dataSourceId),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["data-sources", tenantId, knowledgeBaseId],
      });
      // A re-crawl rewrites documents, so the list beside it is stale too.
      queryClient.invalidateQueries({
        queryKey: ["kb-documents", tenantId, knowledgeBaseId],
      });
    },
  });
}

export function useRetryDocument(tenantId: string, knowledgeBaseId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (documentId: string) =>
      api.retryDocument(tenantId, knowledgeBaseId, documentId),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: ["kb-documents", tenantId, knowledgeBaseId],
      }),
  });
}

export function useDeleteDocument(tenantId: string, knowledgeBaseId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (documentId: string) =>
      api.deleteDocument(tenantId, knowledgeBaseId, documentId),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: ["kb-documents", tenantId, knowledgeBaseId],
      }),
  });
}
