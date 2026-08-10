# Tenant Administrator Guide

**Who this is for:** you run your organisation's account. You add colleagues, decide who
can do what, load your documents in, and put a chat assistant on your website.

You don't need to understand how any of it works underneath. This guide is the "click
here, then here" version.

---

## The one idea to understand first

Your organisation is called a **tenant**. Everything you create — people, documents, chat
widgets — belongs to your tenant and **nobody outside it can see any of it**. Not other
customers, and not the staff who run the service.

The one exception: service staff can start a **support session** to see your screen when
you ask for help. When they do, it's time-limited, every action is recorded against their
name, and the system strips their most powerful abilities for the duration. They can look;
they can't quietly change things.

---

## Signing in and finding your way

Sign in and you land on your **Dashboard**. The sidebar has a **Tenant** section (your
organisation) and an **Account** section (you personally).

The Dashboard's most useful part is **"What you can do here"** — a plain list of everything
you're permitted to do. If a button is missing somewhere, look here: you probably don't
have that permission, and whoever owns your account can grant it.

If you belong to more than one organisation, there's a switcher in the top bar.

---

## Task: add your colleagues

**Members → Add member.**

You need their email address. If they already have an account on the service, they're
added straight away. If not, ask your service administrator to create the account first —
**this system does not send invitation emails**, so an invitation would sit unread forever.

Once someone is a member you can:

| Action | What it means |
|---|---|
| **Suspend** | They stay a member but can't do anything here. Reversible. Good for someone on long leave. |
| **Reactivate** | Undoes a suspend. |
| **Revoke** | Removes them from your organisation. |
| **Restore** | Brings a revoked member back, with their history intact. |
| **Edit** | Change their job title. |

None of this touches their actual account — it only controls their access to *your*
organisation. Only service staff can suspend someone's whole account.

---

## Task: decide who can do what

**Roles & permissions.**

A **permission** is one specific ability, like "upload documents". A **role** is a named
bundle of permissions, like "Content Editor". You give people roles, not individual
permissions.

To make a new role: **Create role**, name it, tick the permissions it should include.
Then assign it to people from the Members screen.

You can also edit an existing role's permissions later — add or remove them and everyone
holding that role is affected immediately.

### The rule that catches everyone out

**You can only give away what you already have.** If you can't upload documents, you can't
create a role that can, and you can't grant it to anyone. This is not a bug and there is no
way around it — it's what stops someone quietly promoting themselves.

If a change is refused, check the Dashboard list of what you're allowed to do.

---

## Task: give your assistant something to know

This is the heart of it. A **knowledge base** is a folder of material your assistant can
answer from.

### Step 1 — make one

**Knowledge bases → New knowledge base.** Give it a name that describes its contents, like
`Admissions FAQ` or `Refund Policy`. Leave visibility as **tenant** unless you have a
reason not to — that means everyone in your organisation can use it.

> You may see **department** and **team** in the visibility list, greyed out. They're not
> broken; there's simply nothing in the product yet that puts people into departments or
> teams, so choosing them would make the knowledge base unreachable by anyone.

### Step 2 — put material in it

Click **Documents** on your knowledge base. There are two ways in.

**Upload files.** Drag them in, or click browse. PDF, Word, Excel, PowerPoint, CSV, JSON,
XML and images (which get read with OCR), up to 50 MB each.

**Point at a website.** Under "Add from the web":
- **url_list** — fetches exactly the pages you list, and nothing else.
- **site** — starts at the address and follows links through the site.

Paste one or more addresses and press **Start**.

### Step 3 — wait for it to be ready

Uploading is instant. **Being searchable is not** — behind the scenes each document is read,
split into pieces, and indexed. You'll see it as **Processing**, then **Ready**. The list
refreshes itself, so just leave it open.

How long that takes depends almost entirely on **whether your PDF is a real document or a
photograph of one**:

| What you uploaded | Roughly how long |
|---|---|
| A PDF made by Word, Google Docs, or a report tool | A moment — the text is already inside it |
| Word, Excel, PowerPoint, CSV, JSON, XML | A moment |
| A **scanned** PDF or photographed pages | Much slower — the words have to be read out of the image |

