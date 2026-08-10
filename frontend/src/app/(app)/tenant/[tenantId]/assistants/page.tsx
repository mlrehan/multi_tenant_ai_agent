"use client";

import { use as usePromise, useState } from "react";
import { Archive, Pencil, Plus, Sparkles } from "lucide-react";
import { toast } from "sonner";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
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
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
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
import { FieldError } from "@/components/shared/field-error";
import {
  useArchiveAssistant,
  useAssistants,
  useCreateAssistant,
  useModelConfigurations,
  usePublishAssistant,
  useUpdateAssistant,
} from "@/features/ai-resources/hooks";
import { isApiError } from "@/lib/api-client";
import type { Assistant, ModelConfiguration, Visibility } from "@/lib/types";

const VISIBILITY_HELP: Record<Visibility, string> = {
  tenant: "Everyone in the tenant",
  department: "One department",
  team: "One team",
  restricted: "Only people you grant access to",
};

// Department/team-scoped visibility is unreachable in practice: nothing in
// this console (or the backend) lets an administrator assign a member to a
// department or team -- the columns exist on tenant_memberships, but there's
// no Department/Team table to validate a value against and no picker to set
// one. Selecting these here would create an assistant nobody can ever see.
const UNASSIGNABLE_VISIBILITY: ReadonlySet<Visibility> = new Set(["department", "team"]);

const createSchema = z.object({
  name: z.string().min(1, "Enter a name.").max(200),
  description: z.string().optional(),
  system_prompt: z.string().optional(),
});
type CreateForm = z.infer<typeof createSchema>;

/** `useModelConfigurations` returns platform defaults too, but
 * `fk_ai_assistants_model_configuration` requires the config's `tenant_id`
 * to match the assistant's (see CLAUDE.md's "known backend defect" note) --
 * a platform default (`tenant_id IS NULL`) is visible here but not usable
 * until that FK is fixed. Shown disabled rather than hidden, so the gap is
 * visible instead of silently absent. */
