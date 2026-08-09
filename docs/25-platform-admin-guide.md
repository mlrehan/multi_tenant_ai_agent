# Platform Administrator Guide

**Who this is for:** you run the service itself. You create customer organisations, manage
accounts across all of them, and keep the lights on. You are *staff*, not a customer.

**Who this is not for:** if you run one organisation's account — its members, its documents,
its chat widget — you want the [Tenant Administrator Guide](26-tenant-admin-guide.md) instead.

---

## The one idea to understand first

The system has **two completely separate worlds**:

| | Platform world | Tenant world |
|---|---|---|
| Who lives here | You, the service operator | Your customers |
| What you manage | Organisations, all accounts | One organisation's people and content |
| Permission names | start with `platform.` | start with `tenant.` |

These are **not** two levels of the same thing. They are two separate lists, kept in
separate database tables. A tenant role can *never* contain a platform permission — not
because someone remembered to check, but because there is nowhere to put it.

Practical consequence: **being a platform administrator does not let you see inside a
customer's data.** You can create a tenant, suspend it, and see that it exists. You cannot
read its documents or its conversations. If you need to act inside a customer's account,
you use Impersonation (below), which is time-limited and fully recorded.

---

## Signing in and what you'll see

Go to the console and sign in. Your sidebar shows a **Platform** section and an **Account**
section — and nothing else.

That is not a bug. The sidebar shows only the worlds you belong to. If you have no
membership in any customer organisation, there is no Tenant section for you, because there
is no tenant you're part of. A colleague who is both staff *and* a member of a customer
organisation would see both.

**Overview** is your landing page: how many tenants exist, how many accounts, and — most
usefully — **Your platform authority**, which lists in plain text exactly what your roles
let you do. If you're ever unsure whether you can do something, look there first.

---

## Task: create a new customer organisation

This is the main thing you do. It takes two steps, in this order.

### Step 1 — create the person who will run it

**Platform → Users → New user.**

Fill in their email address and an initial password (at least 12 characters).

The account is **active immediately**. Normally a new account has to prove its email
address by clicking a link. Here, *you* are that proof — an administrator creating the
account is the vouching step. This also matters because **this deployment does not send
email at all** (see "Things that will surprise you" below), so a verification link would
never arrive.

You must **give the person their password yourself** — a phone call, a password manager,
however your organisation does it. It is hashed the moment you press the button and is
never shown again, to you or anyone.

### Step 2 — create the organisation

**Platform → Tenants → New tenant.**

- **Organization name** — what the customer calls themselves, e.g. `Riverside Academy`.
- **URL slug** — fills in automatically as you type (`riverside-academy`). You can change
  it now, but **not later**: it gets baked into links and API references. Pick carefully.
- **Owner** — start typing the email of the person from Step 1 and pick them from the list.

Press **Create tenant**. Three things happen together, or none of them do:

1. The organisation is created.
2. The owner gets a membership in it.
3. That membership gets the **Tenant Owner** role.

They can now sign in and start working. You are *not* a member of their organisation and
will not see their content.

---

## Task: manage accounts

**Platform → Users** is every account on the service, across every customer. Search by
email; results are paginated.

Click a row to open the detail panel, which shows their platform roles, their resolved
permissions, and which organisations they belong to.

| Action | What it actually does |
|---|---|
| **Suspend** | They cannot sign in, and every existing session is killed immediately — not when their token expires. Use for a departing employee or a compromised account. |
| **Reactivate** | Undoes a suspension. They can sign in again. |
| **Rename** | Changes their email. Resets verification and signs them out everywhere. |
| **Delete** | A *soft* delete — the row stays because the audit log points at it. They cannot sign in and cannot be brought back. |

> **You cannot suspend yourself.** Recovering from that would need direct database access,
> so the system refuses outright.

**Suspending an account is not the same as removing someone from one organisation.**
Suspend stops them everywhere. To remove them from a single customer, that customer's own
administrator does it in their Members screen — or you do it via Impersonation.

---

## Task: look at what permissions exist

**Platform → Permissions** lists every platform-scope permission, grouped by what it
affects, each tagged `low` / `medium` / `high` / `critical`.

The risk tag is not decoration. **An impersonated session has every `high` and `critical`
permission stripped from it.** That is what makes support impersonation safe to hand to a
support team.

**Platform → Roles** shows the roles those permissions are bundled into, and lets you
create new ones and grant or revoke them from people.

### The rule that governs every grant

**You can only give away what you already hold.** You cannot grant a permission you don't
have, and you cannot promote yourself. This applies everywhere authority changes hands —
granting a role, creating a role, building a role hierarchy. There is no "admin override".

If a grant is refused and you expected it to work, check **Overview → Your platform
authority**. You're almost certainly trying to give away something you don't have.

---

## Task: help a customer who is stuck (Impersonation)

Sometimes a customer says "the button doesn't work" and you need to see what they see.

**Platform → Impersonation.** Pick the organisation, pick the person, start the session.

What you must know about it:

- **It is time-limited.** It ends by itself.
- **It is fully recorded.** Every action is written to the audit log tied to *your*
  identity, not theirs. The record shows you did it while acting as them.
- **It is deliberately weaker than the real user.** Every `high` and `critical` permission
  is removed. You can *see* what they see and do routine things. You cannot grant roles,
  change permissions, or export data while impersonating — even if that person normally
  could.
- **A banner is visible the whole time.** You will not forget you're in a session.

That last point is the design's whole intent: impersonation is for *diagnosis*, not for
doing powerful things on someone's behalf.

---

## Task: suspend or restore an organisation

**Platform → Tenants → Suspend.** Use when a customer stops paying, or during a security
incident.

Suspending stops the organisation's people from working in it. The data is untouched.
**Reactivate** brings it back exactly as it was. Nothing is deleted.

**Rename** changes the display name only. The URL slug never changes — links and API
references depend on it.

---

## Things that will surprise you

**No email is ever sent.** Not password resets, not verification links, not invitations.
The system logs "email queued" and does nothing. This is a known gap, not a fault you can
configure away. It means:

- Create accounts yourself in Platform → Users; self-registration cannot complete.
- If someone forgets their password, you cannot send them a reset. You rename or recreate
  the account, or reset it directly in the database.

**Suspending is immediate, not eventual.** Sessions die at once. This is deliberate: a
compromised account that stays usable for another fifteen minutes is a compromised account.

**Two accounts can have the same person's name but they are separate accounts.** Identity
here is the email address.

**Search only exists on Platform → Users.** Other lists show everything. That's fine at
current scale and is a known limitation.

---

## Quick reference

| I want to… | Go to |
|---|---|
| Add a new customer organisation | Platform → Users (create owner), then Platform → Tenants |
| Stop someone signing in anywhere | Platform → Users → Suspend |
| See what I'm allowed to do | Platform → Overview → Your platform authority |
| See every permission that exists | Platform → Permissions |
| Bundle permissions into a role | Platform → Roles |
| Look at a customer's screen to help them | Platform → Impersonation |
| Pause a customer's account | Platform → Tenants → Suspend |
| Change my own password or set up 2FA | Account → My identity |

---

## If something goes wrong

**"I can't grant this role."** You're trying to give away a permission you don't hold.
Check your own authority on the Overview page.

**"The customer says their uploads are stuck on Processing."** The background worker isn't
running. It's a separate process from the website — see
[22-deployment-and-operations.md](22-deployment-and-operations.md). Uploads will queue up
and complete once it starts.

**"A screen is empty that shouldn't be."** Check you're looking at the right organisation
— the tenant switcher is in the top bar.

**"I can't see a customer's documents."** Correct, and intentional. Use Impersonation.