If a scanned document seems to take forever, it isn't stuck. Reading text out of pictures is
genuinely slow work. If you have a choice, export the original as a PDF rather than scanning
a printout: it will index in a fraction of the time and the text will be exact rather than
recognised, which also makes answers more accurate.

If something fails, the reason appears next to it, so you can fix the file and try again.

> **If documents sit on Processing forever**, the background worker isn't running. That's
> one for whoever operates the service — nothing is wrong with your file.

**Read the Chunks column, not just the status.** *Ready* means the pipeline finished;
**Chunks** tells you whether it found anything to search. A document showing **0 chunks** is
flagged in amber — it is in your knowledge base but cannot answer a single question. The
usual cause is a scanned PDF whose pages were too large or too complex to read. Re-export it
from the original if you can, or split it into smaller files.

### Fixing and removing documents

Every document has two buttons on the right:

- **Re-ingest** (circular arrow) — puts it back through the pipeline. Use this after a
  failure that wasn't the file's fault, or after the service has been fixed. You do not need
  to find and upload the file again; the copy already stored is reused, and the document
  keeps its place in the list.
- **Delete** (bin) — removes the document, everything indexed from it, and the stored file.
  It asks first, and it **cannot be undone**. Assistants stop finding it immediately, so this
  is also how you take something out of circulation quickly.

**Click a document's name to see what was actually read out of it.** This is the fastest way
to answer "why doesn't the assistant use this?". You get the text in the pieces the assistant
searches, each with where it came from — a page number for a PDF, a row for a spreadsheet, a
web address for a crawled page.

What to look for:

- **Nothing there at all** — the file gave up no text. Almost always a scan.
- **Text that reads like gibberish** — the words were guessed from a picture of the page.
  Re-export the original rather than scanning it.
- **The wrong content entirely** — crawled pages often capture cookie banners, navigation
  menus or "skip to content" links instead of the article. If the pieces are full of that,
  the assistant is searching the furniture rather than the room.

### Refreshing a website you've already added

Each web source has a **re-crawl** button next to its status. It fetches the pages again:
anything that changed is updated, anything new is added, and nothing is duplicated — a page
keeps its place rather than appearing twice.

Two things it deliberately does **not** do. It doesn't remove pages that have disappeared
from the site, because a site that is briefly down would otherwise empty your knowledge base;
delete those individually if you want them gone. And it doesn't re-fetch on a schedule —
re-crawling happens when you ask for it.

### Step 4 — check it actually works

Two buttons on your knowledge base:

- **Test search** — shows you which pieces of your material a question pulls back. This is
  the fastest way to work out why an answer was poor: usually the right passage wasn't
  found, which means the source material doesn't say it clearly.
- **Ask** — gives you the full answer with citations, exactly as a visitor would see it.

Do this before putting anything in front of customers.

---

## Task: put a chat assistant on your website

**Knowledge bases → Embed.**

This is the biggest step on the screen, so read this bit properly: a widget lets **anyone
visiting the websites you list** ask questions answered from that knowledge base, with no
sign-in. Only publish a knowledge base you're happy for the public to read from.

Fill in three things:

**Name** — for you, so you can tell widgets apart.

**Allowed websites** — the exact addresses where the widget may run, like
`https://www.myschool.org`. Separate several with commas.

> **No wildcards.** You cannot write `*.myschool.org`. That looks convenient but it's how
> these checks get broken — a sloppy match on `.myschool.org` would also accept
> `evil-myschool.org`. List each address you actually use.

**Questions per day** — a spending cap. Once it's reached the widget stops answering until
tomorrow. This is your protection against a bad day costing you a lot of money.

Press **Create widget**. You get a single line of HTML. Give it to whoever looks after your
website and ask them to paste it in before `</body>`. That's the entire installation.

### Running it day to day

- **Turn off** switches the widget off **immediately** — not in a few minutes. If it's
  being abused or saying something wrong, that's the button.