function ModelConfigurationField({
  tenantId,
  value,
  onChange,
  error,
}: {
  tenantId: string;
  value: string;
  onChange: (id: string) => void;
  error?: string;
}) {
  const { data, isLoading } = useModelConfigurations(tenantId);
  // Every row the server returns is one this tenant may assign -- availability
  // is decided by the platform's grant, server-side. Nothing to filter, and
  // nothing to render disabled.
  const available = data?.model_configurations ?? [];
  const byId = new Map(available.map((c) => [c.id, c]));

  return (
    <div>
      <Label htmlFor="model-config">Model configuration</Label>
      <Select value={value} onValueChange={(v) => onChange(v ?? "")}>
        <SelectTrigger id="model-config" className="mt-1.5 w-full">
          {/* A plain <SelectValue> resolves its label from the mounted
              <SelectItem>s, but those only exist once this query has
              loaded -- pre-selecting a value (editing an existing
              assistant) would show the raw id until the list happened to
              re-render. The render-prop form looks the label up directly. */}
          <SelectValue placeholder={isLoading ? "Loading…" : "Select a model configuration"}>
            {(v: string) => (v ? (byId.get(v)?.model_name ?? v) : null)}
          </SelectValue>
        </SelectTrigger>
        <SelectContent>
          {available.map((c: ModelConfiguration) => (
            <SelectItem key={c.id} value={c.id}>
              {c.model_name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <FieldError message={error} />
      {!isLoading && available.length === 0 && (
        <p className="mt-1 text-xs text-muted-foreground">
          No model configuration is available for this tenant. Contact your platform
          administrator.
        </p>
      )}
    </div>
  );
}

export default function AssistantsPage({ params }: { params: Promise<{ tenantId: string }> }) {
  const { tenantId } = usePromise(params);
  const { data, isLoading, error } = useAssistants(tenantId);
  const [open, setOpen] = useState(false);

  const assistants = data?.assistants;

  return (
    <div>
      <PageHeader
        title="Assistants"
        description="AI assistants in this tenant. You only see the ones your visibility allows."
        actions={
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger render={<Button size="sm" />}>
              <Plus />
              New assistant
            </DialogTrigger>
            <CreateAssistantDialog tenantId={tenantId} onDone={() => setOpen(false)} />
          </Dialog>
        }
      />

      {isLoading && <TableSkeleton rows={4} columns={5} />}
      {error && <ErrorState error={error} resource="assistants" />}

      {assistants && assistants.length === 0 && (
        <EmptyState
          icon={Sparkles}
          title="No assistants yet"
          description="Create an assistant to give your team a purpose-built AI helper."
          action={
            <Button size="sm" onClick={() => setOpen(true)}>
              <Plus />
              New assistant
            </Button>
          }
        />
      )}

      {assistants && assistants.length > 0 && (
        <Card>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Visibility</TableHead>
                  <TableHead>ID</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {assistants.map((assistant) => (
                  <AssistantRow key={assistant.id} tenantId={tenantId} assistant={assistant} />
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function AssistantRow({ tenantId, assistant }: { tenantId: string; assistant: Assistant }) {
  const publish = usePublishAssistant(tenantId);
  const archive = useArchiveAssistant(tenantId);
  const [editOpen, setEditOpen] = useState(false);

  async function handlePublish() {
    try {
      await publish.mutateAsync(assistant.id);
      toast.success(`Published ${assistant.name}`);
    } catch (err) {
      toast.error(isApiError(err) ? err.message : "Couldn't publish the assistant.");
    }
  }

  async function handleArchive() {
    try {
      await archive.mutateAsync(assistant.id);
      toast.success(`Archived ${assistant.name}`);
    } catch (err) {
      toast.error(isApiError(err) ? err.message : "Couldn't archive the assistant.");
    }
  }

  return (
    <TableRow>
      <TableCell>
        <div className="font-medium">{assistant.name}</div>
        {assistant.description && (
          <p className="mt-0.5 text-xs text-muted-foreground">{assistant.description}</p>
        )}
      </TableCell>
      <TableCell>
        <StatusBadge status={assistant.status} />
      </TableCell>
      <TableCell>
        <Badge variant="secondary" className="capitalize">
          {assistant.visibility}
        </Badge>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {VISIBILITY_HELP[assistant.visibility]}
        </p>
      </TableCell>
      <TableCell>
        <IdentityChip value={assistant.id} label="assistant" />
      </TableCell>
      <TableCell className="text-right">
        <div className="flex items-center justify-end gap-1.5">
          <Button
            size="xs"
            variant="outline"
            disabled={assistant.status !== "draft" || publish.isPending}
            onClick={handlePublish}
          >
            {assistant.status === "published" ? "Published" : "Publish"}
          </Button>
          <Dialog open={editOpen} onOpenChange={setEditOpen}>
            <DialogTrigger
              render={
                <Button
                  size="icon-xs"
                  variant="ghost"
                  disabled={assistant.status === "archived"}
                />
              }
            >
              <Pencil />
            </DialogTrigger>
            <EditAssistantDialog
              tenantId={tenantId}
              assistant={assistant}
              onDone={() => setEditOpen(false)}
            />
          </Dialog>
          <Button
            size="icon-xs"
            variant="ghost"
            disabled={assistant.status === "archived" || archive.isPending}
            onClick={handleArchive}
            title="Archive"
          >
            <Archive />
          </Button>
        </div>
      </TableCell>
    </TableRow>
  );
}

function CreateAssistantDialog({ tenantId, onDone }: { tenantId: string; onDone: () => void }) {
  const createAssistant = useCreateAssistant(tenantId);
  const [visibility, setVisibility] = useState<Visibility>("tenant");
  const [modelConfigurationId, setModelConfigurationId] = useState("");
  const [modelConfigError, setModelConfigError] = useState<string | undefined>();
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<CreateForm>({ resolver: zodResolver(createSchema) });

  async function onSubmit(values: CreateForm) {
    setModelConfigError(undefined);
    if (!modelConfigurationId) {
      setModelConfigError("Select a model configuration.");
      return;
    }
    try {
      await createAssistant.mutateAsync({
        name: values.name,
        description: values.description?.trim() || null,
        modelConfigurationId,
        visibility,
        departmentId: null,
        teamId: null,
        systemPrompt: values.system_prompt?.trim() || null,
      });
      toast.success(`Created ${values.name}`);
      reset();
      setModelConfigurationId("");
      onDone();
    } catch (err) {
      toast.error(isApiError(err) ? err.message : "Couldn't create the assistant.");
    }
  }

  return (
    <DialogContent className="max-w-lg">
      <form onSubmit={handleSubmit(onSubmit)}>
        <DialogHeader>
          <DialogTitle>New assistant</DialogTitle>
          <DialogDescription>
            Assistants start as drafts. Publish when you&apos;re ready for people to use it.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4 py-4">
          <div>
            <Label htmlFor="assistant-name">Name</Label>
            <Input id="assistant-name" className="mt-1.5" {...register("name")} />
            <FieldError message={errors.name?.message} />
          </div>
          <div>
            <Label htmlFor="assistant-description">Description</Label>
            <Input id="assistant-description" className="mt-1.5" {...register("description")} />
          </div>
          <ModelConfigurationField
            tenantId={tenantId}
            value={modelConfigurationId}
            onChange={setModelConfigurationId}
            error={modelConfigError}
          />
          <div>
            <Label htmlFor="assistant-visibility">Visibility</Label>
            <Select value={visibility} onValueChange={(v) => setVisibility(v as Visibility)}>
              <SelectTrigger id="assistant-visibility" className="mt-1.5 w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(Object.keys(VISIBILITY_HELP) as Visibility[]).map((v) => (
                  <SelectItem key={v} value={v} disabled={UNASSIGNABLE_VISIBILITY.has(v)}>
                    <span className="capitalize">{v}</span> — {VISIBILITY_HELP[v]}
                    {UNASSIGNABLE_VISIBILITY.has(v) ? " (unavailable)" : ""}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="mt-1 text-xs text-muted-foreground">
              Department and team visibility are disabled — this console has no way to assign a
              member to a department or team yet, so nobody could ever match.
            </p>
          </div>
          <div>
            <Label htmlFor="system-prompt">System prompt</Label>
            <Textarea id="system-prompt" rows={3} className="mt-1.5" {...register("system_prompt")} />
          </div>
        </div>
        <DialogFooter>
          <Button type="submit" size="sm" disabled={createAssistant.isPending}>
            {createAssistant.isPending ? "Creating…" : "Create assistant"}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}

function EditAssistantDialog({
  tenantId,
  assistant,
  onDone,
}: {
  tenantId: string;
  assistant: Assistant;
  onDone: () => void;
}) {
  const updateAssistant = useUpdateAssistant(tenantId);
  const [modelConfigurationId, setModelConfigurationId] = useState(
    assistant.model_configuration_id,
  );
  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<CreateForm>({
    resolver: zodResolver(createSchema),
    defaultValues: {
      name: assistant.name,
      description: assistant.description ?? "",
      system_prompt: assistant.system_prompt ?? "",
    },
  });

  async function onSubmit(values: CreateForm) {
    try {
      await updateAssistant.mutateAsync({
        assistantId: assistant.id,
        name: values.name,
        description: values.description?.trim() || null,
        modelConfigurationId,
        systemPrompt: values.system_prompt?.trim() || null,
      });
      toast.success("Assistant updated");
      onDone();
    } catch (err) {
      toast.error(isApiError(err) ? err.message : "Couldn't update the assistant.");
    }
  }

  return (
    <DialogContent className="max-w-lg">
      <form onSubmit={handleSubmit(onSubmit)}>
        <DialogHeader>
          <DialogTitle>Edit {assistant.name}</DialogTitle>
          <DialogDescription>
            Visibility is changed from the assistant&apos;s detail actions, not here.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4 py-4">
          <div>
            <Label htmlFor="edit-assistant-name">Name</Label>
            <Input id="edit-assistant-name" className="mt-1.5" {...register("name")} />
            <FieldError message={errors.name?.message} />
          </div>
          <div>
            <Label htmlFor="edit-assistant-description">Description</Label>
            <Input id="edit-assistant-description" className="mt-1.5" {...register("description")} />
          </div>
          <ModelConfigurationField
            tenantId={tenantId}
            value={modelConfigurationId}
            onChange={setModelConfigurationId}
          />
          <div>
            <Label htmlFor="edit-system-prompt">System prompt</Label>
            <Textarea id="edit-system-prompt" rows={3} className="mt-1.5" {...register("system_prompt")} />
          </div>
        </div>
        <DialogFooter>
          <Button type="submit" size="sm" disabled={updateAssistant.isPending}>
            {updateAssistant.isPending ? "Saving…" : "Save changes"}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
