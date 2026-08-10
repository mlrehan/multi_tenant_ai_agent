"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "@/features/ai-resources/api";
import type { AccessLevel, CrawlMode, Visibility } from "@/lib/types";

// ---- Assistants ----

export function useAssistants(tenantId: string | null) {
  return useQuery({
    queryKey: ["assistants", tenantId],
    queryFn: () => api.listAssistants(tenantId!),
    enabled: Boolean(tenantId),
  });
}

export function useAssistant(tenantId: string | null, assistantId: string | null) {
  return useQuery({
    queryKey: ["assistant", tenantId, assistantId],
    queryFn: () => api.getAssistant(tenantId!, assistantId!),
    enabled: Boolean(tenantId && assistantId),
  });
}

export function useCreateAssistant(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      name: string;
      description: string | null;
      modelConfigurationId: string;
      visibility: Visibility;
      departmentId: string | null;
      teamId: string | null;
      systemPrompt: string | null;
    }) => api.createAssistant(tenantId, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["assistants", tenantId] }),
  });
}

export function useModelConfigurations(tenantId: string | null) {
  return useQuery({
    queryKey: ["model-configurations", tenantId],
    queryFn: () => api.listModelConfigurations(tenantId!),
    enabled: Boolean(tenantId),
  });
}

export function useUpdateAssistant(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      assistantId,
      ...body
    }: {
      assistantId: string;
      name: string;
      description: string | null;
      modelConfigurationId: string;
      systemPrompt: string | null;
    }) => api.updateAssistant(tenantId, assistantId, body),
    onSuccess: (_, vars) => {
      queryClient.invalidateQueries({ queryKey: ["assistants", tenantId] });
      queryClient.invalidateQueries({ queryKey: ["assistant", tenantId, vars.assistantId] });
    },
  });
}

export function useArchiveAssistant(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (assistantId: string) => api.archiveAssistant(tenantId, assistantId),
    onSuccess: (_, assistantId) => {
      queryClient.invalidateQueries({ queryKey: ["assistants", tenantId] });
      queryClient.invalidateQueries({ queryKey: ["assistant", tenantId, assistantId] });
    },
  });
}

export function usePublishAssistant(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (assistantId: string) => api.publishAssistant(tenantId, assistantId),
    onSuccess: (_, assistantId) => {
      queryClient.invalidateQueries({ queryKey: ["assistants", tenantId] });
      queryClient.invalidateQueries({ queryKey: ["assistant", tenantId, assistantId] });
    },
  });
}

export function useChangeAssistantVisibility(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      assistantId,
      visibility,
      departmentId,
      teamId,
    }: {
      assistantId: string;
      visibility: Visibility;
      departmentId: string | null;
      teamId: string | null;
    }) => api.changeAssistantVisibility(tenantId, assistantId, { visibility, departmentId, teamId }),
    onSuccess: (_, vars) => {
      queryClient.invalidateQueries({ queryKey: ["assistants", tenantId] });
      queryClient.invalidateQueries({ queryKey: ["assistant", tenantId, vars.assistantId] });
    },
  });
}

export function useAssistantAccess(tenantId: string, assistantId: string) {
  const grant = useMutation({
    mutationFn: ({ membershipId, accessLevel }: { membershipId: string; accessLevel: AccessLevel }) =>
      api.grantAssistantAccess(tenantId, assistantId, membershipId, accessLevel),
  });
  const revoke = useMutation({
    mutationFn: (membershipId: string) => api.revokeAssistantAccess(tenantId, assistantId, membershipId),
  });
  return { grant, revoke };
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

export function useStartConversation(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ assistantId, title }: { assistantId: string; title: string | null }) =>
      api.startConversation(tenantId, assistantId, title),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["conversations", tenantId] }),
  });
}

// ---- Provider credentials ----

export function useProviderCredentials(tenantId: string | null) {
  return useQuery({
    queryKey: ["provider-credentials", tenantId],
    queryFn: () => api.listProviderCredentials(tenantId!),
    enabled: Boolean(tenantId),
  });
}

export function useStoreProviderCredential(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ provider, secret }: { provider: string; secret: string }) =>
      api.storeProviderCredential(tenantId, provider, secret),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["provider-credentials", tenantId] }),
  });
}

export function useRotateProviderCredential(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ credentialId, newSecret }: { credentialId: string; newSecret: string }) =>
      api.rotateProviderCredential(tenantId, credentialId, newSecret),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["provider-credentials", tenantId] }),
  });
}

export function useRevokeProviderCredential(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (credentialId: string) => api.revokeProviderCredential(tenantId, credentialId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["provider-credentials", tenantId] }),
  });
}

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
