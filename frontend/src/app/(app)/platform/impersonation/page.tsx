"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { ShieldAlert } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { PageHeader } from "@/components/shared/page-header";
import { TenantPicker } from "@/components/shared/tenant-picker";
import { UserPicker } from "@/components/shared/user-picker";
import { useStartImpersonation } from "@/features/platform/hooks";
import { useSession } from "@/features/auth/hooks";
import { isApiError } from "@/lib/api-client";

export default function ImpersonationPage() {
  const router = useRouter();
  const { data: session } = useSession();
  const startImpersonation = useStartImpersonation();
  const [tenantId, setTenantId] = useState("");
  const [targetUserId, setTargetUserId] = useState("");
  const [reason, setReason] = useState("");

  const alreadyImpersonating = Boolean(session?.impersonating);
  const isValid = tenantId.trim() && targetUserId.trim() && reason.trim().length >= 10;

  async function handleStart() {
    try {
      await startImpersonation.mutateAsync({
        tenantId: tenantId.trim(),
        targetUserId: targetUserId.trim(),
        reason: reason.trim(),
      });
      toast.success("Impersonation session started");
      router.push(`/tenant/${tenantId.trim()}/dashboard`);
    } catch (err) {
      toast.error(isApiError(err) ? err.message : "Couldn't start the session.");
    }
  }

  return (
    <div className="max-w-2xl">
      <PageHeader
        title="Support impersonation"
        description="View the console as a tenant user to reproduce an issue they've reported."
      />

      <Alert className="mb-6">
        <ShieldAlert className="size-4" />
        <AlertTitle>Impersonation is deliberately limited</AlertTitle>
        <AlertDescription>
          You act with the target&apos;s identity but not their full privileges: role management,
          member management, credential access, data export, and any high or critical-risk
          permission are stripped for the duration. Every action is recorded against your platform
          account, not theirs.
        </AlertDescription>
      </Alert>

      {alreadyImpersonating ? (
        <Alert variant="destructive">
          <AlertTitle>A session is already active</AlertTitle>
          <AlertDescription>
            End the current impersonation session from the banner at the top of the page before
            starting another.
          </AlertDescription>
        </Alert>
      ) : (
        <Card>
          <CardHeader>
            <CardTitle>Start a session</CardTitle>
            <CardDescription>
              Sessions are time-boxed to 30 minutes and end automatically.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div>
              <Label htmlFor="tenant-id">Tenant</Label>
              <div className="mt-1.5">
                <TenantPicker id="tenant-id" value={tenantId} onChange={setTenantId} />
              </div>
            </div>
            <div>
              <Label htmlFor="target-user-id">Target user</Label>
              <div className="mt-1.5">
                <UserPicker id="target-user-id" value={targetUserId} onChange={setTargetUserId} />
              </div>
              <p className="mt-1 text-xs text-muted-foreground">
                The user must have an active membership in the selected tenant — checked when the
                session starts.
              </p>
            </div>
            <div>
              <Label htmlFor="reason">Reason</Label>
              <Textarea
                id="reason"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="Ticket #1234 — user reports assistants list is empty after role change"
                className="mt-1.5"
                rows={3}
              />
              <p className="mt-1 text-xs text-muted-foreground">
                Recorded in the audit log. Reference a ticket where possible (10 characters
                minimum).
              </p>
            </div>
            <Button
              size="sm"
              disabled={!isValid || startImpersonation.isPending}
              onClick={handleStart}
            >
              {startImpersonation.isPending ? "Starting…" : "Start session"}
            </Button>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