- **Turn on** brings it back.
- The **public key** in the snippet is not a password. It's meant to be visible in your
  page's source. On its own it grants nothing: the widget decides which knowledge base it
  reads, your website list decides where it may run, your daily cap decides how much it may
  spend.

### What a visitor sees

A small chat button in the corner. They ask a question; the answer streams in with numbered
sources. If your material doesn't answer the question, it says so rather than guessing —
that's deliberate, and it's what makes the answers trustworthy.

Visitors are shown *where* an answer came from, but never internal identifiers.

---

## Task: manage AI assistants

**Assistants** are for use *inside* your organisation, unlike widgets which face the public.

**New assistant** — give it a name, pick a **model configuration** from the dropdown (this
is which AI model it uses; if you're unsure, take the default), and optionally write a
**system prompt** describing how it should behave.

You can **edit** it later or **archive** it, which removes it from the active list without
destroying its history.

---

## Task: use your own AI provider account (optional)

**Provider credentials.** By default the service uses its own AI provider account. If you'd
rather use yours and be billed directly, add the key here.

It is encrypted the moment you save it and **never shown again** — not to you, not to
support, not in any screen or log. You'll only ever see a short hint like `sk-…4f2a` so you
can tell which key it is. If you lose the original, you replace it; nobody can recover it
for you.

---

## Looking after your own account

**Account → My identity.**

- Change your password.
- Turn on **two-factor authentication** with an authenticator app. Worth doing.
- See which sign-in methods are linked (Google, Facebook).
- **Sign out everywhere** — kills every session on every device at once. Use it if you
  think someone else has your password.

---

## Things that will surprise you

**No emails are sent.** No invitations, no password resets, no verification. Ask your
service administrator to create accounts.

**Uploading is not the same as being ready.** Give it a moment and watch for **Ready**.

**A knowledge base with nothing in it answers nothing.** It won't error — it'll just say it
doesn't have the information, which looks like a broken assistant but is an empty folder.

**"Turn off" really is immediate**, even for a visitor already chatting.

**You can't see other organisations, and they can't see you.** If a screen looks empty,
check the tenant switcher at the top — you may be looking at a different organisation.

---

## Quick reference

| I want to… | Go to |
|---|---|
| Add a colleague | Members → Add member |
| Stop someone accessing our account | Members → Suspend (or Revoke to remove) |
| Create a job role like "Editor" | Roles & permissions → Create role |
| Add our documents | Knowledge bases → New, then Documents |
| Pull in our website's content | Knowledge bases → Documents → Add from the web |
| Check why an answer was poor | Knowledge bases → Test search |
| Put chat on our website | Knowledge bases → Embed |
| Switch the public chat off right now | Knowledge bases → Embed → Turn off |
| Retry a document that failed | Knowledge bases → Documents → circular-arrow button |
| Remove a document and everything indexed from it | Knowledge bases → Documents → bin button |
| See what was actually read out of a file | Knowledge bases → Documents → click its name |
| Refresh a website you already added | Knowledge bases → Documents → re-crawl button beside the source |
| Use our own AI provider account | Provider credentials |
| Turn on 2FA | Account → My identity |

---

## If something goes wrong

**"The button I need isn't there."** You don't have that permission. Check the Dashboard's
"What you can do here", then ask whoever owns your organisation's account.

**"Documents stay on Processing."** The background worker isn't running. Contact whoever
operates the service.

**"It says Ready but the assistant never uses it."** Check the **Chunks** column. If it shows
**0**, nothing searchable came out of the file — almost always a scanned PDF. Re-export the
original as a PDF instead of scanning a printout, then delete the old copy and upload the new
one.

**"A document failed and I don't want to upload it again."** Press **Re-ingest**. The stored
copy is reused, so there is nothing to re-upload.

**"The widget doesn't appear on our site."** The page's address must be on the widget's
allowed-websites list, exactly — `https://www.example.com` and `https://example.com` are
different addresses. Add both if you use both.

**"The assistant says it doesn't know something that's definitely in our documents."** Use
**Test search** with the same question. If the right passage doesn't come back, the source
material probably doesn't state it plainly enough — rephrasing the document usually fixes
it better than rephrasing the question.
