# Admin Console Guide — what every screen is for

A plain-language tour of the **IAM Control Center** (`frontend/`, served at
`http://localhost:3000` in development). Written for whoever has to *operate* this system, not for
whoever builds it — no code, no schema.

If you just want to get it running, start at [../DEPLOYMENT.md](../DEPLOYMENT.md) instead and come
back here when you're looking at the screens and wondering what they do.

---

## Table of contents

- [First, the one idea that explains everything](#first-the-one-idea-that-explains-everything)
- [Three words that sound alike but aren't](#three-words-that-sound-alike-but-arent)
- [Platform screens](#platform-screens)
  - [Overview](#platform--overview) · [Tenants](#platform--tenants) · [Users](#platform--users) ·
    [Roles](#platform--roles) · [Permissions](#platform--permissions) ·
    [Impersonation](#platform--impersonation)
- [Tenant screens](#tenant-screens)
  - [Dashboard](#tenant--dashboard) · [Members](#tenant--members) ·
    [Roles & permissions](#tenant--roles--permissions) · [Assistants](#tenant--assistants) ·
    [Knowledge bases](#tenant--knowledge-bases) · [Conversations](#tenant--conversations) ·
    [Provider credentials](#tenant--provider-credentials)
- [Account screens](#account--my-identity)
- [Common tasks](#common-tasks-start-to-finish)
- [Things that surprise people](#things-that-surprise-people)

---

## First, the one idea that explains everything

This product is an **AI assistant service sold to companies**. Each customer company gets its own
walled-off space. That space is called a **tenant**.

So there are two completely separate worlds, and almost every question about this console comes
down to *which world am I in?*

| | **Platform** | **Tenant** |
|---|---|---|
| Who lives here | You — the company running this service | Your customers |
| What it governs | The service itself | One customer's own space |
| Typical job | "Onboard Acme Corp", "suspend a non-paying customer", "help a customer debug" | "Add Priya to the marketing team", "let support see this assistant" |
| Sidebar section | **Platform** | **Tenant** |
| Example permission | `platform.tenants.create` | `tenant.assistants.publish` |

**These two worlds never mix.** A tenant administrator — even the most powerful one at Acme Corp —
cannot get platform permissions. They're stored in separate tables, and the system is built so that
a tenant role is structurally incapable of holding a platform permission. That isn't a rule someone
remembers to follow; it's enforced by the database.

You may hold roles in both worlds at once. If you do, the sidebar shows both sections.

---

## Three words that sound alike but aren't

Getting these three straight makes the rest of the console obvious.

**User** — a person's login. One email, one password, one set of second factors. A user exists
*above* all tenants; it isn't owned by any of them. Managed under **Platform → Users**.

**Member (membership)** — a user's presence *inside one tenant*. The same person can be a member of
three tenants, with a different job title and different roles in each. Managed under
**Tenant → Members**.

**Role** — a named bundle of permissions ("Tenant Owner", "Analyst"). You grant people roles, not
individual permissions.

The distinction that catches people out:

> **Suspending a user** stops that person signing in *anywhere*, in every tenant at once.
> **Revoking a membership** removes them from *one* tenant and leaves everything else untouched.
>
> The first is under Platform → Users. The second is under Tenant → Members. Reaching for the wrong
> one is the most common mistake on this console.

---

## Platform screens

Only visible if you hold platform permissions. A tenant-only user won't see this section at all.

### Platform → Overview

**What it is:** the operator's front page.

**What it's for:** a glance at the size and shape of the deployment — how many tenants (and how many
are suspended), how many user accounts, how many roles and permissions are defined — plus the
newest tenants and, importantly, **what you personally are allowed to do**.

That last panel is worth reading when something is missing. If a screen you expected isn't in the
sidebar, this page tells you which permissions you actually hold, which is usually the answer.

### Platform → Tenants

**What it is:** the list of customer organizations.

**What it's for:** onboarding a new customer, taking one offline, and putting it back.

**Creating one.** You give it a display name ("Acme Corporation"); the **URL slug** is derived
automatically (`acme-corporation`) and you can override it. The slug is the tenant's short
identifier in URLs and APIs, so it's lowercase letters, numbers and hyphens only. The form warns you
before submitting if the slug is taken.

You also pick an **owner** — searched by email, not typed as an ID. That person becomes the tenant's
first member and receives the Tenant Owner role automatically, so somebody can administer it from
minute one.

**Suspending one.** Requires a reason, which is recorded. Members immediately lose access. It is
fully reversible — a suspended tenant shows a **Reactivate** button.

> Suspend is the lever for non-payment or a policy breach. It's deliberately not deletion: nothing
> is destroyed, and the customer's data is intact when you reactivate.

**Renaming one.** The display name can be changed at any time — useful after a customer's own
rebrand. The **URL slug is permanent**: it's baked into links and API references from the moment the
tenant is created, so changing it later would silently break anything built against it. If the slug
itself is wrong, that's a case for support, not a form field.

### Platform → Users

**What it is:** every login account across the whole service, searchable by email.

**What it's for:** the full lifecycle of a person's access to the product.

| Action | What actually happens |
|---|---|
| **New user** | Creates an active account with an initial password you set and communicate out of band. No email is sent (see [Things that surprise people](#things-that-surprise-people)). |
| **Change email** | Changes what they sign in with. Signs them out everywhere and clears their verified status — the new address hasn't been proven to be theirs. |
| **Suspend** | They can't sign in anywhere, and every existing session dies *immediately* — not when their token expires. |
| **Reactivate** | Undoes a suspension. |
| **Delete** | A **soft** delete: they vanish from this list and can never sign in again, but the record is kept because the audit history refers to it. An IAM system that can erase who did what has defeated its own record-keeping. |

**Clicking a row** opens a detail panel — the single most useful screen when someone asks *"what can
this person actually do?"* It shows their platform roles, their fully-resolved platform permissions,
and every tenant they belong to. You can grant and revoke platform roles from right here.

You cannot suspend or delete **your own** account. Recovering from that needs direct database
access, so the console refuses rather than letting you lock yourself out.

### Platform → Roles

**What it is:** the platform-scope roles, the grant/revoke tool, and role definition itself.

**What it's for:** deciding who on *your* team can do what — who can onboard customers, who can
suspend them, who can use support impersonation.

**The rule that governs every grant:** you can only give away access you already hold, and never a
role that outranks you. If you try, you get a refusal naming exactly which permissions you're
missing. This is what stops an administrator quietly promoting themselves, and it applies to
everyone — there is no exception, which is also why the very first administrator has to be created
by a script rather than through this screen.

**Grant / revoke uses the same searchable-by-email picker as everywhere else** — type an email
instead of pasting a user ID.

**Creating a role.** "New role" defines a custom platform role: a code, a name, a rank, and the
permissions it carries. The same self-escalation guard applies at definition time, not just when
you later assign it — you can't write yourself a role containing more than you hold and then grant
it to yourself. Built-in roles (marked **System**) can't be edited or have their permissions
changed; only custom roles can be, from the **Edit** action on each row.

### Platform → Permissions

**What it is:** the catalog of every platform permission, and which role grants which.

**What it's for:** understanding and auditing. Nothing here is edited — it's the reference you
consult when deciding what a role *should* contain.

Permissions are grouped by resource and tagged **low / medium / high / critical**. That risk level
isn't decoration: an impersonated support session has every `high` and `critical` permission
stripped from it automatically.

The **By role** tab shows what each role definition grants. Note the wording — a *role definition*
is not the same as a *person's* effective permissions, because inheritance and overrides can change
the latter. For a specific person, use the detail panel in Platform → Users.

### Platform → Impersonation

**What it is:** controlled, temporary access to a customer's tenant as one of their users.

**What it's for:** support. When a customer says "the assistant won't publish", this lets you see
exactly what they see instead of guessing.

Requires `platform.support.impersonate`, is time-limited, and is **fully audited**. While a session
is active, a banner is pinned across the top of the console with a live countdown and a one-click
exit — a support session that doesn't announce itself is how "I forgot I was impersonating"
incidents happen.

Both the tenant and the target user are picked from searchable lists, not typed as raw IDs — the
target-user search only needs to find *a* person; the backend independently re-checks they actually
hold an active membership in the chosen tenant when the session starts.

**What you can't do while impersonating:** anything high-risk. Your platform permissions are set
aside entirely, and the target user's own `high` and `critical` permissions are stripped too. You
can look; you cannot grant yourself a role or export the customer's data while wearing their face.

---

## Tenant screens

Scoped to whichever tenant you're currently in. Switch tenants from the picker in the top bar.

### Tenant → Dashboard

**What it is:** the landing page inside a tenant.

**What it's for:** orientation — the tenant's status, your own effective permissions here, and quick
routes into the areas you have access to.

Your permissions here are unrelated to your platform ones. You can be a platform super-admin and
still be a read-only member of this particular tenant.

### Tenant → Members

**What it is:** the people in this tenant.

**What it's for:** the day-to-day of team management — inviting people, assigning their roles, and
handling departures.

- **Invite** — sends an invitation tied to a specific email address, optionally with roles
  pre-assigned so they're productive on arrival. Since this deployment sends no email (see
  [Things that surprise people](#things-that-surprise-people)), the invitation link never actually
  reaches anyone yet.
- **Add member** — the practical alternative to Invite today: pick an existing user from the
  searchable picker and give them an active membership immediately, no email step involved.
- **Suspend / Reactivate** — a temporary hold on this tenant only. Their account and their other
  tenants are unaffected.
- **Revoke access** — removes them from this tenant permanently. Their login still works elsewhere.
- **Restore access** — undoes a revocation, putting a former member back with a fresh membership.
  Useful for the "I revoked the wrong person" moment.
- **Edit job title** — the pencil next to a member's name; a small free-text field with no bearing
  on access.

Roles are assigned per membership, from this screen.

### Tenant → Roles & permissions

**What it is:** the tenant's own RBAC — the most powerful screen in the tenant section.

**What it's for:** shaping access to fit how the customer actually works, rather than accepting
whatever the defaults are.

Four things live here:

1. **Roles** — the built-in ones (Tenant Owner, Administrator, Member) plus any custom roles you
   define for this tenant. A custom role is just a named bundle of permissions, and its permissions
   stay editable afterward from the **Edit** action on the role's row — you're not locked into what
   you picked at creation. Built-in roles are marked **System** and can't be edited: a customer
   accidentally stripping their own Tenant Owner role of the permission needed to fix that is not a
   recoverable mistake, so the built-ins are fixed by design.
2. **Role hierarchy** — make one role inherit another's permissions, so "Senior Analyst" can be
   "Analyst plus three extra things" rather than a duplicated list. Cycles are rejected.
3. **Overrides** — a targeted exception for one person: grant them one extra permission, or take
   one away, without inventing a whole role. Every override records a reason and can expire.
4. **The permission catalog** — what's available to build with.

Two guard rails worth knowing:

- **You can't grant what you don't hold.** The same rule as platform roles, applied here — including
  when *defining* a custom role, not just when assigning one. Otherwise you could write yourself a
  role containing anything and then take it.
- **A DENY override always wins** over any grant. That's what makes "everyone except this
  contractor" expressible.

### Tenant → Assistants

**What it is:** the AI assistants this tenant has configured.

**What it's for:** creating and editing assistants and — the part that matters for access control —
deciding **who can see each one**.

You can **create**, **edit** (name, description, model configuration, system prompt), and
**archive** an assistant (the pencil and archive icons on each row). Archiving is a one-way soft
delete — an archived assistant drops out of the active list and can't be published or edited
further, but nothing is destroyed.

**What "Model configuration" means.** It's *which AI model and provider settings* the assistant
actually runs on — think "Claude Opus via our Anthropic account" as opposed to a different model or
a different set of parameters. It's picked from a list, not typed, and the list only offers
configurations this tenant actually owns; a **platform default** configuration (one every tenant is
meant to be able to use) is shown for visibility but greyed out as currently unassignable — a known
gap in how assistants are linked to model configurations at the database level, tracked as a
backend defect rather than hidden. If the tenant has no configuration of its own yet, the form says
so and points at asking a platform administrator to provision one; there's no manage screen for
these yet.

Four visibility modes:

| Mode | Who can see it |
|---|---|
| **Tenant** | Everyone in this tenant |
| **Department** | One department |
| **Team** | One team |
| **Restricted** | Only people explicitly added to it |

**Department and team visibility are currently unavailable, and the console is honest about it** —
those two options are shown disabled rather than offered and silently broken. The reason is that
nothing in this product yet lets an administrator *put* a member into a department or team in the
first place: the columns exist on a membership row, but there's no Department or Team management
screen, no picker, and nothing validates the values — so an assistant set to "Department" visibility
today would be visible to nobody, ever, with no way to fix it short of restarting the create flow.
Until department/team assignment ships, use **Restricted** (explicit per-person access) for anything
narrower than the whole tenant.

Assistants also move through **draft → published → archived**, so work-in-progress isn't exposed to
the whole company before it's ready.

> If you can't see an assistant you don't have access to, the system reports it as *not found*
> rather than *forbidden*. That's on purpose: "you're not allowed to see this thing that exists" is
> itself a leak of information.

### Tenant → Knowledge bases

**What it is:** collections of documents an assistant can draw on when answering.

**What it's for:** grounding assistants in the customer's own material — help articles, contracts,
policies — instead of general knowledge. This is the retrieval half of a RAG setup.

Knowledge bases use the same four visibility modes as assistants.

**Documents** opens the upload panel. Drop files in or browse for them — PDF, Word, Excel,
PowerPoint, CSV, JSON, XML and images (which are OCR'd), up to 50 MB each. Both entry points run
the *same* validation and the *same* upload, so a `.zip` is refused identically whether it was
dropped or picked; files are **staged first**, listed with their sizes and individually removable,
and only sent when **Upload** is pressed. Rejections (wrong type, over 50 MB, empty, already in the
list) are reported per file rather than silently dropped.

Uploading is instantaneous; *indexing* is not. A file is parsed, split into chunks and embedded by
a background worker, so it appears in the list as **Processing** and settles to **Ready** or
**Failed** on its own — the list refreshes itself until nothing is still in flight. A failed
document shows the reason inline, so a corrupt or password-protected file can be fixed without a
support ticket.

**Status and chunk count answer different questions, and the list shows both.** *Ready* means the
pipeline finished; **Chunks** means it produced something searchable. A `Ready` document with zero
chunks — a scanned PDF whose pages defeated OCR is the usual cause — is in the knowledge base and
cannot answer anything, so it is called out in amber rather than left looking healthy. New
ingestions can no longer end that way at all: zero chunks from an uploaded file is now recorded as
a failure with a reason.

Each row has **Re-ingest** and **Delete**. Re-ingest re-queues the stored bytes, so a transient
failure needs no re-upload and the document keeps its identity and history. Delete removes the
vector points first, then the chunk rows, then the stored file, then soft-deletes the record — in
that order, because an orphaned vector would keep answering questions and citing a source the
tenant was told is gone. It asks for confirmation and cannot be undone. Both are gated on
`tenant.documents.upload`: changing what a knowledge base contains is one authority, whether that
means adding a file or removing one.

> If documents stay on **Processing** indefinitely, the background worker isn't running. It's a
> separate process from the API — see [22-deployment-and-operations.md](22-deployment-and-operations.md).

Once a document is Ready, the built-in **retrieval tester** checks what a given question actually
pulls back, which is the fastest way to diagnose "the assistant gave a bad answer".

The security property here is invisible but important: which slice of the vector store gets searched
is derived on the server from the knowledge base you were already authorized for. It's never taken
from the request, so a crafted query can't read another tenant's documents.

**Embed** publishes a knowledge base to the open internet as a chat widget. This is the biggest
step available on this screen and the dialog says so plainly: everything else here changes what your
own members can see, while a widget lets **anyone visiting the listed websites** ask questions
answered from that knowledge base, with no sign-in.

Creating one asks for three things:

- **Allowed websites** — the exact addresses where the widget may run, e.g. `https://example.com`.
  There are no wildcards, deliberately: `*.example.com` looks convenient and is how origin checks get
  broken, because a naive suffix match also accepts `evil-example.com`. List each subdomain you use.
- **Questions per day** — a spending cap. Once reached the widget stops answering until tomorrow.
- **A name**, so a tenant with several widgets can tell them apart.

You then get a one-line `<script>` tag to paste into the site. The **public key** in it is an
identifier, not a secret — it ships in the page source, and on its own it grants nothing: the widget
row decides which knowledge base is readable, the allowed-websites list decides where it may run, and
the daily cap decides how much it may spend. That's the opposite of a provider credential, which is
encrypted and never shown again.

**Turn off** is the control to reach for if a widget is being abused, and it takes effect
immediately — not when the visitor's session expires. Visitors hold a 30-minute session token, but
the widget is re-read on every single question, so "off" means off now. Turning it back on resumes
answering.

> Worth knowing what the allowed-websites list is and isn't worth. Browsers set the `Origin` header
> and page JavaScript cannot forge it, so it genuinely stops another *website* embedding your widget.
> It does not stop someone calling the endpoint directly from a script. Against that, the real
> defences are the daily cap and the per-IP rate limit — they bound what abuse can cost you rather
> than preventing it.

### Tenant → Conversations

**What it is:** the record of exchanges between members and assistants.

**What it's for:** oversight and troubleshooting — usage patterns, and reviewing a specific
conversation when something went wrong.

**Conversation content is owner-only.** Ordinary members see their own conversations and nobody
else's. Someone holding the auditor permission can see others' — and **every such access is itself
recorded**. Reading someone's conversation is an act that leaves a trace.

### Tenant → Provider credentials

**What it is:** where a tenant's own AI provider API keys (OpenAI, Anthropic, …) are stored.

**What it's for:** letting a customer bring their own keys, so usage bills to their provider account
rather than yours.

**A secret you paste here can never be read back.** It's encrypted immediately on arrival, and every
screen and API response that lists credentials shows only the provider name and a short hint
(`sk-…4f2a`) — there is no field anywhere capable of carrying the secret back out. You can **rotate**
(replace) or **delete** a credential, but never view it. If someone has lost the key, they get a new
one from the provider; this system won't recover it for them.

That is deliberate. A form that merely declines to display a secret is one careless change away from
leaking it; a system with nowhere to put it cannot.

---

## Account → My identity

**What it is:** your own account, available from every screen.

**What it's for:**

- Your profile and account status.
- **Change password** — signs you out everywhere, including the browser you're using. That's the
  point: if the old password leaked, a live session somewhere else is exactly what you're closing.
- **Multi-factor authentication** — enroll an authenticator app. Once verified, it's required at
  sign-in.
- **Linked sign-in providers** — Google/Facebook accounts attached to this identity.
- **Sign out everywhere** — kills every session on every device immediately.

---

## Common tasks, start to finish

**Onboard a new customer**
1. Platform → Users → **New user** — create the account for their administrator (unless they already
   have one).
2. Platform → Tenants → **New tenant** — name the organization, accept the generated slug, choose
   that person as owner.
3. Tell them their initial password through some channel other than email-from-this-system, because
   this system doesn't send email.

**Give a colleague the ability to onboard customers**
1. Platform → Users → find them → open the detail panel.
2. Grant a platform role that includes `platform.tenants.create`.
3. If you're refused, you don't hold that permission yourself — the message names what's missing.

**Someone left the company**
- They're leaving *one customer's* team → Tenant → Members → **Revoke access**.
- They're leaving *your* company → Platform → Users → **Suspend** (reversible) or **Delete**
  (permanent, soft). Either way, every session dies immediately.

**A customer reports a bug you can't reproduce**
1. Platform → Impersonation — start a session against their tenant, giving a reason.
2. Work through the problem; the banner shows your countdown.
3. End the session. Everything you did is in the audit log.

**Work out why someone can't do something**
1. Platform → Users → their row → detail panel — shows their resolved permissions and tenants.
2. If it's tenant-specific, switch into that tenant → Roles & permissions — check their roles, any
   inherited roles, and whether a DENY override is in the way.

---

## Things that surprise people

**No email is ever sent.** Registration and password-reset links are never delivered — this
deployment logs them instead of mailing them. So: create accounts through Platform → Users and pass
the password on yourself, and expect accounts to show as "not verified". The very first
administrator has to be created with `scripts/bootstrap_platform_admin.py` for the same reason.

**Suspension is immediate, not eventual.** Suspending a user or changing their password doesn't wait
for their session to expire — every existing session stops working on their next request.

**Deletion keeps the row.** A deleted user disappears from the directory and can't sign in, but the
record survives so audit history still makes sense.

**Being a platform admin gives you nothing inside a tenant.** The two worlds are separate. To act
inside a customer's tenant you need a membership there, or an impersonation session.

**Impersonation is intentionally weak.** You get a *reduced* version of the target's access, not
their full powers. If you can't do something while impersonating, that's the design working.

**Nothing you can't see is admitted to exist.** Resources outside your visibility report as "not
found" rather than "forbidden", so the console can't be used to enumerate what's there.

---

## Where to go deeper

| Question | Document |
|---|---|
| How do I install and run this? | [../DEPLOYMENT.md](../DEPLOYMENT.md) |
| How does the console avoid exposing tokens to the browser? | [../frontend/README.md](../frontend/README.md) |
| How are effective permissions actually calculated? | [06-authorization-model.md](06-authorization-model.md) |
| How is one tenant's data kept from another's? | [07-tenant-isolation-and-rls.md](07-tenant-isolation-and-rls.md) |
| What attacks was this designed against? | [03-threat-model.md](03-threat-model.md) |
| How do I run this in production? | [22-deployment-and-operations.md](22-deployment-and-operations.md) |
