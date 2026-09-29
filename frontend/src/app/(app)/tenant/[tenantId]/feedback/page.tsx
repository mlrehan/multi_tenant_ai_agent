"use client";

import { use as usePromise, useState } from "react";

import { PageHeader } from "@/components/shared/page-header";
import type { FeedbackFilters } from "@/features/ai-resources/api";
import {
  FeedbackFilterBar,
  FeedbackList,
  FeedbackSummary,
  PAGE_SIZE,
} from "@/features/ai-resources/feedback/feedback-review";
import { useAnswerFeedback } from "@/features/ai-resources/hooks";

/** What people thought of this tenant's AI answers.
 *
 *  Same permission as reading every conversation (`tenant.conversations.view`):
 *  each rating carries the visitor's question and the answer they rated. The
 *  headline numbers are the whole tenant's; the filters narrow the list. */
export default function TenantFeedbackPage({
  params,
}: {
  params: Promise<{ tenantId: string }>;
}) {
  const { tenantId } = usePromise(params);
  const [filters, setFilters] = useState<FeedbackFilters>({ limit: PAGE_SIZE, offset: 0 });
  const feedback = useAnswerFeedback(tenantId, filters);

  return (
    <div>
      <PageHeader
        title="Answer feedback"
        description="Every 👍 and 👎 given to your chatbot's answers — in your website chatbot and in the console. Open one to see the exact answer that was rated."
      />
      {feedback.data && (
        <FeedbackSummary helpful={feedback.data.helpful} notHelpful={feedback.data.not_helpful} />
      )}
      <FeedbackFilterBar filters={filters} onChange={setFilters} />
      <FeedbackList
        items={feedback.data?.items}
        total={feedback.data?.total ?? 0}
        isLoading={feedback.isLoading}
        error={feedback.error}
        filters={filters}
        onPage={(offset) => setFilters({ ...filters, offset })}
      />
    </div>
  );
}
