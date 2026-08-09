"use client";

import { use as usePromise } from "react";
import { MessagesSquare } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
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
import { IdentityChip } from "@/components/shared/identity-chip";
import { useConversations } from "@/features/ai-resources/hooks";

export default function ConversationsPage({
  params,
}: {
  params: Promise<{ tenantId: string }>;
}) {
  const { tenantId } = usePromise(params);
  const { data, isLoading, error } = useConversations(tenantId);

  const conversations = data?.conversations;

  return (
    <div>
      <PageHeader
        title="Conversations"
        description="Your own chat sessions. Other people's conversation content is never listed here — only an auditor with the right permission can open one, and only its metadata."
      />

      {isLoading && <TableSkeleton rows={4} columns={4} />}
      {error && <ErrorState error={error} resource="conversations" />}

      {conversations && conversations.length === 0 && (
        <EmptyState
          icon={MessagesSquare}
          title="No conversations yet"
          description="Conversations appear here once you start chatting with a published assistant."
        />
      )}

      {conversations && conversations.length > 0 && (
        <Card>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Title</TableHead>
                  <TableHead>Assistant</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Last message</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {conversations.map((conversation) => (
                  <TableRow key={conversation.id}>
                    <TableCell className="font-medium">
                      {conversation.title ?? (
                        <span className="text-muted-foreground">Untitled</span>
                      )}
                    </TableCell>
                    <TableCell>
                      <IdentityChip value={conversation.assistant_id} label="assistant" />
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={conversation.status} />
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground tabular-nums">
                      {conversation.last_message_at
                        ? new Date(conversation.last_message_at).toLocaleString()
                        : "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
