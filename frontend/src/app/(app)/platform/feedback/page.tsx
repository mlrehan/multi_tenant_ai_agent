"use client";

import { X } from "lucide-react";
import { useState } from "react";

import { PageHeader } from "@/components/shared/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { FeedbackFilters } from "@/features/ai-resources/api";
import {
  FeedbackFilterBar,
  FeedbackList,
  FeedbackSummary,
  PAGE_SIZE,
} from "@/features/ai-resources/feedback/feedback-review";
import { usePlatformAnswerFeedback } from "@/features/ai-resources/hooks";
import { cn } from "@/lib/utils";

/** Answer ratings across every tenant, for the platform operator.
 *
 *  **Every page load is recorded in the audit log** (server-side): this shows
 *  tenants' conversation content to someone outside the tenant, and the
 *  notice below says so rather than leaving it to be discovered.
 *
 *  The "by tenant" table is sorted worst-first -- most "not helpful" ratings
 *  -- because the operator's question is "whose answers are landing badly",
 *  and clicking a tenant filters the list below to theirs. */
export default function PlatformFeedbackPage() {
  const [filters, setFilters] = useState<FeedbackFilters>({ limit: PAGE_SIZE, offset: 0 });
  const feedback = usePlatformAnswerFeedback(filters);
  const byTenant = feedback.data?.by_tenant ?? [];
  const helpful = byTenant.reduce((n, t) => n + t.helpful, 0);
  const notHelpful = byTenant.reduce((n, t) => n + t.not_helpful, 0);
  const selected = byTenant.find((t) => t.tenant_id === filters.tenantId);

  return (
    <div>
      <PageHeader
        title="Answer feedback"
        description="How every tenant's users rate the AI's answers. Viewing this page is recorded in the audit log, because it shows tenants' conversation content."
      />

      {feedback.data && <FeedbackSummary helpful={helpful} notHelpful={notHelpful} />}

      {byTenant.length > 0 && (
        <Card className="mb-4">
          <CardHeader>
            <CardTitle className="text-base">By tenant</CardTitle>
            <CardDescription>Most “not helpful” first. Select a tenant to see only its ratings.</CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Tenant</TableHead>
                  <TableHead className="text-right">Ratings</TableHead>
                  <TableHead className="text-right">Helpful</TableHead>
                  <TableHead className="text-right">Not helpful</TableHead>
                  <TableHead className="text-right">Satisfaction</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {byTenant.map((t) => (
                  <TableRow
                    key={t.tenant_id}
                    className={cn("cursor-pointer", filters.tenantId === t.tenant_id && "bg-muted/60")}
                    tabIndex={0}
                    aria-pressed={filters.tenantId === t.tenant_id}
                    onClick={() => setFilters({ ...filters, tenantId: t.tenant_id, offset: 0 })}
                    onKeyDown={(e) =>
                      (e.key === "Enter" || e.key === " ") &&
                      setFilters({ ...filters, tenantId: t.tenant_id, offset: 0 })
                    }
                  >
                    <TableCell className="font-medium">{t.tenant_name ?? t.tenant_id}</TableCell>
                    <TableCell className="text-right tabular-nums">{t.total}</TableCell>
                    <TableCell className="text-right tabular-nums">{t.helpful}</TableCell>
                    <TableCell className="text-right tabular-nums">{t.not_helpful}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {t.total ? `${Math.round((t.helpful / t.total) * 100)}%` : "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      <FeedbackFilterBar
        filters={filters}
        onChange={setFilters}
        extra={
          selected ? (
            <Button
              size="sm"
              variant="outline"
              onClick={() => setFilters({ ...filters, tenantId: undefined, offset: 0 })}
            >
              {selected.tenant_name ?? "Tenant"} <X className="size-3.5" />
            </Button>
          ) : null
        }
      />
      <FeedbackList
        items={feedback.data?.items}
        total={feedback.data?.total ?? 0}
        isLoading={feedback.isLoading}
        error={feedback.error}
        filters={filters}
        onPage={(offset) => setFilters({ ...filters, offset })}
        showTenant
      />
    </div>
  );
}
