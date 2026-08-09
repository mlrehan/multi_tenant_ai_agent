import { cn } from "@/lib/utils";
import type { RiskLevel } from "@/lib/types";

type Tone = "success" | "warning" | "danger" | "info" | "neutral";

const STATUS_TONE: Record<string, Tone> = {
  // Tenant / membership / assistant / conversation statuses.
  active: "success",
  published: "success",
  ready: "success",
  invited: "info",
  pending: "info",
  pending_verification: "info",
  draft: "neutral",
  processing: "info",
  suspended: "warning",
  archived: "neutral",
  revoked: "danger",
  deactivated: "danger",
  failed: "danger",
  expired: "danger",
};

const TONE_CLASSES: Record<Tone, string> = {
  success: "bg-status-success/10 text-status-success border-status-success/25",
  warning: "bg-status-warning/10 text-status-warning border-status-warning/25",
  danger: "bg-status-danger/10 text-status-danger border-status-danger/25",
  info: "bg-status-info/10 text-status-info border-status-info/25",
  neutral: "bg-muted text-muted-foreground border-border",
};

export function StatusBadge({ status, className }: { status: string; className?: string }) {
  const tone = STATUS_TONE[status] ?? "neutral";
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium capitalize",
        TONE_CLASSES[tone],
        className,
      )}
    >
      <span className="size-1.5 rounded-full bg-current" aria-hidden />
      {status.replace(/_/g, " ")}
    </span>
  );
}

const RISK_CLASSES: Record<RiskLevel, string> = {
  low: "bg-risk-low/10 text-risk-low border-risk-low/25",
  medium: "bg-risk-medium/10 text-risk-medium border-risk-medium/25",
  high: "bg-risk-high/10 text-risk-high border-risk-high/25",
  critical: "bg-risk-critical/10 text-risk-critical border-risk-critical/25",
};

export function RiskBadge({ level, className }: { level: RiskLevel; className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium uppercase tracking-wide",
        RISK_CLASSES[level],
        className,
      )}
    >
      {level}
    </span>
  );
}
