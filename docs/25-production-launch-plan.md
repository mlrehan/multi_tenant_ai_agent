# Production launch plan

Status as of **2026-09-29**. The minimum work to take this platform to a first production launch (a pilot with real nursery customers), in the order I recommend. Nothing here has been started yet. Each item says why it blocks launch, roughly how long it takes, and who does it.

The feature set is broad and much of it is built to an enterprise standard (tenant isolation, access control, grounded answers with citations, metering and cost, live ingestion, handoff to humans). What stands between it and production is mostly operational safety, not missing features. See "After the pilot" for the enterprise-sales gaps.

---

## Before going live (must do)

### 1. Backups, with one test restore
- **Why:** one bad disk or mistaken command loses every tenant's data. The dev database has already been wiped twice.
- **What:**
  - A nightly backup of Postgres, the `uploads` volume and the `qdrant-storage` volume, copied off the server.
  - Then restore one onto a scratch machine and check it works.
  - Commands are in DEPLOYMENT.md, "Backing up the database" (now including Qdrant).
- **Effort:** about half a day, mostly infrastructure. **Owner:** you, with help.

### 2. Real email delivery
- **Why:** password reset, invitations and email verification only write to a log today (`ConsoleEmailSender`). Locked-out users can't recover their accounts, and invitations never arrive.
- **What:**
  - One adapter behind the existing `EmailSender` / `InvitationEmailSender` ports, for Postmark, Amazon SES or SMTP, plus settings and a test.
  - Once email is verifiable, reconsider letting `PENDING_VERIFICATION` accounts sign in.
- **Effort:** about half a day. **Owner:** Claude (code).

### 3. One full test run, and fix the local database name
- **Why:** the security, integration and API suites haven't run since the answer gate, tenant prompt layers, ingestion progress and knowledge-base delete went in.
- **What:**
  - Set `DATABASE__NAME=iam_platform` in your local `.env`. It currently says `iam_platform_test`, which is why your console data and the tests share a database and test runs wipe it. Your current data lives in `iam_platform_test`, so either recreate it afterwards or copy it across first.
  - Then run `python -m pytest` against the separate test database and fix anything real it finds.
- **Effort:** about an hour, plus fixes. **Owner:** Claude.

### 4. Pass the real client IP through the console to the rate limiter — ✅ done 2026-09-29
- **Done:**
  - The console proxy now forwards one validated client IP (the last `X-Forwarded-For` entry, IPv4-mapped addresses unwrapped).
  - The API trusts that header only from loopback and private-network peers: `FORWARDED_ALLOW_IPS`, previously `"*"`.
- **Verified live:** after visitor A used up exactly 300 requests and got 429, visitor B was unaffected. A forged-then-real header was bucketed under the real IP. A real signed-in session counted against the browser's own address.

*(Original description:)*
- **Why:** the API rate-limits per IP (300 requests a minute). Signed-in traffic reaches the API from the console's server process, which doesn't forward the visitor's IP, so **every console user shares one bucket**. A handful of admins on dashboards with live polling can hit it and start getting 429s.
- **What:**
  - The BFF proxy (`frontend/src/app/api/backend/[...path]/route.ts`) forwards the client IP, which Nginx now sets by overwriting `X-Forwarded-For`, as DEPLOYMENT.md Step 8c already does.
  - Also narrow uvicorn's `forwarded_allow_ips="*"` (in `asgi.py`) to the proxies actually in front of it.
- **Effort:** a couple of hours, including a test. **Owner:** Claude (code).

### 5. Production configuration, then one staging deploy
- **Why:** the production setup has never been deployed end to end.
- **What:** follow DEPLOYMENT.md Part B (corrected on 2026-09-29) on a staging server.
  - Fresh secrets, HTTPS, correct `CORS_ALLOWED_ORIGINS` / `PUBLIC_API_BASE_URL` / `JWT__ISSUER`.
  - Nginx with `client_max_body_size` and the overwritten `X-Forwarded-For`.
  - **One** console process.
  - Then click through sign-in, create a tenant, upload a document, answer through the widget, and hand off to a person.
- **Effort:** about a day. **Owner:** you, with Claude's help.

### 6. Basic monitoring
- **Why:** otherwise the first sign of a dead worker or a failing ingestion is a customer complaint.
- **What:**
  - An uptime check on the API's `/readyz` (from the server, since it isn't public).
  - An alert when `celery … inspect ping` fails.
  - Error reporting such as Sentry for the API, worker and console.
- **Effort:** a few hours. **Owner:** you, with Claude's help.

### 7. The legal minimum for children's data (UK)
- **Why:** nursery conversations can contain children's personal and safeguarding information, and they go to US AI providers.
- **What:**
  - Agreements with OpenAI and Cohere (with zero data retention if available).
  - A privacy notice beside the website chatbot.
  - A written procedure for deletion requests (a manual one is fine for a pilot).
  - Confirm each tenant's retention setting.
- **Effort:** your side. Claude can draft the technical parts.

---

## Accepted for the pilot (conscious decisions)

- **Run exactly one console process.** Token-refresh sharing is per process; several processes can sign people out at random. One is plenty for a pilot.
- **No key rotation yet.** The JWT signing key and the data-encryption key can't be rotated. Acceptable while they are strong, stored `chmod 600` and backed up encrypted; it becomes urgent only if a key leaks.
- **Deploys take several minutes and briefly interrupt service** (the Dockerfile layer order; Compose recreate). Deploy at quiet times.
- **Falgoon's plan allows 2 knowledge bases** (raised for testing on 2026-09-29). Set it back to 1 if that wasn't intended.

---

## After the pilot (for enterprise sales)

In rough priority:

1. **Enterprise single sign-on and provisioning:** SAML or OIDC and SCIM. Only Google and Facebook sign-in exist today. Most enterprise security reviews require these.
2. **Key rotation:** JWKS-style multi-key JWT verification, and a versioned encryption envelope with KMS.
3. **Data-protection tooling:** export and erase a person's data (including website visitors), and data-residency options.
4. **CI:** enforce `ruff`, `mypy`, `lint-imports` and the tests (57 ruff and 10 mypy errors remain today, mostly long prompt lines). Add frontend tests (none exist).
5. **Load testing and scaling:** multi-worker ingestion, prompt caching (answers use about 7,800 tokens), and preventing concurrent token-budget overshoot.
6. **Integrations:** a public API with keys (the table exists, no API), webhooks, connectors (Google Drive, SharePoint, Notion, help desks), scheduled re-crawls, and custom widget domains.
7. **Billing:** subscriptions, payments and invoices on top of the existing plans and metering.
8. **Human-agent tooling:** saved replies, response-time targets, assignment rules, satisfaction surveys and conversation export.
9. **AI quality:** an automated answer-evaluation suite, output moderation and personal-data redaction, and a staged way to test prompt or model changes.
10. **Accessibility audit and languages.**
11. **Dockerfile:** move the application code copy below the model and browser layers, so code-only rebuilds take seconds, not minutes.
12. **Open product decisions:** whether agents may post into members' private Ask threads (`PostAgentMessage`); assigned model configurations that no answer path reads; `claude-opus-5` filed under provider `openai`.
