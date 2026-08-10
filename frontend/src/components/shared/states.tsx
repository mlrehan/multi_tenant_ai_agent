import type { LucideIcon } from "lucide-react";
import { AlertCircle, Inbox, Lock } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { isApiError } from "@/lib/api-client";

export function TableSkeleton({ rows = 5, columns = 4 }: { rows?: number; columns?: number }) {
  return (
    <div className="space-y-2" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }).map((_, rowIndex) => (
        <div key={rowIndex} className="flex gap-3">
          {Array.from({ length: columns }).map((_, colIndex) => (
            <Skeleton key={colIndex} className="h-8 flex-1" />
          ))}
        </div>
      ))}
    </div>
  );
}

/** An empty state is an invitation to act, not a dead end -- so it always
 * names the next step rather than only reporting the absence. */
export function EmptyState({
  icon: Icon = Inbox,
  title,
  description,
  action,
}: {
  icon?: LucideIcon;
  title: string;
  description: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-border px-6 py-12 text-center">
      <Icon className="mb-3 size-8 text-muted-foreground" />
      <h3 className="text-sm font-medium">{title}</h3>
      <p className="mt-1 max-w-sm text-sm text-muted-foreground">{description}</p>
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

/**
 * Turns an API failure into something a human can act on. A 403 here is
 * genuinely different from an error -- the request worked, the answer is
 * "you don't have that permission" -- so it gets its own presentation
 * rather than being reported as a failure.
 */
export function ErrorState({
  error,
  resource,
  scope = "tenant",
}: {
  error: unknown;
  resource: string;
  /** Which permission system governs this resource, and therefore who can
   *  grant it. Platform and tenant permissions live in separate tables and a
   *  tenant role can never hold a platform one, so telling someone denied a
   *  *platform* permission to "ask a tenant administrator" sends them to a
   *  person who structurally cannot help. */
  scope?: "tenant" | "platform";
}) {
  if (isApiError(error) && error.isForbidden) {
    return (
      <Alert>
        <Lock className="size-4" />
        <AlertTitle>You don&apos;t have access to {resource}</AlertTitle>
        <AlertDescription>
          {scope === "platform"
            ? "This is a platform-scope permission. Ask a platform administrator to grant it — a tenant administrator cannot."
            : "Ask a tenant administrator to grant you the required permission."}
        </AlertDescription>
      </Alert>
    );
  }

  if (isApiError(error) && error.isNotFound) {
    return (
      <Alert>
        <AlertCircle className="size-4" />
        <AlertTitle>Not found</AlertTitle>
        <AlertDescription>
          {resource} doesn&apos;t exist, or you don&apos;t have access to it.
        </AlertDescription>
      </Alert>
    );
  }

  return (
    <Alert variant="destructive">
      <AlertCircle className="size-4" />
      <AlertTitle>Couldn&apos;t load {resource}</AlertTitle>
      <AlertDescription>
        {isApiError(error) ? error.message : "Something went wrong. Try again."}
      </AlertDescription>
    </Alert>
  );
}
