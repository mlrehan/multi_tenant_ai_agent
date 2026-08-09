# IAM Control Center — Admin Console

The web console for the Enterprise Multi-Tenant IAM Platform. Next.js 16 (App Router) +
TypeScript + Tailwind v4 + shadcn/ui, talking to the FastAPI backend in `../src`.

> **Looking for what the screens actually do?** This file is the architecture. For a plain-language
> tour of every screen — what it's for, and why Platform and Tenant are separate worlds — read
> [../docs/23-admin-console-guide.md](../docs/23-admin-console-guide.md).

---

## Quick start

You need the backend running first — see [../DEPLOYMENT.md](../DEPLOYMENT.md) Part A.

```bash
# 1. From the repo root: start Postgres + Redis, then the backend
docker compose -f docker-compose.dev.yml up -d
python -m alembic upgrade head
python -m iam_platform.asgi          # serves http://localhost:8000

# 2. In this directory
npm install
npm run dev                          # serves http://localhost:3000
```

Create `.env.local` if your backend isn't on `localhost:8000`:

```bash
BACKEND_API_URL=http://localhost:8000
```

**Seed some data to look at.** A fresh database has no roles, permissions, or tenants, so most
screens will be empty. Register an account through the UI at `/register`, verify it, then from the
repo root:

```bash
python scripts/seed_demo_data.py your-email@example.com
```

That creates the role/permission catalog, a demo tenant with three members, two assistants, and a
knowledge base, and grants your account the platform super-admin role.

---

## Architecture

### The browser never holds a token

Every authenticated request goes through a **same-origin BFF proxy** at `/api/backend/*`
(`src/app/api/backend/[...path]/route.ts`). The browser only ever has an `httpOnly` session cookie
it cannot read.

```
Browser ──fetch('/api/backend/…')──► Next route handler ──Bearer token──► FastAPI
        ◄──── JSON, tokens stripped ─┘
```

The proxy does four things:

1. Attaches `Authorization: Bearer` from the `httpOnly` access-token cookie.
2. Forwards `X-Tenant-Id` when the client sends one.
3. On a 401, **transparently refreshes once and retries** — the browser never sees the 401.
4. Extracts tokens out of login/refresh/impersonation responses into `httpOnly` cookies and
   **strips them from the body** before it reaches the browser.

This means an XSS payload cannot read the access token, and the frontend never needs to know the
backend's address. `NEXT_PUBLIC_*` is deliberately unused — the backend URL is server-only.

> `X-Tenant-Id` is *not* a security boundary here. The backend re-validates a real, active
> membership on every single request (docs/07), so a tampered header is harmless by the backend's
> own design — not by anything this frontend enforces.

### Two proxy rules that are easy to get wrong

Both of these were live bugs, not hypotheticals:

- **`204/205/304` responses must be built with a `null` body.** The `Response`
  constructor throws on any body for those statuses — including the empty string
  `upstream.text()` returns. Most mutating endpoints here answer 204, so getting
  this wrong turns every successful action into a 500 *after* the backend has
  already performed it.
- **`POST /v1/auth/logout` needs the raw refresh token substituted server-side.**
  The browser can't hold it, so the client sends a placeholder and the proxy
  swaps in the cookie value. Without that substitution logout clears cookies and
  looks fine while the session stays alive in the database.

### This is Base UI, not Radix

shadcn/ui here is built on Base UI. The differences that have bitten this
codebase:

| Radix | Base UI |
|---|---|
| `asChild` | `render={<Element />}` |
| `Menu.Item` `onSelect` | `Menu.Item` **`onClick`** (no `onSelect` exists) |
| `Tooltip` `delayDuration` | `delay` |
| `Select` `onValueChange: (v: string)` | `(v: string \| null)` |

The `onSelect` one is the dangerous entry: `onSelect` is a valid DOM prop on a
`<div>`, so TypeScript accepts it and the handler simply never fires. Every menu
item in the app was silently inert until this was found by clicking one.

### Where "/" sends you

`app/page.tsx` is a client component, not a `redirect()`, because the answer depends on the
caller's resolved permissions — which only exist behind the authenticated proxy:

