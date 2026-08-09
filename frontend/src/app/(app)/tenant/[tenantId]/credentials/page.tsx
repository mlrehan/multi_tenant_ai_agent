"use client";

import { use as usePromise, useState } from "react";
import { KeyRound, Plus, ShieldCheck } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent } from "@/components/ui/card";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
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
import {
  useProviderCredentials,
  useRevokeProviderCredential,
  useRotateProviderCredential,
  useStoreProviderCredential,
} from "@/features/ai-resources/hooks";
import { isApiError } from "@/lib/api-client";
import type { ProviderCredential } from "@/lib/types";

export default function CredentialsPage({ params }: { params: Promise<{ tenantId: string }> }) {
  const { tenantId } = usePromise(params);
  const { data, isLoading, error } = useProviderCredentials(tenantId);
  const [open, setOpen] = useState(false);

  const credentials = data?.credentials;

  return (
    <div>
      <PageHeader
        title="Provider credentials"
        description="API keys for the AI providers this tenant uses."
        actions={
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger render={<Button size="sm" />}>
              <Plus />
              Add credential
            </DialogTrigger>
            <StoreCredentialDialog tenantId={tenantId} onDone={() => setOpen(false)} />
          </Dialog>
        }
      />

      <Alert className="mb-6">
        <ShieldCheck className="size-4" />
        <AlertTitle>Keys are encrypted and never shown again</AlertTitle>
        <AlertDescription>
          After you save a key, only the last four characters are ever displayed. To change one,
          rotate it — there&apos;s no way to read the stored value back, by design.
        </AlertDescription>
      </Alert>

      {isLoading && <TableSkeleton rows={3} columns={4} />}
      {error && <ErrorState error={error} resource="provider credentials" />}

      {credentials && credentials.length === 0 && (
        <EmptyState
          icon={KeyRound}
          title="No credentials stored"
          description="Add a provider API key so this tenant's assistants can call the model."
          action={
            <Button size="sm" onClick={() => setOpen(true)}>
              <Plus />
              Add credential
            </Button>
          }
        />
      )}

      {credentials && credentials.length > 0 && (
        <Card>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Provider</TableHead>
                  <TableHead>Key</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Last rotated</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {credentials.map((credential) => (
                  <CredentialRow key={credential.id} tenantId={tenantId} credential={credential} />
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function CredentialRow({
  tenantId,
  credential,
}: {
  tenantId: string;
  credential: ProviderCredential;
}) {
  const rotate = useRotateProviderCredential(tenantId);
  const revoke = useRevokeProviderCredential(tenantId);
  const [rotateOpen, setRotateOpen] = useState(false);
  const [newSecret, setNewSecret] = useState("");

  const isRevoked = Boolean(credential.revoked_at);

  async function handleRotate() {
    try {
      await rotate.mutateAsync({ credentialId: credential.id, newSecret });
      toast.success(`Rotated ${credential.provider} key`);
      setNewSecret("");
      setRotateOpen(false);
    } catch (err) {
      toast.error(isApiError(err) ? err.message : "Couldn't rotate the key.");
    }
  }

  async function handleRevoke() {
    try {
      await revoke.mutateAsync(credential.id);
      toast.success(`Revoked ${credential.provider} key`);
    } catch (err) {
      toast.error(isApiError(err) ? err.message : "Couldn't revoke the key.");
    }
  }

  return (
    <TableRow>
      <TableCell className="font-medium capitalize">{credential.provider}</TableCell>
      <TableCell>
        <code className="font-mono text-xs text-muted-foreground">••••{credential.key_hint}</code>
      </TableCell>
      <TableCell>
        <StatusBadge status={isRevoked ? "revoked" : "active"} />
      </TableCell>
      <TableCell className="text-sm text-muted-foreground tabular-nums">
        {credential.rotated_at ? new Date(credential.rotated_at).toLocaleDateString() : "Never"}
      </TableCell>
      <TableCell className="text-right">
        <div className="flex justify-end gap-1.5">
          <Dialog open={rotateOpen} onOpenChange={setRotateOpen}>
            <DialogTrigger render={<Button size="xs" variant="outline" disabled={isRevoked} />}>
              Rotate
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Rotate {credential.provider} key</DialogTitle>
                <DialogDescription>
                  The new key replaces the old one immediately.
                </DialogDescription>
              </DialogHeader>
              <div className="py-2">
                <Label htmlFor={`secret-${credential.id}`}>New key</Label>
                <Input
                  id={`secret-${credential.id}`}
                  type="password"
                  value={newSecret}
                  onChange={(e) => setNewSecret(e.target.value)}
                  autoComplete="off"
                  className="mt-1.5 font-mono text-xs"
                />
              </div>
              <DialogFooter>
                <Button
                  size="sm"
                  disabled={!newSecret.trim() || rotate.isPending}
                  onClick={handleRotate}
                >
                  {rotate.isPending ? "Rotating…" : "Rotate key"}
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
          <Button
            size="xs"
            variant="destructive"
            disabled={isRevoked || revoke.isPending}
            onClick={handleRevoke}
          >
            Revoke
          </Button>
        </div>
      </TableCell>
    </TableRow>
  );
}

function StoreCredentialDialog({ tenantId, onDone }: { tenantId: string; onDone: () => void }) {
  const store = useStoreProviderCredential(tenantId);
  const [provider, setProvider] = useState("");
  const [secret, setSecret] = useState("");

  async function handleSave() {
    try {
      await store.mutateAsync({ provider: provider.trim(), secret });
      toast.success(`Stored ${provider} key`);
      setProvider("");
      setSecret("");
      onDone();
    } catch (err) {
      toast.error(isApiError(err) ? err.message : "Couldn't store the credential.");
    }
  }

  return (
    <DialogContent>
      <DialogHeader>
        <DialogTitle>Add a provider credential</DialogTitle>
        <DialogDescription>
          The key is encrypted before it&apos;s stored. You won&apos;t be able to read it back.
        </DialogDescription>
      </DialogHeader>
      <div className="space-y-4 py-2">
        <div>
          <Label htmlFor="provider">Provider</Label>
          <Input
            id="provider"
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
            placeholder="anthropic"
            className="mt-1.5"
          />
        </div>
        <div>
          <Label htmlFor="secret">API key</Label>
          <Input
            id="secret"
            type="password"
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
            autoComplete="off"
            className="mt-1.5 font-mono text-xs"
          />
        </div>
      </div>
      <DialogFooter>
        <Button
          size="sm"
          disabled={!provider.trim() || !secret.trim() || store.isPending}
          onClick={handleSave}
        >
          {store.isPending ? "Saving…" : "Save credential"}
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}
