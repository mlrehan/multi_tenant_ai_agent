"use client";

import { useEffect, useState } from "react";
import { ShieldAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { IdentityChip } from "@/components/shared/identity-chip";
import { useSession } from "@/features/auth/hooks";
import { useEndImpersonation } from "@/features/platform/hooks";
import { useImpersonationStore } from "@/stores/impersonation-store";

function useCountdown(expiresAt: string | null) {
  // Ticks a clock rather than storing the remaining seconds directly: the
  // remaining time is *derived* from (expiry - now), so keeping it in state
  // would mean syncing state to state. Only the interval callback sets
  // state, never the effect body.
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!expiresAt) return;
    const interval = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(interval);
  }, [expiresAt]);

  if (!expiresAt) return null;
  return Math.max(0, Math.round((new Date(expiresAt).getTime() - now) / 1000));
}

function formatCountdown(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

/**
 * A persistent, impossible-to-miss bar whenever the current session is
 * impersonating a tenant user. This is not decoration -- a support session
 * that doesn't visibly announce itself is exactly how "I forgot I was
 * impersonating and did something as them" incidents happen. Renders
 * whenever the access token's `act` claim is present (per useSession,
 * decoded server-side), even if the client-side impersonation-store detail
 * record is missing (e.g. a page opened fresh in a new tab) -- degrading to
 * a minimal banner rather than not showing at all in that case.
 */
export function ImpersonationBanner() {
  const { data: session } = useSession();
  const details = useImpersonationStore((s) => s.active);
  const endImpersonation = useEndImpersonation();
  const remaining = useCountdown(details?.expiresAt ?? null);

  if (!session?.impersonating) return null;

  const sessionId = session.impersonationSessionId;

  return (
    <div className="flex items-center gap-3 border-b border-status-warning/30 bg-status-warning/10 px-4 py-2 text-sm text-foreground">
      <ShieldAlert className="size-4 shrink-0 text-status-warning" />
      <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-1">
        <span className="font-medium">Viewing as another user</span>
        {details && (
          <>
            <span className="text-muted-foreground">—</span>
            <IdentityChip value={details.targetUserId} label="target user" />
            <span className="text-muted-foreground">in tenant</span>
            <IdentityChip value={details.tenantId} label="tenant" />
          </>
        )}
        {remaining !== null && (
          <span className="ml-1 font-mono text-xs text-status-warning tabular-nums">
            expires in {formatCountdown(remaining)}
          </span>
        )}
      </div>
      <Button
        size="sm"
        variant="outline"
        className="h-7 shrink-0 border-status-warning/40 text-xs hover:bg-status-warning/15"
        disabled={!sessionId || endImpersonation.isPending}
        onClick={() => sessionId && endImpersonation.mutate(sessionId)}
      >
        End session
      </Button>
    </div>
  );
}