- any platform permission → `/platform` (works with zero tenants; it's where you create the first)
- exactly one active membership → that tenant's dashboard
- otherwise → `/select-tenant`

The sidebar renders **every scope the caller holds**, not the one matching the URL prefix. The
prefix-based version left `/select-tenant` and `/account` with an empty rail, which is how a
platform admin with no tenants ended up on a screen with no navigation and no way forward.

### Route protection

`src/proxy.ts` (Next.js 16 renamed `middleware.ts` → `proxy.ts`) redirects unauthenticated visitors
to `/login` and signed-in visitors away from it. It is an **optimistic check only** — it reads
cookie *presence*, not validity. Real enforcement is the backend's per-request permission checks.

### Layout

```
src/
├─ app/
│  ├─ (auth)/          login, register, verify-email, forgot/reset password
│  ├─ (app)/           everything behind the sidebar shell
│  │  ├─ account/      own identity: password, MFA, linked providers, sessions
│  │  ├─ platform/     overview, tenants, users, roles, permissions, impersonation
│  │  └─ tenant/[tenantId]/   dashboard, members, rbac, assistants, …
│  ├─ api/backend/     the BFF proxy
│  └─ api/session/     cookie-presence + impersonation check (no secrets returned)
├─ features/           one folder per bounded context: api.ts (fetch) + hooks.ts (React Query)
├─ components/
│  ├─ ui/              shadcn/ui primitives
│  ├─ shared/          IdentityChip, PermissionList, StatusBadge, states
│  └─ app-shell/       sidebar, topbar, impersonation banner
└─ lib/                api-client, types (mirrors backend DTOs), auth-cookies
```

`features/*/api.ts` holds plain typed fetch calls; `features/*/hooks.ts` wraps them in React Query.
Pages never call `fetch` directly.

---

## Design decisions worth knowing

**Identifiers are the subject matter, so they're treated as first-class.** Tenant IDs, membership
IDs, and permission codes are set in JetBrains Mono and rendered through `IdentityChip`, which
truncates the middle of a UUID and copies to clipboard on click. Body text is Inter.

**The sidebar is deep navy even in light mode.** Security consoles read as operational with a dark
rail against a light canvas; a fully light sidebar has no anchor. Primary accent is teal, chosen to
stay out of the way of the semantic red/amber used for risk levels and destructive actions.

**Risk levels are data, not decoration.** `low`/`medium`/`high`/`critical` come from the permission
catalog and drive the impersonation permission-stripping on the backend, so they get their own color
scale rather than being folded into the generic status palette.

**Permission gating hides rather than disables.** Nav items and actions the caller lacks permission
for are hidden; while permissions are still loading they're also hidden, so a forbidden item never
flashes into view. `useHasTenantPermission` returns `undefined` while loading to make that explicit.

**The impersonation banner is a security control, not chrome.** It renders whenever the access
token carries an `act` claim, shows a live countdown, and offers a one-click exit. A support session
that doesn't visibly announce itself is how "I forgot I was impersonating" incidents happen.

**Empty states name the next action**, and **403 is not an error** — `ErrorState` renders a
permission failure differently from a real failure, because the request worked and the answer was
"you don't have that permission."

---

## Environment variables

| Variable | Required | Default | Notes |
|---|---|---|---|
| `BACKEND_API_URL` | in production | `http://localhost:8000` in dev | **Server-only.** Where the FastAPI backend lives. |

There are deliberately no `NEXT_PUBLIC_*` variables — nothing about the backend is exposed to the
browser.

---

## Scripts

```bash
npm run dev      # dev server (Turbopack)
npm run build    # production build
npm run start    # serve the production build
npm run lint     # eslint
npx tsc --noEmit # typecheck
```

`lint` and `tsc` both pass clean.

---

## Known gaps

These are backend limitations the UI works around, not frontend bugs:

- **No model-configuration CRUD.** You can pick an existing configuration, but nothing in the
  console creates one — `ListModelConfigurations` exists, create/update/delete don't.
- **Platform-default model configurations don't work.** `ai_assistants` carries a plain composite FK
  `(tenant_id, model_configuration_id) → model_configurations(tenant_id, id)` with a `NOT NULL`
  `tenant_id`, so a tenant assistant can only reference a config owned by that same tenant — a
  platform default (`tenant_id IS NULL`) is unreachable, despite docs/16 describing them as
  "readable by all tenants". `scripts/seed_demo_data.py` seeds a tenant-scoped config to work
  around it.
- **No override list endpoint.** Overrides can be created but not listed, so the RBAC tab creates
  only.
- **No delete or re-index for a document.** Uploading works and status is visible, but a document
  that ingested badly can only be replaced by uploading it again — there's no `DELETE` route and no
  way to re-run ingestion from the console.
- **Upload is one request per file with no progress bar.** The browser gets no byte-level progress
  from `fetch`, so multiple files upload sequentially with a single pending state rather than a
  per-file progress indicator.
- **No pagination anywhere.** Every list endpoint returns the full set; lists are rendered in full.
- **OAuth account linking isn't reachable** — the callback always does login/JIT-registration.

---

## Verified against a live backend

The auth flow, tenant selection, dashboard, and members roster were all exercised against a running
FastAPI backend and real Postgres, not mocks. Two real bugs were found and fixed that way: a nested
`<button>` hydration error in the tenant switcher, and — in the backend — a login crash on unknown
email addresses caused by a malformed dummy Argon2 hash (now covered by a regression test).
