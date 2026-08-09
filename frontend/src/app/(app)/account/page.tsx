"use client";

import { useState } from "react";
import { toast } from "sonner";
import { KeyRound, ShieldCheck, Smartphone } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { PageHeader } from "@/components/shared/page-header";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/shared/states";
import { StatusBadge } from "@/components/shared/status-badge";
import { IdentityChip } from "@/components/shared/identity-chip";
import { FieldError } from "@/components/shared/field-error";
import {
  useChangePassword,
  useConfirmTotpEnrollment,
  useLogoutAll,
  useMyAccount,
  useStartTotpEnrollment,
} from "@/features/auth/hooks";
import { isApiError } from "@/lib/api-client";
import type { MfaMethodSummary } from "@/lib/types";

export default function AccountPage() {
  const account = useMyAccount();

  return (
    <div>
      <PageHeader
        eyebrow="Account"
        title="My identity"
        description="Your account, how you sign in, and the second factors protecting it."
      />

      {account.isLoading && <TableSkeleton rows={5} columns={2} />}
      {account.error && <ErrorState error={account.error} resource="your account" />}

      {account.data && (
        <div className="grid gap-6 lg:grid-cols-2">
          <Card className="lg:col-span-2">
            <CardHeader>
              <CardTitle>Profile</CardTitle>
              <CardDescription>Read-only. Email changes are not supported yet.</CardDescription>
            </CardHeader>
            <CardContent>
              <dl className="grid gap-x-8 gap-y-4 sm:grid-cols-2 lg:grid-cols-4">
                <Field label="Email">
                  <span className="text-sm">{account.data.email}</span>
                </Field>
                <Field label="Account status">
                  <StatusBadge status={account.data.status} />
                </Field>
                <Field label="Email verified">
                  {account.data.email_verified ? (
                    <Badge variant="outline">Verified</Badge>
                  ) : (
                    <span
                      className="text-xs text-muted-foreground"
                      title="This deployment has no real email provider wired in yet, so verification links are never delivered."
                    >
                      Not verified — no mail provider configured
                    </span>
                  )}
                </Field>
                <Field label="User ID">
                  <IdentityChip value={account.data.user_id} label="user" />
                </Field>
                <Field label="Created">
                  <span className="text-sm tabular-nums">
                    {new Date(account.data.created_at).toLocaleString()}
                  </span>
                </Field>
                <Field label="Last sign-in">
                  <span className="text-sm tabular-nums">
                    {account.data.last_login_at
                      ? new Date(account.data.last_login_at).toLocaleString()
                      : "—"}
                  </span>
                </Field>
              </dl>
            </CardContent>
          </Card>

          <ChangePasswordCard hasPassword={account.data.has_password} />
          <MfaCard methods={account.data.mfa_methods} />

          <Card>
            <CardHeader>
              <CardTitle>Linked sign-in providers</CardTitle>
              <CardDescription>
                Social accounts that can sign in to this identity.
              </CardDescription>
            </CardHeader>
            <CardContent>
              {account.data.linked_providers.length === 0 ? (
                <EmptyState
                  title="No linked providers"
                  description="You sign in with a password only. Linking Google or Facebook is done from the login screen."
                />
              ) : (
                <ul className="divide-y divide-border">
                  {account.data.linked_providers.map((p) => (
                    <li key={p.provider} className="flex items-center justify-between py-3">
                      <div>
                        <p className="text-sm font-medium capitalize">{p.provider}</p>
                        <p className="text-xs text-muted-foreground">
                          {p.provider_email ?? "no email shared"}
                        </p>
                      </div>
                      <span className="text-xs text-muted-foreground tabular-nums">
                        linked {new Date(p.linked_at).toLocaleDateString()}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>

          <SessionsCard />
        </div>
      )}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</dt>
      <dd className="mt-1.5">{children}</dd>
    </div>
  );
}

function ChangePasswordCard({ hasPassword }: { hasPassword: boolean }) {
  const changePassword = useChangePassword();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [violations, setViolations] = useState<string[]>([]);

  const mismatch = confirm.length > 0 && next !== confirm;
  const canSubmit = hasPassword && current.length > 0 && next.length > 0 && !mismatch;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setViolations([]);
    try {
      await changePassword.mutateAsync({ currentPassword: current, newPassword: next });
      toast.success("Password changed. Sign in again with your new password.");
    } catch (err) {
      // A 422 carries the policy violations that actually failed -- show them
      // inline against the field rather than flattening to a generic toast.
      if (isApiError(err) && err.violations?.length) {
        setViolations(err.violations);
        return;
      }
      toast.error(
        isApiError(err) && err.status === 401
          ? "That current password isn't right."
          : "Couldn't change the password.",
      );
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <KeyRound className="size-4" />
          Password
        </CardTitle>
        <CardDescription>
          Changing it signs you out everywhere, including this browser — that&apos;s the point:
          if the old password leaked, a live session elsewhere is what you&apos;re closing.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {!hasPassword ? (
          <EmptyState
            title="No password set"
            description="This account signs in through a linked provider only, so there's no password to change."
          />
        ) : (
          <form onSubmit={submit} className="space-y-4">
            <div>
              <Label htmlFor="current-password">Current password</Label>
              <Input
                id="current-password"
                type="password"
                autoComplete="current-password"
                value={current}
                onChange={(e) => setCurrent(e.target.value)}
                className="mt-1.5"
              />
            </div>
            <div>
              <Label htmlFor="new-password">New password</Label>
              <Input
                id="new-password"
                type="password"
                autoComplete="new-password"
                value={next}
                onChange={(e) => setNext(e.target.value)}
                className="mt-1.5"
              />
              {violations.length > 0 && (
                <ul className="mt-1.5 space-y-0.5">
                  {violations.map((v) => (
                    <li key={v}>
                      <FieldError message={v} />
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <div>
              <Label htmlFor="confirm-password">Confirm new password</Label>
              <Input
                id="confirm-password"
                type="password"
                autoComplete="new-password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                className="mt-1.5"
              />
              {mismatch && <FieldError message="Passwords don't match." />}
            </div>
            <Button type="submit" size="sm" disabled={!canSubmit || changePassword.isPending}>
              {changePassword.isPending ? "Changing…" : "Change password"}
            </Button>
          </form>
        )}
      </CardContent>
    </Card>
  );
}

function MfaCard({ methods }: { methods: MfaMethodSummary[] }) {
  const start = useStartTotpEnrollment();
  const confirmEnrollment = useConfirmTotpEnrollment();
  const [pending, setPending] = useState<{ id: string; uri: string; secret: string } | null>(null);
  const [code, setCode] = useState("");

  async function beginEnrollment() {
    try {
      const result = await start.mutateAsync();
      setPending({
        id: result.mfa_method_id,
        uri: result.provisioning_uri,
        secret: result.secret,
      });
    } catch {
      toast.error("Couldn't start enrollment.");
    }
  }

  async function finishEnrollment(e: React.FormEvent) {
    e.preventDefault();
    if (!pending) return;
    try {
      await confirmEnrollment.mutateAsync({ mfaMethodId: pending.id, code });
      toast.success("Authenticator app enrolled.");
      setPending(null);
      setCode("");
    } catch {
      toast.error("That code didn't verify. Check your authenticator and try again.");
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ShieldCheck className="size-4" />
          Multi-factor authentication
        </CardTitle>
        <CardDescription>
          A second factor is required at sign-in once any method is verified.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {methods.length === 0 ? (
          <EmptyState
            icon={Smartphone}
            title="No second factor enrolled"
            description="Your account is protected by a password alone. Enroll an authenticator app to add a second factor."
          />
        ) : (
          <ul className="divide-y divide-border">
            {methods.map((m) => (
              <li key={m.id} className="flex items-center justify-between py-3">
                <div>
                  <p className="text-sm font-medium">
                    {m.label ?? m.type.toUpperCase()}
                    {m.is_primary && (
                      <Badge variant="outline" className="ml-2">
                        primary
                      </Badge>
                    )}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {m.verified ? "Verified" : "Awaiting verification"} · added{" "}
                    {new Date(m.created_at).toLocaleDateString()}
                  </p>
                </div>
                <StatusBadge status={m.verified ? "active" : "invited"} />
              </li>
            ))}
          </ul>
        )}

        {pending ? (
          <form onSubmit={finishEnrollment} className="space-y-3 rounded-lg border border-border p-4">
            <p className="text-sm font-medium">Scan or paste this into your authenticator</p>
            <code className="block break-all rounded bg-muted px-2 py-1.5 font-mono text-xs">
              {pending.secret}
            </code>
            <p className="text-xs text-muted-foreground">
              Then enter the 6-digit code it shows to confirm enrollment.
            </p>
            <div className="flex items-end gap-2">
              <div className="flex-1">
                <Label htmlFor="totp-code">Code</Label>
                <Input
                  id="totp-code"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  placeholder="123456"
                  className="mt-1.5 font-mono"
                />
              </div>
              <Button type="submit" size="sm" disabled={code.length === 0 || confirmEnrollment.isPending}>
                {confirmEnrollment.isPending ? "Verifying…" : "Confirm"}
              </Button>
              <Button type="button" size="sm" variant="ghost" onClick={() => setPending(null)}>
                Cancel
              </Button>
            </div>
          </form>
        ) : (
          <>
            <Separator />
            <Button size="sm" variant="outline" onClick={beginEnrollment} disabled={start.isPending}>
              {start.isPending ? "Starting…" : "Enroll authenticator app"}
            </Button>
          </>
        )}
      </CardContent>
    </Card>
  );
}

function SessionsCard() {
  const logoutAll = useLogoutAll();

  return (
    <Card>
      <CardHeader>
        <CardTitle>Sessions</CardTitle>
        <CardDescription>
          Signing out everywhere revokes every refresh token and bumps your security stamp, so
          every other browser and device is signed out immediately rather than when its token
          happens to expire.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-sm text-muted-foreground">
          There is no per-session listing yet — the backend stores sessions but exposes no
          enumeration endpoint, so this console deliberately shows the one action it can actually
          perform rather than a list it would have to invent.
        </p>
        <Button
          size="sm"
          variant="destructive"
          onClick={() => logoutAll.mutate()}
          disabled={logoutAll.isPending}
        >
          {logoutAll.isPending ? "Signing out…" : "Sign out everywhere"}
        </Button>
      </CardContent>
    </Card>
  );
}
