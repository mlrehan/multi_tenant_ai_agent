# Deployment Guide

A complete, beginner-friendly walkthrough for running this project two ways:

- **Part A — Development.** Run it on your own laptop/PC to write code and test changes.
- **Part B — Production.** Run it for real on a cloud Ubuntu server, reachable over the internet.

You don't need to be a DevOps expert. Follow the steps in order and copy-paste the commands. Every command is shown exactly as you should type it.

> **Already know the basics and just want the reference?** The deep technical version of this guide (topology diagrams, migration theory, scaling math, incident playbooks) lives in [docs/22-deployment-and-operations.md](docs/22-deployment-and-operations.md). This document is the practical "do this, then this" version.

---

## Table of contents

- [How this project is put together](#how-this-project-is-put-together)
- [Part A — Development (run it on your computer)](#part-a--development-run-it-on-your-computer)
- [Part B — Production (run it on a cloud Ubuntu server)](#part-b--production-run-it-on-a-cloud-ubuntu-server)
- [Everyday production operations](#everyday-production-operations)
- [Security checklist before you go live](#security-checklist-before-you-go-live)
- [Troubleshooting](#troubleshooting)

---

## How this project is put together

These run together:

| Piece | What it is | In development | In production |
|---|---|---|---|
| **The API** | The actual application (Python/FastAPI) | Runs directly on your computer with `python` | Runs inside a Docker container |
| **PostgreSQL** | The database | Runs inside Docker | Runs inside Docker |
| **Redis** | A fast in-memory cache (login attempt counters, rate limits) and the background job queue | Runs inside Docker | Runs inside Docker |
| **The worker** | A separate process that ingests uploaded documents in the background (Celery) | Runs directly on your computer with `celery` | Runs as its own container/service |
| **Qdrant** | The vector database that makes document search work | Runs inside Docker | Your own self-hosted server |

In development, only the databases and cache run in Docker — you run the API and worker directly so you can see errors instantly and restart them in a second. In production, **everything** runs in Docker, including the API itself, because that's what makes it reproducible and safe to deploy on a server you don't sit in front of.

> **The worker is genuinely separate from the API.** They share code and configuration but are two processes, and neither starts the other. If you run the API alone, document uploads will succeed and then sit on "Processing" forever, because nothing is there to do the work.

---

## Part A — Development (run it on your computer)

### What you need installed first

| Tool | Why | Check you have it |
|---|---|---|
| **Docker Desktop** (Windows/Mac) or **Docker Engine** (Linux) | Runs PostgreSQL and Redis for you, no manual install | `docker --version` |
| **Python 3.13 or newer** | Runs the application | `python3 --version` |
| **Git** | Downloads the code | `git --version` |

If any of those commands fail, install the tool first:
- Docker Desktop: https://www.docker.com/products/docker-desktop/
- Python: https://www.python.org/downloads/
- Git: https://git-scm.com/downloads

> **Windows users:** run all the commands below in **Git Bash** (installed together with Git) or **WSL**, not the plain Windows Command Prompt — the commands use Linux-style syntax. If a command below says `python3` and your terminal replies `command not found`, use `python` instead — some Windows Python installs only register that name.

### Step 1 — Get the code

```bash
git clone <this-repository-url>
cd ai_agent_by_Claude
```

(If you already have the folder, just `cd` into it — you can skip cloning.)

### Step 2 — Start the database, cache and vector store

This project ships a ready-made Docker Compose file that starts PostgreSQL, Redis and Qdrant for you, with the correct database roles already configured (this project uses PostgreSQL's Row-Level Security, so the setup is a little more specific than a stock database):

```bash
docker compose -f docker-compose.dev.yml up -d
```

Check both are healthy:

```bash
docker compose -f docker-compose.dev.yml ps
```

You should see `postgres`, `redis` and `qdrant` all listed as `running` (or `healthy`). Leave them running — you don't need to restart them every time you change code.

> **Keep the Qdrant versions in step.** The Python client refuses to talk to a server more than one minor version away and warns loudly. If you bump `qdrant-client` in `pyproject.toml`, bump the image tag in `docker-compose.dev.yml` to match, and vice versa.

### Step 3 — Set up Python

Create an isolated Python environment (a "virtual environment" keeps this project's packages separate from everything else on your machine) and install the project into it:

```bash
python3 -m venv .venv
```

Activate it:

```bash
# Linux / macOS / Git Bash on Windows
source .venv/bin/activate

# Windows PowerShell
.venv\Scripts\Activate.ps1
```

You'll know it worked because your terminal prompt now starts with `(.venv)`. Now install the project and its development tools (tests, linter, type checker):

```bash
pip install -e ".[dev]"
```

This step takes a minute or two the first time.

### Step 4 — Create your settings file

The application reads its configuration from a file called `.env` in the project root. A template already exists — copy it:

```bash
cp .env.example .env
```

Now you need to fill in three things that don't have safe defaults: a JWT signing keypair (used to issue login tokens) and a data-encryption key (used to protect stored AI-provider secrets like OpenAI API keys). Nothing here needs to be memorable — you generate it once and paste it in.

**4a. Generate a JWT keypair:**

```bash
openssl genrsa -out jwt_private.pem 2048
openssl rsa -in jwt_private.pem -pubout -out jwt_public.pem
```

These two commands need to go into `.env` as **single-line** values (the `.env` format doesn't support multi-line values), so the actual line breaks in the file get replaced with the two characters `\n`. This little script does that for you and prints what to paste:

```powershell
@'
for name, field in [
    ("jwt_private.pem", "JWT__PRIVATE_KEY_PEM"),
    ("jwt_public.pem", "JWT__PUBLIC_KEY_PEM"),
]:
    with open(name, encoding="utf-8") as f:
        value = f.read().strip().replace("\n", "\\n")

    print(f"{field}={value}")
    print()
'@ | python
```

Copy each printed line into `.env`, replacing the existing empty `JWT__PRIVATE_KEY_PEM=` and `JWT__PUBLIC_KEY_PEM=` lines.

**4b. Generate the encryption key:**

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Copy the printed value into `.env` as the value of `ENCRYPTION__DATA_KEY=`.

**4c. Check the database/Redis lines match Step 2.** The rest of `.env.example`'s defaults already match `docker-compose.dev.yml`, so you shouldn't need to touch anything else — but it's worth glancing at these lines to confirm:

```
DATABASE__HOST=localhost
DATABASE__PORT=55432
REDIS__URL=redis://localhost:56379/0
```

Everything else in `.env.example` (OAuth login, rate limits, log level) has a working default. Leave it as-is for now — you can revisit `docs/21-configuration-and-secrets.md` later if you want social login (Google/Facebook) working locally.

### Step 5 — Create the database tables

The project uses [Alembic](https://alembic.sqlalchemy.org/) to manage the database schema. Run this once (and again any time you pull code that adds a new migration):

```bash
python -m alembic upgrade head
```

If this succeeds silently (or prints a line ending in `... done`), your database now has all the required tables.

### Step 6 — Run the server

```bash
python -m iam_platform.asgi
```

You should see log lines confirming the server started, ending with something like `Uvicorn running on http://0.0.0.0:8000`.

> **Note on restarting:** this project doesn't have "hot reload" wired up yet — after you change code, stop the server (`Ctrl+C`) and run the command again to pick up your changes.

#### And the background worker, in a terminal of its own

Document ingestion — parsing an uploaded PDF, splitting it into chunks, turning those into embeddings and indexing them — runs outside the API so a large upload never blocks a web request. That work happens in a **Celery worker**, which you start yourself:

```bash
celery -A iam_platform.workers.main:celery_app worker --loglevel=info --pool=threads --concurrency=4
```

Leave it running alongside the API. It reads the same `.env`, so there is nothing extra to configure — but it does need `OPENAI__API_KEY` and `QDRANT__URL` to be set, because embedding and indexing are the work it does.

> **Mind the double underscore.** It is `OPENAI__API_KEY`, not `OPENAI_API_KEY` — the `__` is what nests the value under the `openai` settings group. The single-underscore form is the name OpenAI's own SDK uses, so it is an easy mistake, and it used to be swallowed silently: no error, and no key. The app now refuses to start and tells you the correct spelling.

**The first PDF, Word, Excel, PowerPoint or image you upload will be slow** — a minute or two. Docling downloads its layout models from Hugging Face the first time it parses one, and caches them under `~/.cache/huggingface` for every run after. CSV, JSON and XML need no models and are fast from the start. (The production image bakes those models in at build time, so this only affects local development.)

You can skip it if you're only working on identity, authorization or tenant administration; nothing in those areas touches it. You'll notice its absence the moment you upload a document: the upload succeeds, and the document then stays on **Processing** indefinitely.

### Step 7 — Check it's working

Open a **second terminal** (leave the server running in the first one) and try:

```bash
curl http://localhost:8000/livez
# {"status":"alive"}

curl http://localhost:8000/readyz
# {"status":"ready","dependencies":[...]}
```

If `/readyz` reports `"ready"` with all dependencies `"healthy": true`, everything is wired up correctly — the API, database, and cache are all talking to each other.

You can also browse to **http://localhost:8000/docs** in your web browser for the interactive API documentation (Swagger UI), where you can try out every endpoint directly.

### Step 8 — Create your platform administrator account

You can register an account through the API (or the admin console you'll start in Step 9), but a freshly registered account has **no permissions at all** — and it's not something you can fix by registering a "better" account either. Every platform-role grant in this system is gated by a self-escalation guard: an actor can only grant permissions they already hold. That's exactly the right rule for day-to-day use, but it means the *very first* platform administrator can never be created through the API — nobody holds any platform permission yet to grant one.

There's a second, unrelated problem in the way too: registration normally requires clicking an emailed verification link, but this project's email sender only ever logs "email queued" to the console — there is no real email provider wired in yet (see the [Known gaps](docs/22-deployment-and-operations.md#known-gaps)), in **every** environment including production. So a freshly registered account can't even verify itself yet.

`scripts/bootstrap_platform_admin.py` handles both problems in one step. It runs directly against the database with the same table-owner credentials Alembic migrations use — a deliberate, one-time bypass of the normal API authorization path, not a backdoor left lying around for routine use.

First, register the account you want to make an admin (through `/register` in the console once you've started it in Step 9, or directly against the API):

```powershell
Invoke-RestMethod `
  -Method POST `
  -Uri "http://localhost:8000/v1/auth/register" `
  -ContentType "application/json" `
  -Body '{
    "email": "admin@lait.co.uk",
    "password": "Correct-Horse-9!"
  }'
```

Then bootstrap it:

```powershell
python scripts/bootstrap_platform_admin.py admin@lait.co.uk
```

This activates the account (skipping the email verification step that can't be completed yet) and grants it a `platform_super_admin` role holding every platform permission: `platform.tenants.create`, `platform.tenants.suspend`, `platform.support.impersonate`, `platform.users.read`, and `platform.users.manage`. It's safe to re-run — running it again against an account that already holds the role just confirms that and does nothing else, and re-running after an upgrade picks up any permissions added since. You can now log in as this account, either via `/v1/auth/login` directly or through the admin console.

**Now seed the tenant role catalog too — this step is easy to miss and the console breaks in a non-obvious way without it:**

```powershell
python scripts/bootstrap_tenant_catalog.py
```

`bootstrap_platform_admin.py` only seeds the *platform*-scope catalog (fully separate tables, by design). Nothing seeds the *tenant*-scope one — `tenant_permissions` and the built-in Tenant Owner / Tenant Administrator / Member roles — until you run this. Skip it, and creating a tenant still "succeeds": the tenant exists, the owner has an active membership, but the console can't find a "Tenant Owner" role to grant them, so they land with **zero tenant permissions and a nearly-empty sidebar** (just Dashboard, Assistants, Knowledge bases, Conversations — the screens that don't need a permission). As of this fix, `CreateTenant` refuses outright with a 503 if you skip this step and try to create a tenant anyway, rather than succeeding silently. Also idempotent — safe to re-run.

### Step 9 — Run the admin console (frontend)

The backend is a headless API — day-to-day administration (managing tenants, roles, members, AI resources) is done through the Next.js admin console in `frontend/`. In a **third terminal** (leave the backend running in the first):

```bash
cd frontend
npm install
npm run dev
```

Browse to **http://localhost:3000** and log in with the account you bootstrapped in Step 8. See [frontend/README.md](frontend/README.md) for the console's architecture (it never exposes your JWT to the browser — it proxies every request through a same-origin BFF route that holds tokens in an `httpOnly` cookie).

**Where you land depends on who you are.** `/` routes you by your actual permissions: a platform administrator goes to **/platform** (which works with zero tenants — it's where you create the first one), someone with exactly one tenant goes straight to that tenant's dashboard, and anyone else gets the tenant picker. The sidebar shows every scope you hold, so a platform admin who is also a tenant member sees both sections at once.

**Working with an empty database is expected on a fresh install.** With no tenants yet, go to **Platform → Tenants → New tenant**. The form derives the URL slug from the organization name (you can override it) and warns before you submit if that slug is taken; the owner is chosen from a searchable list of users, not by pasting a UUID. If you need a user to own it first, create one under **Platform → Users → New user**.

> **Want a populated console to explore instead?** `python scripts/seed_demo_data.py admin@lait.co.uk` (run from the repo root, after Step 8) creates a demo tenant with three members, two AI assistants, and a knowledge base.

#### What you can do from the console

> **New to this system?** [docs/23-admin-console-guide.md](docs/23-admin-console-guide.md) is a
> plain-language tour of every screen — what each one is, what it's for, and the platform-vs-tenant
> distinction that explains the whole layout. Read that rather than guessing from the table below.

| Screen | What it manages |
|---|---|
| **Platform → Overview** | Tenant/user/role/permission counts and your own resolved platform authority |
| **Platform → Tenants** | Create (auto-slug + user picker), suspend with a reason, reactivate |
| **Platform → Users** | Create, search, rename, suspend/reactivate, soft-delete; per-user detail showing platform roles, resolved permissions and tenant memberships, with inline role grant/revoke |
| **Platform → Roles / Permissions** | The platform catalog, risk-summarised, plus a role → permission matrix |
| **Tenant → Members / Roles & permissions** | Invitations, membership lifecycle, custom roles, hierarchy, allow/deny overrides |
| **Account → My identity** | Your own profile, password change, TOTP enrollment, linked providers, sign out everywhere |

Suspending or deleting a user takes effect immediately — every session is revoked and they cannot sign in again until reactivated. Deletion is a soft delete: the account disappears from the directory and can no longer authenticate, but the row is kept because audit history references it.

### Step 10 — Run the automated tests (optional but recommended)

```bash
python -m pytest
```

This runs the full test suite (hundreds of tests) against your local database and Redis.

**Expect around 20 minutes, and don't mistake the quiet for a hang.** The HTTP-level tests each build a complete application — two database connection pools, a Redis pool, an HTTP client — and then wipe every table on the way out, so a single one costs 10–20 seconds before any of its own work happens. That's deliberate: it's what makes them exercise the real request path instead of a mock. It also means the suite spends most of its time waiting rather than computing, so it looks idle while it's working, and progress output is buffered when you redirect it to a file.

**Never run two copies at once.** They compete for the same database and each other's teardown truncations, and the whole thing slows to a crawl — which reads exactly like a deadlock and isn't one.

For a quick check while you're editing code, the subset that doesn't touch the database finishes in seconds:

```bash
python -m pytest -m "not integration"
```

> Never run the full suite against a database you care about — teardown truncates every table. Stop the API server first, too: the suite and a running server compete for the same Postgres and Redis, and both start timing out.

### Everyday development workflow

Once set up, your day-to-day loop is:

1. Make sure Postgres/Redis/Qdrant are running: `docker compose -f docker-compose.dev.yml up -d` (only needed if you'd stopped them)
2. Activate your virtual environment: `source .venv/bin/activate`
3. Run the server: `python -m iam_platform.asgi`
4. If you're working on document ingestion: `celery -A iam_platform.workers.main:celery_app worker --loglevel=info --pool=threads --concurrency=4` in its own terminal
5. If you're working on the admin console too: `cd frontend && npm run dev` in its own terminal
6. Edit code, stop the affected process (`Ctrl+C`), run it again to see your change — the frontend's `npm run dev` uses Turbopack and hot-reloads on its own
7. When you're done for the day: `docker compose -f docker-compose.dev.yml down` (stops the containers; add `-v` only if you also want to wipe the database data)

---

## Part B — Production (run it on a cloud Ubuntu server)

This section assumes you have a fresh Ubuntu server from a cloud provider (AWS, DigitalOcean, Hetzner, Linode, Azure, etc. — any of them work identically from here on) and can connect to it over SSH.

**What you need before starting:**

- An Ubuntu 22.04 or 24.04 server with at least 2 GB RAM
- SSH access to it (`ssh youruser@your-server-ip`)
- (Optional but recommended) a domain name pointed at the server's IP address, for HTTPS
- An AWS account — explained in Step 4 below, and needed for one specific reason: **production secrets are stored in AWS Secrets Manager**, not in plain text on the server. This is a deliberate security choice this project makes (see the box below). Your server itself does **not** need to be hosted on AWS — Secrets Manager is reachable from any cloud.

> **Why AWS Secrets Manager specifically, even if my server isn't on AWS?**
> This project refuses to start in production mode with secrets sitting in plain environment variables — that's an intentional safety rail, not a bug. Today, AWS Secrets Manager is the only "real" secret store this project knows how to talk to (support for HashiCorp Vault, Azure Key Vault, and Google Secret Manager is designed for but not yet built — see `docs/22-deployment-and-operations.md`). Using AWS Secrets Manager just means your server makes API calls to AWS to fetch secrets at startup; it costs a few cents a month and takes about 10 minutes to set up (Step 4 walks through it). If your organization already has Vault or another secret manager and wants it supported, that's a small, well-contained engineering task — the integration point is one file (`src/iam_platform/infrastructure/secrets/`).

### Step 1 — Basic server setup

SSH into your server as root (or your initial user), then:

```bash
# Update the system
sudo apt update && sudo apt upgrade -y

# Create a non-root user to work as (skip if you already have one)
sudo adduser deploy
sudo usermod -aG sudo deploy

# Switch to it
su - deploy
```

Set up a basic firewall — only allow SSH for now, we'll open web ports later:

```bash
sudo ufw allow OpenSSH
sudo ufw enable
```

### Step 2 — Install Docker

```bash
# Install Docker's official install script
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
rm get-docker.sh

# Let your user run docker without typing sudo every time
sudo usermod -aG docker $USER
```

**Log out and log back in** (or run `newgrp docker`) for the group change to take effect. Confirm it worked:

```bash
docker --version
docker compose version
```

Both should print a version number with no errors.

### Step 3 — Get the code onto the server

```bash
git clone <this-repository-url>
cd ai_agent_by_Claude
```

### Step 4 — Set up your production secrets

This is the step with the most moving parts, so it's broken into small pieces. You're creating six pieces of secret material:

1. A password for the database's admin (superuser) role
2. A password for the application's normal database role (`app_tenant`)
3. A password for the application's platform database role (`app_platform`)
4. A JWT signing keypair (same as in development, Step 4a — generate a fresh one, don't reuse your dev keys)
5. A data-encryption key (same as in development, Step 4b — generate a fresh one)
6. AWS credentials that let the server fetch secrets from AWS Secrets Manager

**4a. Generate three strong database passwords:**

```bash
openssl rand -base64 32   # run this three times, write down each result
```

**4b. Generate a fresh JWT keypair** (exactly like development Step 4a, but do this again — don't copy your dev keys to production):

```bash
openssl genrsa -out jwt_private.pem 2048
openssl rsa -in jwt_private.pem -pubout -out jwt_public.pem

python3 - <<'PY'
for name in ["jwt_private.pem", "jwt_public.pem"]:
    with open(name) as f:
        print(name, "->", f.read().strip().replace("\n", "\\n"))
    print()
PY
```

Keep the two printed values handy — you'll paste them in Step 4e.

**4c. Generate a fresh encryption key:**

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

**4d. Create an AWS account and an IAM user for Secrets Manager.** If you already have an AWS account, skip to creating the IAM user.

1. Sign up at https://aws.amazon.com/ if you don't already have an account.
2. In the AWS Console, go to **IAM → Users → Create user**. Name it something like `iam-platform-prod`.
3. Attach the permission `SecretsManagerReadWrite` (or, for tighter security later, a custom policy scoped to just the secrets you create below).
4. Under **Security credentials** for that user, create an **Access key** (choose "Application running outside AWS" as the use case). Save the **Access Key ID** and **Secret Access Key** it shows you — this is the only time AWS displays the secret key.
5. Go to **Secrets Manager → Store a new secret** and create one secret per value from steps 4a–4c above (six secrets total). Name them clearly, e.g.:
   - `prod/iam-platform/db-superuser-password`
   - `prod/iam-platform/db-app-tenant-password`
   - `prod/iam-platform/db-app-platform-password`
   - `prod/iam-platform/jwt-private-key`
   - `prod/iam-platform/jwt-public-key`
   - `prod/iam-platform/encryption-data-key`

   For each, choose "Other type of secret" and paste the plain value (the raw password, or the `\n`-escaped PEM line, or the Fernet key) as **plaintext**, not as key/value JSON.

Note the exact secret names — you'll need them in the next step, prefixed with `secret://`.

**4e. Create the production `.env` file.** This file is read by Docker Compose to fill in the values in `docker-compose.prod.yml` — it is **not** the same kind of file as the development `.env`. Create it in the project root:

```bash
nano .env
```

Paste this in, replacing every placeholder with your real values from steps 4a–4d:

```bash
# --- Database passwords (plain values — these two containers, Postgres
# itself and the one-time migration job, run before secrets can be fetched
# from AWS, so they take the raw password directly) ---
POSTGRES_SUPERUSER_PASSWORD=paste-your-superuser-password-here
APP_TENANT_PASSWORD=paste-your-app-tenant-password-here
APP_PLATFORM_PASSWORD=paste-your-app-platform-password-here

# --- Tell the app to fetch its own secrets from AWS Secrets Manager ---
SECRET_PROVIDER=aws_secrets_manager
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=paste-your-access-key-id-here
AWS_SECRET_ACCESS_KEY=paste-your-secret-access-key-here

# --- These are REFERENCES to the secrets you created in AWS, not the
# secrets themselves -- replace the names if you named yours differently ---
JWT_PRIVATE_KEY_PEM=secret://prod/iam-platform/jwt-private-key
JWT_PUBLIC_KEY_PEM=secret://prod/iam-platform/jwt-public-key
ENCRYPTION_DATA_KEY=secret://prod/iam-platform/encryption-data-key

# --- AI answering ---
# Only needed if you use the knowledge-base / chat features.
# CHAT_REASONING_EFFORT matters more than it looks: on a reasoning model, left
# unset the model decides how long to think and occasionally spends ten seconds
# on a question -- emitting nothing at all meanwhile, so a visitor watches an
# empty chat bubble. Measured on gpt-5.5: unset gave a 2.11s median and a
# 10.80s worst case; "low" gave 1.24s and 1.58s. Leave it BLANK on a
# non-reasoning model, which rejects the parameter outright.
OPENAI__CHAT_REASONING_EFFORT=low

# --- Everything else ---
CORS_ALLOWED_ORIGINS=["https://yourdomain.com"]

# Where third-party websites reach this API. Only used to build the one-line
# <script> tag tenants paste into their own sites for the chat widget. Set it
# if you use that feature and sit behind a reverse proxy -- left empty, the API
# guesses from the incoming request's Host, which a proxy may not preserve, and
# tenants would be handed a snippet pointing at the wrong hostname.
PUBLIC_API_BASE_URL=https://api.yourdomain.com

LOG_LEVEL=INFO
```

Save and exit (in `nano`: `Ctrl+O`, Enter, `Ctrl+X`).

Lock the file down so only you can read it:

```bash
chmod 600 .env
```

> **Why are the three database passwords plain values instead of `secret://` references, when everything else is a reference?** The database container and the one-off migration job both start up *before* the application's own secret-fetching code runs, so they can't fetch anything from AWS yet — they need their password directly. This `.env` file, protected with `chmod 600` and never committed to git (already covered by `.gitignore`), is the one place those three values live in plain text. Everything the long-running API process touches (the JWT keys, the encryption key, and its own two database passwords `DATABASE__PASSWORD`/`DATABASE__PLATFORM_PASSWORD`, which are set from the same `APP_TENANT_PASSWORD`/`APP_PLATFORM_PASSWORD` values in the compose file) is resolved through this project's `secret://` mechanism instead.

### Step 5 — Build and start everything

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

This does three things in order:
1. Builds the application's Docker image
2. Starts PostgreSQL and Redis, waits until they're healthy
3. Runs the database migration as a one-time job, then starts the API — the API waits for the migration to finish successfully before it starts

The first run takes a few minutes (downloading base images, building). Watch it happen:

```bash
docker compose -f docker-compose.prod.yml logs -f
```

Press `Ctrl+C` to stop watching (this does **not** stop the containers).

### Step 6 — Verify it's working

```bash
curl http://localhost:8000/readyz
```

You should see `{"status":"ready", ...}`. If you see `503` or `"not_ready"`, check the logs:

```bash
docker compose -f docker-compose.prod.yml logs api
docker compose -f docker-compose.prod.yml logs migrate
```

### Step 7 — Create your platform administrator account

Same underlying reason as in development (Part A, Step 8): the self-escalation guard means nobody can grant the *first* platform role through the API — nobody holds any platform permission yet to grant one — and this project's email sender doesn't deliver real mail in any environment yet, so a freshly registered account can't self-verify either. `scripts/bootstrap_platform_admin.py` (now baked into the image, see the Dockerfile) handles both by running directly against the database with the migrator role's credentials — the same authority the migration job already uses, and the same one-time, deliberate bypass as in development.

Register the account you want to make an admin:

```bash
curl -X POST https://yourdomain.com/v1/auth/register \
  -H "content-type: application/json" \
  -d '{"email": "you@yourdomain.com", "password": "a-strong-unique-password"}'
```

(If you haven't put HTTPS in front of the API yet — that's the next step — substitute `http://localhost:8000` here.)

Then bootstrap it, running the script inside a one-off container that reuses the `migrate` service's existing database credentials:

```bash
docker compose -f docker-compose.prod.yml run --rm migrate python scripts/bootstrap_platform_admin.py you@yourdomain.com
```

This activates the account and grants it a `platform_super_admin` role (`platform.tenants.create`, `platform.tenants.suspend`, `platform.support.impersonate`). It's idempotent — safe to re-run against an account that already holds the role, it'll just report that and do nothing else.

**Now seed the tenant role catalog too, the same way:**

```bash
docker compose -f docker-compose.prod.yml run --rm migrate python scripts/bootstrap_tenant_catalog.py
```

This step is easy to miss and the failure mode isn't obvious: `bootstrap_platform_admin.py` only seeds the *platform*-scope catalog (fully separate tables, by design). Nothing seeds the *tenant*-scope one — `tenant_permissions` and the built-in Tenant Owner / Tenant Administrator / Member roles — until this runs. Skip it, and creating a tenant still "succeeds": the tenant exists, the owner has a membership, but there's no "Tenant Owner" role to grant them, so they land with zero tenant permissions and a nearly-empty console. `CreateTenant` now refuses outright (503) if you try to create a tenant before this step has run, rather than succeeding silently. Also idempotent — safe to re-run.

> **The admin console (`frontend/`) isn't part of this production compose setup.** This guide's `docker-compose.prod.yml` only builds and runs the backend API — there's no frontend service, Dockerfile, or reverse-proxy entry for it here yet. To run it against this server, build it separately (`cd frontend && npm run build && npm run start`, or deploy it to any Node/Next.js host) with `BACKEND_API_URL` pointed at this server's API origin, and put it behind its own domain or path in Nginx. Until then, `https://yourdomain.com/docs` (Step 6/8) and direct API calls are how you administer this deployment.

### Step 8 — Put a real domain and HTTPS in front of it

Right now the API is only reachable on port 8000, without encryption. For a real deployment you want a domain name with HTTPS, using **Nginx** as a reverse proxy and **Let's Encrypt** (via Certbot) for a free TLS certificate.

**8a. Install Nginx and Certbot:**

```bash
sudo apt install -y nginx certbot python3-certbot-nginx
```

**8b. Point your domain at the server.** In your domain registrar's DNS settings, create an **A record** for `yourdomain.com` (and/or `api.yourdomain.com`) pointing at your server's public IP address. This can take a few minutes to a few hours to propagate.

**8c. Create an Nginx site config:**

```bash
sudo nano /etc/nginx/sites-available/iam-platform
```

Paste this in (replace `yourdomain.com` with your real domain):

```nginx
server {
    listen 80;
    server_name yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Don't expose metrics to the public internet -- see the security
    # checklist below.
    location /metrics {
        deny all;
    }
}
```

Enable it:

```bash
sudo ln -s /etc/nginx/sites-available/iam-platform /etc/nginx/sites-enabled/
sudo nginx -t   # checks the config for typos
sudo systemctl reload nginx
```

**8d. Get a free HTTPS certificate:**

```bash
sudo certbot --nginx -d yourdomain.com
```

Certbot will ask for an email address (for renewal notices) and automatically edit your Nginx config to redirect HTTP to HTTPS. Certificates auto-renew; you don't need to do anything further.

**8e. Now that Nginx is your public entry point, stop exposing port 8000 to the whole internet.** Edit `docker-compose.prod.yml` and change the `api` service's port line from:

```yaml
    ports:
      - "8000:8000"
```

to:

```yaml
    ports:
      - "127.0.0.1:8000:8000"
```

This makes port 8000 reachable only from the server itself (which is exactly what Nginx needs) and unreachable from the outside world directly. Apply it:

```bash
docker compose -f docker-compose.prod.yml up -d
```

### Step 9 — Lock down the firewall

Now that Nginx is handling public traffic, open only the ports the world actually needs:

```bash
sudo ufw allow 'Nginx Full'   # opens 80 and 443
sudo ufw status
```

Port 8000 should **not** appear in `ufw status` as allowed — it's only reachable locally now (Step 8e), and even if it were, the firewall wouldn't let outside traffic reach it either. This is defense in depth: two independent reasons port 8000 isn't publicly reachable.

### Step 10 — Confirm auto-start on reboot

Docker itself starts on boot by default after installation (Step 2). The containers are configured with `restart: unless-stopped`, so they come back up automatically after a server reboot or crash — you don't need to set up anything extra. You can verify Docker's boot-start is enabled with:

```bash
sudo systemctl is-enabled docker
# should print: enabled
```

**You're live.** Visit `https://yourdomain.com/docs` in a browser to confirm.

---

## Everyday production operations

### Viewing logs

```bash
docker compose -f docker-compose.prod.yml logs -f api
```

Logs are structured JSON, one line per event — pipe through `jq` if you have it installed for easier reading: `... | jq .`

### Checking health

```bash
curl https://yourdomain.com/readyz
```

For metrics (Prometheus format, only reachable from the server itself per the Nginx config above):

```bash
curl http://localhost:8000/metrics
```

### Deploying an update

```bash
cd ai_agent_by_Claude
git pull
docker compose -f docker-compose.prod.yml up -d --build
```

This rebuilds the image, re-runs migrations (skipping any already applied), and restarts the API with zero manual steps. The old container keeps serving traffic until the new one is healthy and ready to take over.

**After an upgrade that adds permissions, re-run the bootstrap script.** New platform permissions don't attach themselves to existing roles, so an administrator who upgraded before `platform.users.read`/`platform.users.manage` existed won't see the Users screen until:

```bash
docker compose -f docker-compose.prod.yml run --rm migrate python scripts/bootstrap_platform_admin.py you@yourdomain.com
```

It's idempotent — safe on every deploy, and a no-op when there's nothing new to grant.

### Backing up the database

```bash
docker compose -f docker-compose.prod.yml exec postgres \
  pg_dump -U postgres iam_platform | gzip > backup-$(date +%F).sql.gz
```

Consider putting this in a daily cron job and copying the resulting file off the server (e.g., to S3 or another remote location) — a backup that lives only on the server it's backing up doesn't protect you if that server is lost.

### Restarting / stopping

```bash
# Restart just the API (e.g. after an .env change)
docker compose -f docker-compose.prod.yml restart api

# Stop everything
docker compose -f docker-compose.prod.yml down

# Stop everything AND delete the database data (careful!)
docker compose -f docker-compose.prod.yml down -v
```

---

## Security checklist before you go live

Go through this list once before pointing real users at the server:

- [ ] `.env` file permissions are `600` (owner-read-only) — `chmod 600 .env`
- [ ] `.env` is not committed to git (`git status` should not show it — it's already in `.gitignore`)
- [ ] `CORS_ALLOWED_ORIGINS` in `.env` lists only your real frontend domain(s), not `*` or `localhost`
- [ ] Port 8000 is bound to `127.0.0.1` only (Step 8e) and is not in `ufw status`'s allowed list
- [ ] `/metrics` is blocked at the Nginx layer (Step 8c) — it has no authentication of its own
- [ ] HTTPS is working (`https://yourdomain.com`, not just `http://`) and HTTP redirects to it
- [ ] You generated **fresh** production secrets rather than reusing development ones
- [ ] The IAM user's AWS permissions are scoped to Secrets Manager only, not full AWS admin access
- [ ] Database backups are running and are copied somewhere other than the server itself
- [ ] You've read the **Known gaps** section of [docs/22-deployment-and-operations.md](docs/22-deployment-and-operations.md#known-gaps) so you know what this project deliberately doesn't do yet (there is no background-job worker, and rotating the JWT key or the encryption key isn't a supported operation yet — plan around both)

---

## Troubleshooting

**A button in the admin console does nothing / an action returns a 500** — if you're on an older checkout, several separate defects caused exactly this and are now fixed: menu items used the wrong Base UI prop and were inert, the console's BFF proxy crashed on every `204 No Content` reply (which is what most successful actions return), permission-denied tenant actions raised an unmapped exception, and creating a *second* tenant for the same owner violated a unique index. Pull the latest code, run `python -m alembic upgrade head`, and restart both the backend and `npm run dev`.

**The console is empty and the sidebar only shows "My identity"** — that's a database with no data plus an account with no permissions. Two common causes: you never ran `scripts/bootstrap_platform_admin.py` (Step 8), or you ran the backend test suite, whose teardown truncates every table — including your admin account and its role grants. Re-run the bootstrap script, then `scripts/seed_demo_data.py` if you want demo content back.

**I created a tenant and its owner's sidebar only shows Dashboard, Assistants, Knowledge bases, and Conversations — no Members, no Roles & permissions, no Provider credentials** — the owner's membership has no role assigned, because `scripts/bootstrap_tenant_catalog.py` (Step 8) was never run and there was no "Tenant Owner" role for `CreateTenant` to grant. Run it now, then assign the role to the affected membership by hand (there's no "repair an existing tenant" button, since this shouldn't happen again — `CreateTenant` now refuses with a 503 instead of creating a tenant this way going forward):

```sql
INSERT INTO tenant_membership_roles (id, tenant_id, membership_id, role_id, granted_by_user_id)
SELECT gen_random_uuid(), '<tenant-id>', '<membership-id>',
       (SELECT id FROM tenant_roles WHERE code = 'tenant_owner' AND tenant_id IS NULL),
       '<your-user-id>';
```

**I upgraded and the Users screen disappeared** — `platform.users.read`/`platform.users.manage` were added after the first release. Re-run `python scripts/bootstrap_platform_admin.py <your-email>`; it's idempotent and picks up permissions added since.

**I registered an account and never got a verification email** — this isn't a misconfiguration on your end. The project's email sender (`ConsoleEmailSender`) only ever logs "email queued" to the console — no real provider (SES, SendGrid, etc.) is wired in yet, in development *or* production. For the platform administrator account, `scripts/bootstrap_platform_admin.py` (Part A Step 8 / Part B Step 7) activates the account directly and sidesteps this entirely. For any other account, someone holding database access has to flip that user's `status` to `'active'` by hand until a real email provider is added — see [docs/22-deployment-and-operations.md](docs/22-deployment-and-operations.md#known-gaps).

**`docker compose` says "command not found"** — you have an old Docker install with the separate `docker-compose` (with a hyphen) tool. Either install the current Docker (Step 2 does this correctly) or substitute `docker-compose` for `docker compose` throughout — the commands are otherwise identical.

**`/readyz` returns 503 / `"not_ready"`** — one of Postgres or Redis isn't reachable yet. Check `docker compose ... ps` to confirm both show as healthy, and check `docker compose ... logs postgres redis`.

**Migration container exits immediately with a permission or validation error** — almost always a `.env` value is missing or malformed. Re-check every value in Step 4e is filled in, especially that `SECRET_PROVIDER` is not left as `env` (production refuses to start with that setting on purpose).

**"refusing to start: environment=production requires a real secret provider"** — this is the intentional safety check described above. `SECRET_PROVIDER` must be `aws_secrets_manager` (with working AWS credentials) whenever `ENVIRONMENT=production`.

**Can't reach the AWS secret ("SecretNotFoundError" in the logs)** — double-check the secret name after `secret://` in `.env` exactly matches the name you gave it in the AWS Console (Step 4d), and that the IAM user's access key in `.env` has permission to read it.

**Changes to `.env` don't seem to apply** — Docker Compose only re-reads environment variables when a container restarts, not automatically. Run `docker compose -f docker-compose.prod.yml up -d` again after editing `.env`.

**I forgot to save my JWT keys / encryption key somewhere safe** — if you still have the running containers, the values are in your `.env` file. If you've lost both, you'll need to generate new ones and restart the API; note that this invalidates every currently logged-in user's session, and previously-encrypted provider credentials become unreadable if the encryption key specifically is lost (there is currently no key-rotation procedure — see the checklist above).

**Still stuck?** The deep-dive reference at [docs/22-deployment-and-operations.md](docs/22-deployment-and-operations.md) covers failure modes, scaling, and rollback in more technical detail.
