"use client";

import { use as usePromise, useState } from "react";
import Link from "next/link";
import {
  Bot,
  Building,
  Gauge,
  MessageSquareReply,
  Palette,
  ShieldAlert,
  Sparkles,
  UserCog,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { Separator } from "@/components/ui/separator";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { PageHeader } from "@/components/shared/page-header";
import { ErrorState } from "@/components/shared/states";
import { Skeleton } from "@/components/ui/skeleton";
import { AVATAR_LABELS, AvatarGlyph, ChatbotPreview } from "@/components/shared/chatbot-preview";
import {
  useChatbotSettings,
  useTeams,
  useTenantPlan,
  useUpdateChatbotBehaviour,
  useUpdateChatbotSettings,
  useUpdateWidgetPresentation,
  useWidgetPresentation,
} from "@/features/chatbot/hooks";
import { useChatWidgets } from "@/features/ai-resources/hooks";
import { useHasTenantPermission } from "@/features/rbac/hooks";
import { isApiError } from "@/lib/api-client";
import { AVATAR_KEYS, type AvatarKey, type Personality, type ResponseLength } from "@/lib/types";

const MANAGE = "tenant.documents.upload";

const PERSONALITIES: { value: Personality; label: string; hint: string }[] = [
  { value: "neutral", label: "Neutral", hint: "Plain and even. States facts directly." },
  { value: "friendly", label: "Friendly", hint: "Warm and conversational." },
  { value: "reassuring", label: "Reassuring", hint: "Calm and supportive — for anxious parents." },
  { value: "professional", label: "Professional", hint: "Formal, as written correspondence." },
];

const LENGTHS: { value: ResponseLength; label: string; hint: string }[] = [
  { value: "concise", label: "Concise", hint: "Two or three sentences." },
  { value: "balanced", label: "Balanced", hint: "A short paragraph with key detail." },
  { value: "detailed", label: "Detailed", hint: "Thorough, with bullets where useful." },
];

export default function ChatbotPage({ params }: { params: Promise<{ tenantId: string }> }) {
  const { tenantId } = usePromise(params);
  // `undefined` while the permission query is in flight. Controls stay
  // disabled during that window (fail closed in the UI; the server is the real
  // gate), but the banner waits for a *definite* `false` -- telling a user who
  // can edit that they cannot is worse than a moment of disabled buttons.
  const canManage = useHasTenantPermission(tenantId, MANAGE);
  const knownReadOnly = canManage === false;

  const settings = useChatbotSettings(tenantId);
  const plan = useTenantPlan(tenantId);
  const teams = useTeams(tenantId, true);
  const widgets = useChatWidgets(tenantId);

  // Identity is stored per widget, so the tab has to say *which* one. It
  // previously always edited `chat_widgets[0]`: a tenant running two embeds
  // configured one and watched the other keep the defaults, which is exactly
  // the "the real widget doesn't look like the preview" complaint.
  const widgetList = widgets.data?.chat_widgets ?? [];
  const [pickedWidgetId, setPickedWidgetId] = useState<string | null>(null);
  const selectedWidgetId = pickedWidgetId ?? widgetList[0]?.id ?? null;
  const presentation = useWidgetPresentation(tenantId, selectedWidgetId);

  const saveSettings = useUpdateChatbotSettings(tenantId);
  const savePresentation = useUpdateWidgetPresentation(tenantId);
  const saveBehaviour = useUpdateChatbotBehaviour(tenantId);

  // --- form state, hydrated once each query lands ---------------------------
  const [enabled, setEnabled] = useState(true);
  const [companyName, setCompanyName] = useState("");
  const [companyDescription, setCompanyDescription] = useState("");
  const [industry, setIndustry] = useState("");
  const [allowHandoff, setAllowHandoff] = useState(true);
  const [aiSummary, setAiSummary] = useState(false);
  const [aiForUnassigned, setAiForUnassigned] = useState(true);
  const [dailyLimit, setDailyLimit] = useState("");
  const [shareLocation, setShareLocation] = useState(true);

  const [chatbotName, setChatbotName] = useState("");
  const [chatbotTitle, setChatbotTitle] = useState("");
  const [avatarKey, setAvatarKey] = useState<AvatarKey>("nursery-default");
  const [greeting, setGreeting] = useState("");
  const [quickReplies, setQuickReplies] = useState(true);

  const [role, setRole] = useState("");
  const [avoid, setAvoid] = useState("");
  const [personality, setPersonality] = useState<Personality>("neutral");
  const [responseLength, setResponseLength] = useState<ResponseLength>("balanced");

  // Hydration happens *during render*, guarded by a stamp, rather than in an
  // effect. React documents this as the way to adjust state when incoming data
  // changes; doing it in an effect renders once with stale values and then
  // again, which is both a visible flash and what `set-state-in-effect` warns
  // about. The stamp is the row's own `updated_at`, so a save re-hydrates the
  // form with what the server actually stored and nothing else re-runs it.
  const [settingsStamp, setSettingsStamp] = useState<string | null>(null);
  if (settings.data && settingsStamp !== settings.data.updated_at) {
    const s = settings.data;
    setSettingsStamp(s.updated_at);
    setEnabled(s.ai_chatbot_enabled);
    setCompanyName(s.company_name ?? "");
    setCompanyDescription(s.company_description);
    setIndustry(s.industry);
    setAllowHandoff(s.allow_human_handoff);
    setAiSummary(s.add_ai_summary_as_internal_comment);
    setAiForUnassigned(s.allow_ai_for_unassigned_conversations);
    setDailyLimit(s.daily_message_limit?.toString() ?? "");
    setShareLocation(s.share_visitor_location);
    // The brief arrives with the settings row now, not from an assistant.
    // Empty means "never written": show the shipped default so Save stores
    // something coherent rather than an empty string that silently means
    // "use the default".
    setRole(s.role_instructions || s.default_role);
    setAvoid(s.avoid_instructions || s.default_avoid);
    setPersonality(s.personality);
    setResponseLength(s.response_length);
  }

  const [presentationStamp, setPresentationStamp] = useState<string | null>(null);
  if (presentation.data && presentationStamp !== presentation.data.widget_id) {
    const p = presentation.data;
    setPresentationStamp(p.widget_id);
    setChatbotName(p.chatbot_name);
    setChatbotTitle(p.chatbot_title);
    setAvatarKey(p.avatar_key);
    setGreeting(p.greeting ?? "");
    setQuickReplies(p.show_quick_reply_suggestions);
  }

  const ceiling = plan.data?.max_messages_per_day ?? null;

  /** The master switch persists on toggle, unlike every other field here.
   *
   *  It sits in its own card above the tabs, its copy describes an immediate
   *  effect ("When off, visitors go straight to a person"), and the preview
   *  beside it redraws to match — so an admin who flips it has three separate
   *  confirmations that it applied. It did not: `onCheckedChange` only set
   *  local state, and the value reached the server solely via a Save button
   *  inside a tab the admin had no reason to open. Found live: the switch read
   *  "Off" while the widget kept answering and kept billing.
   *
   *  This is also the control someone reaches for in an incident, which is
   *  precisely when "it looked like it worked" is most expensive. */
  async function handleToggleAi(next: boolean) {
    setEnabled(next);
    try {
      await handleSaveSettings(next);
    } catch {
      // Put the switch back rather than leave it showing a state the server
      // does not have -- the silent disagreement is the whole bug above.
      setEnabled(!next);
    }
  }

  async function handleSaveSettings(enabledOverride?: boolean) {
    try {
      await saveSettings.mutateAsync({
        // React state updates are not synchronous, so a toggle that saved
        // `enabled` would send the value it had *before* the click.
        ai_chatbot_enabled: enabledOverride ?? enabled,
        company_name: companyName.trim() || null,
        company_description: companyDescription,
        industry,
        allow_human_handoff: allowHandoff,
        add_ai_summary_as_internal_comment: aiSummary,
        allow_ai_for_unassigned_conversations: aiForUnassigned,
        daily_message_limit: dailyLimit.trim() === "" ? null : Number(dailyLimit),
        share_visitor_location: shareLocation,
      });
      toast.success("Chatbot settings saved.");
    } catch (err) {
      toast.error(isApiError(err) ? err.message : "Couldn't save these settings.");
      // Rethrown so `handleToggleAi` can put the switch back. The toast is
      // still the user-facing report; every other caller is a Save button
      // whose own `void` call ignores this, exactly as before.
      throw err;
    }
  }

  async function handleSavePresentation() {
    if (!selectedWidgetId) return;
    try {
      await savePresentation.mutateAsync({
        widgetId: selectedWidgetId,
        chatbotName,
        chatbotTitle,
        avatarKey,
        greeting: greeting.trim() || null,
        showQuickReplySuggestions: quickReplies,
      });
      toast.success("Identity saved.");
    } catch (err) {
      toast.error(isApiError(err) ? err.message : "Couldn't save the identity.");
    }
  }

  async function handleSaveBehaviour() {
    try {
      await saveBehaviour.mutateAsync({
        roleInstructions: role,
        avoidInstructions: avoid,
        personality,
        responseLength,
      });
      toast.success("Behaviour saved.");
    } catch (err) {
      toast.error(isApiError(err) ? err.message : "Couldn't save the behaviour.");
    }
  }

  if (settings.error) return <ErrorState error={settings.error} resource="chatbot settings" />;

  return (
    <div>
      <PageHeader
        title="AI Chatbot"
        description="One place to configure who your chatbot is, how it behaves, and when it should fetch a colleague."
      />

      {knownReadOnly && (
        <Alert className="mb-4">
          <ShieldAlert className="size-4" />
          <AlertTitle>Read only</AlertTitle>
          <AlertDescription>
            You can see this configuration but not change it. Editing needs the
            &ldquo;{MANAGE}&rdquo; permission — the same authority as changing what your
            chatbot knows.
          </AlertDescription>
        </Alert>
      )}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="min-w-0">
          <Card className="mb-4">
            <CardContent className="flex items-center justify-between gap-4 py-4">
              <div>
                <div className="flex items-center gap-2 font-medium">
                  <Sparkles className="size-4 text-primary" /> AI Chatbot
                  <Badge variant={enabled ? "default" : "outline"}>
                    {enabled ? "On" : "Off"}
                  </Badge>
                </div>
                <p className="mt-1 text-sm text-muted-foreground">
                  When off, visitors go straight to a person. No retrieval, no model call, and
                  neither quota is consumed — conversations still work.
                </p>
              </div>
              <Switch
                checked={enabled}
                disabled={!canManage || saveSettings.isPending}
                onCheckedChange={(v) => void handleToggleAi(v)}
                aria-label="AI chatbot enabled"
              />
            </CardContent>
          </Card>

          <Tabs defaultValue="identity">
            <TabsList className="mb-4 flex-wrap">
              <TabsTrigger value="identity">
                <Palette className="mr-1.5 size-3.5" /> Identity
              </TabsTrigger>
              <TabsTrigger value="behaviour">
                <Bot className="mr-1.5 size-3.5" /> Behaviour
              </TabsTrigger>
              <TabsTrigger value="tone">
                <UserCog className="mr-1.5 size-3.5" /> Tone &amp; style
              </TabsTrigger>
              <TabsTrigger value="company">
                <Building className="mr-1.5 size-3.5" /> Company
              </TabsTrigger>
              <TabsTrigger value="reply">
                <MessageSquareReply className="mr-1.5 size-3.5" /> Reply experience
              </TabsTrigger>
              <TabsTrigger value="handoff">
                <Gauge className="mr-1.5 size-3.5" /> Handoff &amp; limits
              </TabsTrigger>
            </TabsList>

            {/* ---- Identity ---- */}
            <TabsContent value="identity">
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Identity</CardTitle>
                  <CardDescription>
                    How your chatbot introduces itself on your website. Set per embed, so a
                    parent portal and a public site can differ.
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  {presentation.isLoading && <Skeleton className="h-24 w-full" />}
                  {!selectedWidgetId && widgets.isLoading && (
                    <Skeleton className="h-24 w-full" />
                  )}
                  {!selectedWidgetId && !widgets.isLoading && (
                    <div className="space-y-3">
                      <p className="text-sm text-muted-foreground">
                        You haven&rsquo;t created your chatbot yet. Go to{" "}
                        <strong>Knowledge bases</strong>, choose the knowledge base it
                        should answer from, and select <strong>Embed</strong> — that
                        creates it and gives you the code for your website. Come back
                        here afterwards to set its name, avatar and greeting.
                      </p>
                      <Button render={<Link href={`/tenant/${tenantId}/knowledge-bases`} />}>
                        Go to Knowledge bases
                      </Button>
                    </div>
                  )}
                  {selectedWidgetId && (
                    <>
                      {widgetList.length > 1 && (
                        <div>
                          <Label htmlFor="widget-picker">Widget</Label>
                          <Select
                            value={selectedWidgetId}
                            onValueChange={setPickedWidgetId}
                            disabled={!canManage}
                          >
                            <SelectTrigger id="widget-picker" className="mt-1.5">
                              {/* Render-prop form, not a bare <SelectValue />.
                                  Base UI resolves the displayed label by
                                  reading the currently *mounted* items, and
                                  this list arrives from an async query — so a
                                  pre-set value renders as the raw UUID until
                                  something forces it to re-resolve. Same trap
                                  documented on ModelConfigurationField. */}
                              <SelectValue>
                                {(v) => {
                                  const w = widgetList.find((x) => x.id === v);
                                  return w ? `${w.name} — ${w.allowed_origins.join(", ")}` : v;
                                }}
                              </SelectValue>
                            </SelectTrigger>
                            <SelectContent>
                              {widgetList.map((w) => (
                                <SelectItem key={w.id} value={w.id}>
                                  {w.name} — {w.allowed_origins.join(", ")}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                          <p className="mt-1.5 text-xs text-muted-foreground">
                            The settings below apply to this embed only. The preview on the
                            right shows what a visitor to those origins will see.
                          </p>
                        </div>
                      )}
                      <div className="grid gap-4 sm:grid-cols-2">
                        <div>
                          <Label htmlFor="chatbot-name">Chatbot name</Label>
                          <Input
                            id="chatbot-name"
                            className="mt-1.5"
                            value={chatbotName}
                            disabled={!canManage}
                            onChange={(e) => setChatbotName(e.target.value)}
                          />
                        </div>
                        <div>
                          <Label htmlFor="chatbot-title">Chatbot title</Label>
                          <Input
                            id="chatbot-title"
                            className="mt-1.5"
                            value={chatbotTitle}
                            disabled={!canManage}
                            onChange={(e) => setChatbotTitle(e.target.value)}
                          />
                        </div>
                      </div>

                      <div>
                        <Label>Avatar</Label>
                        <p className="mb-2 text-xs text-muted-foreground">
                          Shipped with the platform. Not a URL — that would let your chatbot
                          load an image from a third-party origin on your visitors&rsquo;
                          browsers.
                        </p>
                        <div className="flex flex-wrap gap-2">
                          {AVATAR_KEYS.map((key) => (
                            <button
                              key={key}
                              type="button"
                              disabled={!canManage}
                              onClick={() => setAvatarKey(key)}
                              className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-sm transition ${
                                avatarKey === key
                                  ? "border-primary bg-primary/5 text-primary"
                                  : "hover:bg-accent"
                              } disabled:opacity-50`}
                            >
                              <AvatarGlyph avatarKey={key} className="size-4" />
                              {AVATAR_LABELS[key]}
                            </button>
                          ))}
                        </div>
                      </div>

                      <div>
                        <Label htmlFor="greeting">Greeting</Label>
                        <Input
                          id="greeting"
                          className="mt-1.5"
                          placeholder="Hello! How can I help?"
                          value={greeting}
                          disabled={!canManage}
                          onChange={(e) => setGreeting(e.target.value)}
                        />
                      </div>

                      <Button
                        onClick={handleSavePresentation}
                        disabled={!canManage || savePresentation.isPending}
                      >
                        {savePresentation.isPending ? "Saving…" : "Save identity"}
                      </Button>
                    </>
                  )}
                </CardContent>
              </Card>
            </TabsContent>

            {/* ---- Behaviour ---- */}
            <TabsContent value="behaviour">
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Role &amp; boundaries</CardTitle>
                  <CardDescription>
                    What your chatbot is for, and what it must not do. Both are added to the
                    platform&rsquo;s own rules — they never replace them, so grounding and
                    citation stay enforced.
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  <CharField
                    id="role"
                    label="Role"
                    hint="What your chatbot helps with."
                    value={role}
                    rows={6}
                    disabled={!canManage}
                    onChange={setRole}
                  />
                  <CharField
                    id="avoid"
                    label="Avoid"
                    hint="Topics and actions to refuse or escalate. These only ever add restrictions."
                    value={avoid}
                    rows={6}
                    disabled={!canManage}
                    onChange={setAvoid}
                  />
                  <Button
                    onClick={handleSaveBehaviour}
                    disabled={!canManage || saveBehaviour.isPending}
                  >
                    {saveBehaviour.isPending ? "Saving…" : "Save behaviour"}
                  </Button>
                </CardContent>
              </Card>
            </TabsContent>

            {/* ---- Tone & style ---- */}
            <TabsContent value="tone">
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Tone &amp; style</CardTitle>
                  <CardDescription>
                    Fixed options rather than free text: each maps to a vetted instruction, so
                    a tone setting can never become a way to rewrite your chatbot&rsquo;s
                    rules.
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-5">
                  <ChoiceGroup
                    label="Personality"
                    options={PERSONALITIES}
                    value={personality}
                    disabled={!canManage}
                    onChange={(v) => setPersonality(v as Personality)}
                  />
                  <Separator />
                  <ChoiceGroup
                    label="Response length"
                    options={LENGTHS}
                    value={responseLength}
                    disabled={!canManage}
                    onChange={(v) => setResponseLength(v as ResponseLength)}
                  />
                  <Button
                    onClick={handleSaveBehaviour}
                    disabled={!canManage || saveBehaviour.isPending}
                  >
                    {saveBehaviour.isPending ? "Saving…" : "Save tone & style"}
                  </Button>
                </CardContent>
              </Card>
            </TabsContent>

            {/* ---- Company context ---- */}
            <TabsContent value="company">
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Company context</CardTitle>
                  <CardDescription>
                    Background your chatbot can draw on. This is what it calls your
                    organisation — <strong>it does not rename your account</strong>.
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div>
                    <Label htmlFor="company-name">Company name</Label>
                    <Input
                      id="company-name"
                      className="mt-1.5"
                      placeholder="Defaults to your organisation name"
                      value={companyName}
                      disabled={!canManage}
                      onChange={(e) => setCompanyName(e.target.value)}
                    />
                  </div>
                  <div>
                    <Label htmlFor="industry">Industry</Label>
                    <Input
                      id="industry"
                      className="mt-1.5"
                      maxLength={100}
                      value={industry}
                      disabled={!canManage}
                      onChange={(e) => setIndustry(e.target.value)}
                    />
                  </div>
                  <CharField
                    id="company-description"
                    label="Company description"
                    hint="A short description of your setting, used as background — not as instructions."
                    value={companyDescription}
                    max={2000}
                    rows={6}
                    disabled={!canManage}
                    onChange={setCompanyDescription}
                  />
                  <Button
                    onClick={() => void handleSaveSettings()}
                    disabled={!canManage || saveSettings.isPending}
                  >
                    {saveSettings.isPending ? "Saving…" : "Save company context"}
                  </Button>
                </CardContent>
              </Card>
            </TabsContent>

            {/* ---- Reply experience ---- */}
            <TabsContent value="reply">
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Reply experience</CardTitle>
                  <CardDescription>What the visitor gets alongside the answer.</CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  <ToggleRow
                    label="Quick reply suggestions"
                    hint="Offer contextual buttons under the answer. Sent as structured data, not HTML inside the model's output."
                    checked={quickReplies}
                    disabled={!canManage || !selectedWidgetId}
                    onChange={setQuickReplies}
                  />
                  <ToggleRow
                    label="Use visitor location"
                    hint="Let your chatbot use approximate country/city when it's relevant. Precise browser location is never collected silently."
                    checked={shareLocation}
                    disabled={!canManage}
                    onChange={setShareLocation}
                  />
                  <div className="flex gap-2">
                    <Button
                      onClick={handleSavePresentation}
                      disabled={!canManage || !selectedWidgetId || savePresentation.isPending}
                    >
                      Save quick replies
                    </Button>
                    <Button
                      variant="outline"
                      onClick={() => void handleSaveSettings()}
                      disabled={!canManage || saveSettings.isPending}
                    >
                      Save location setting
                    </Button>
                  </div>
                </CardContent>
              </Card>
            </TabsContent>

            {/* ---- Handoff & limits ---- */}
            <TabsContent value="handoff">
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Human handoff</CardTitle>
                  <CardDescription>
                    When your chatbot should fetch a colleague, and what happens then.
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  <ToggleRow
                    label="Allow transfer to a colleague"
                    hint="Your chatbot offers a transfer when asked, or when a question needs staff judgement."
                    checked={allowHandoff}
                    disabled={!canManage}
                    onChange={setAllowHandoff}
                  />
                  <ToggleRow
                    label="AI summary as an internal comment"
                    hint="On transfer, write a staff-only summary of the conversation. Never shown to the visitor. If it fails, the transfer still happens."
                    checked={aiSummary}
                    disabled={!canManage}
                    onChange={setAiSummary}
                  />
                  <ToggleRow
                    label="AI answers unassigned conversations"
                    hint="Let your chatbot respond first while nobody has claimed the conversation. After a human takes over, it stays quiet until deliberately handed back."
                    checked={aiForUnassigned}
                    disabled={!canManage}
                    onChange={setAiForUnassigned}
                  />

                  <div className="rounded-lg border p-3">
                    <div className="text-sm font-medium">Teams available for transfer</div>
                    {(teams.data?.teams.length ?? 0) === 0 ? (
                      <p className="mt-1 text-sm text-muted-foreground">
                        No active teams. Visitors will be told a transfer isn&rsquo;t available
                        rather than offered a menu that goes nowhere. Add teams on the Inbox
                        screen.
                      </p>
                    ) : (
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {teams.data?.teams.map((t) => (
                          <Badge key={t.id} variant="secondary">
                            {t.name}
                          </Badge>
                        ))}
                      </div>
                    )}
                  </div>

                  <Separator />

                  <div>
                    <Label htmlFor="daily-limit">Daily AI message limit</Label>
                    <p className="mb-1.5 text-xs text-muted-foreground">
                      Your own cap. Leave blank to use your plan&rsquo;s maximum
                      {ceiling !== null && <> of {ceiling.toLocaleString()}</>}. A value above
                      the plan maximum is refused.
                    </p>
                    <Input
                      id="daily-limit"
                      inputMode="numeric"
                      className="max-w-[200px]"
                      placeholder={ceiling !== null ? ceiling.toString() : "Unlimited"}
                      value={dailyLimit}
                      disabled={!canManage}
                      onChange={(e) => setDailyLimit(e.target.value.replace(/[^\d]/g, ""))}
                    />
                    {settings.data?.effective_daily_message_limit != null && (
                      <p className="mt-1.5 text-xs text-muted-foreground">
                        Currently enforced:{" "}
                        <strong>
                          {settings.data.effective_daily_message_limit.toLocaleString()}
                        </strong>{" "}
                        messages/day
                        {plan.data?.messages_used_today != null && (
                          <> · {plan.data.messages_used_today.toLocaleString()} used today</>
                        )}
                      </p>
                    )}
                  </div>

                  <Button
                    onClick={() => void handleSaveSettings()}
                    disabled={!canManage || saveSettings.isPending}
                  >
                    {saveSettings.isPending ? "Saving…" : "Save handoff & limits"}
                  </Button>
                </CardContent>
              </Card>
            </TabsContent>
          </Tabs>
        </div>

        {/* ---- Preview ---- */}
        <div className="lg:sticky lg:top-4 lg:self-start">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-sm font-semibold">Preview</h2>
            <span className="text-xs text-muted-foreground">Illustrative, not generated</span>
          </div>
          <ChatbotPreview
            chatbotName={chatbotName}
            chatbotTitle={chatbotTitle}
            avatarKey={avatarKey}
            greeting={greeting || null}
            showQuickReplies={quickReplies}
            personality={personality}
            responseLength={responseLength}
            allowHandoff={allowHandoff}
            teamNames={(teams.data?.teams ?? []).map((t) => t.name)}
            enabled={enabled}
          />
          <p className="mt-2 text-xs text-muted-foreground">
            The sample answer illustrates the selected tone and length. It is not produced by
            the model — rendering a preview through the model would spend your tokens on every
            toggle.
          </p>
        </div>
      </div>
    </div>
  );
}

function CharField({
  id,
  label,
  hint,
  value,
  max,
  rows,
  disabled,
  onChange,
}: {
  id: string;
  label: string;
  hint: string;
  value: string;
  /** Omitted where the field is deliberately unbounded -- the count is still
   *  shown, because knowing how long a brief has grown is useful, but there is
   *  no ceiling to measure it against. */
  max?: number;
  rows: number;
  disabled: boolean;
  onChange: (v: string) => void;
}) {
  const over = max !== undefined && value.length > max;
  return (
    <div>
      <div className="flex items-baseline justify-between">
        <Label htmlFor={id}>{label}</Label>
        <span className={`text-xs ${over ? "font-medium text-destructive" : "text-muted-foreground"}`}>
          {max === undefined
            ? value.length.toLocaleString()
            : `${value.length.toLocaleString()} / ${max.toLocaleString()}`}
        </span>
      </div>
      <p className="mb-1.5 text-xs text-muted-foreground">{hint}</p>
      <Textarea
        id={id}
        rows={rows}
        value={value}
        disabled={disabled}
        aria-invalid={over}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}

function ChoiceGroup({
  label,
  options,
  value,
  disabled,
  onChange,
}: {
  label: string;
  options: { value: string; label: string; hint: string }[];
  value: string;
  disabled: boolean;
  onChange: (v: string) => void;
}) {
  return (
    <div>
      <Label className="mb-2 block">{label}</Label>
      <div className="grid gap-2 sm:grid-cols-2">
        {options.map((o) => (
          <button
            key={o.value}
            type="button"
            disabled={disabled}
            onClick={() => onChange(o.value)}
            className={`rounded-lg border p-3 text-left transition ${
              value === o.value ? "border-primary bg-primary/5" : "hover:bg-accent"
            } disabled:opacity-50`}
          >
            <div className="text-sm font-medium">{o.label}</div>
            <div className="mt-0.5 text-xs text-muted-foreground">{o.hint}</div>
          </button>
        ))}
      </div>
    </div>
  );
}

function ToggleRow({
  label,
  hint,
  checked,
  disabled,
  onChange,
}: {
  label: string;
  hint: string;
  checked: boolean;
  disabled: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <div className="flex items-start justify-between gap-4 rounded-lg border p-3">
      <div>
        <div className="text-sm font-medium">{label}</div>
        <div className="mt-0.5 text-xs text-muted-foreground">{hint}</div>
      </div>
      <Switch checked={checked} disabled={disabled} onCheckedChange={onChange} />
    </div>
  );
}
