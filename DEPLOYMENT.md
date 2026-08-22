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

Six pieces run together, and **both development and production run all six in Docker** — the same `docker compose` model, just two different files (`docker-compose.dev.yml` vs `docker-compose.prod.yml`) with different port numbers and security settings:

| Piece | What it is |
|---|---|
| **`api`** | The application itself (Python/FastAPI) |
| **`worker`** | A separate process that ingests uploaded documents in the background (Celery) — genuinely separate from the API: they share code but neither starts the other, so if it isn't running, uploads succeed and then sit on "Processing" forever |
| **`migrate`** | Creates/updates the database tables, then exits — a one-time job, not a long-running service |
| **`postgres`** | The database |
| **`redis`** | Cache, login-attempt/rate-limit counters, and the job queue the worker consumes |
| **`qdrant`** | Vector search over uploaded documents, for the knowledge-base / chat features |

**Development and production differ only in where things are reachable from and what secrets look like.** In development, Postgres/Redis/Qdrant/the API are each published to `localhost` on an easy-to-remember port so you can poke at them directly; secrets are throwaway values in a git-ignored `.env`. In production, nothing but the API is reachable at all (and only from the server itself — Nginx is what the internet actually talks to), and secrets are real.

> **Prefer to run the API and worker natively with `python`/`celery` instead of in Docker** — for a debugger attached, or faster edit-reload? That is still fully supported; see **"Running natively instead of Docker"** at the end of Part A. The Docker path below is the one actually exercised end-to-end and is what this guide recommends by default.

---

## Part A — Development (run it on your computer)

### What you need installed first

| Tool | Why | Check you have it |
|---|---|---|
| **Docker Desktop** (Windows/Mac) or **Docker Engine** (Linux) | Runs everything — the database, cache, vector store, API and worker | `docker --version` |
| **Git** | Downloads the code | `git --version` |
| **Node.js 20+** | Runs the admin console (Step 6) | `node --version` |
| **Python 3.13+** | Only needed to run the automated test suite (Step 7) — the app itself runs entirely in Docker | `python3 --version` |

If any of those commands fail, install the tool first:
- Docker Desktop: https://www.docker.com/products/docker-desktop/
- Git: https://git-scm.com/downloads
- Node.js: https://nodejs.org/
- Python: https://www.python.org/downloads/

> **Windows users:** run all the commands below in **Git Bash** (installed together with Git) or **WSL**, not the plain Windows Command Prompt — the commands use Linux-style syntax.

### Step 1 — Get the code

```bash
git clone <this-repository-url>
cd ai_agent_by_Claude
```

(If you already have the folder, just `cd` into it — you can skip cloning.)

### Step 2 — Create your settings file

Everything — the app and Docker Compose's own setup — reads its configuration from one file called `.env` in the project root. A template already exists — copy it:

```bash
cp .env.example .env
```

Two things need real values before anything will start: a JWT signing keypair (used to issue login tokens) and a data-encryption key (used to protect stored AI-provider secrets like OpenAI API keys). Nothing here needs to be memorable — you generate each one once and paste it in.

**2a. Generate a JWT keypair:**

```bash
openssl genrsa -out jwt_private.pem 2048
openssl rsa -in jwt_private.pem -pubout -out jwt_public.pem
```

These need to go into `.env` as **single-line** values (the `.env` format doesn't support real line breaks inside a value), so the actual line breaks get replaced with the two characters `\n`. This little script does that for you and prints what to paste:

```bash
python3 - <<'PY'
for name, field in [
    ("jwt_private.pem", "JWT__PRIVATE_KEY_PEM"),
    ("jwt_public.pem", "JWT__PUBLIC_KEY_PEM"),
]:
    with open(name, encoding="utf-8") as f:
        value = f.read().strip().replace("\n", "\\n")
    print(f"{field}={value}")
    print()
PY
```

Copy each printed line into `.env`, replacing the existing empty `JWT__PRIVATE_KEY_PEM=` and `JWT__PUBLIC_KEY_PEM=` lines. Then delete the `.pem` files — the key belongs only in `.env` now:

```bash
rm -f jwt_private.pem jwt_public.pem
```

**2b. Generate the encryption key:**

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Copy the printed value into `.env` as the value of `ENCRYPTION__DATA_KEY=`.

**2c. Everything else in `.env.example` already has a working default** — the database/Redis/Qdrant ports match what `docker-compose.dev.yml` publishes, and the passwords are throwaway local-only values. Leave it as-is; you can revisit `docs/21-configuration-and-secrets.md` later for things like social login (Google/Facebook).

> **If you replace any of the three database passwords, never use one containing a literal `$`.** Docker Compose interpolates `${VAR}`/`$VAR` patterns wherever it resolves a value from `.env` — including *inside another value*. A password like `my$ecret` gets silently mangled to `my` before it ever reaches Postgres or the app: Compose treats `$ecret` as a reference to an unset variable and drops it, with only a quiet `"ecret" variable is not set` warning buried in the build log to explain why authentication starts failing. `openssl rand -base64 32` (used throughout this guide) never produces `$`, so following the commands as written avoids this entirely. If you generate passwords with something else — a password manager, for instance — strip or avoid `$` in the result.

### Step 3 — Build and start everything

```bash
docker compose -f docker-compose.dev.yml up -d --build
```

This builds the application image once (used by `migrate`, `api` and `worker` alike), starts Postgres/Redis/Qdrant and waits until each is genuinely healthy, runs the database migration as a one-time job, then starts the API and the worker.

> **Expect the first build to take 20–45 minutes**, and to download roughly 2 GB — it installs PyTorch, docling's document-parsing models and a headless browser, so a fresh container never has to fetch them the moment you upload your first document. This is a one-time cost; later runs of this same command finish in well under a minute, because only the small top layer (your source code) needs rebuilding. If a later build unexpectedly re-downloads the whole 2 GB again, something invalidated the dependency layer — check whether `pyproject.toml` changed, since that's the file it's keyed on.

Watch it happen:

```bash
docker compose -f docker-compose.dev.yml logs -f
```

Press `Ctrl+C` to stop watching (this does **not** stop the containers).

### Step 4 — Check it's working

All six services should be listed, and `postgres`/`redis`/`qdrant`/`worker` should show `healthy`; `migrate` should show `Exited (0)`:

```bash
docker compose -f docker-compose.dev.yml ps
```

Then check the API — note the port is **18000**, not 8000: that's where this compose file publishes the API container's internal port 8000 to your machine, to leave 8000 free for anything else you run locally.

```bash
curl http://localhost:18000/livez
# {"status":"alive"}

curl http://localhost:18000/readyz
# {"status":"ready","dependencies":[...]}
```

If `/readyz` reports `"ready"` with every dependency `"healthy": true`, the API, database and cache are all talking to each other. You can also browse to **http://localhost:18000/docs** for the interactive API documentation (Swagger UI).

**Check the worker separately — `/readyz` doesn't cover it.** The API reports healthy whether or not anything is consuming the document-ingestion queue, so a dead worker is invisible from that endpoint alone:

```bash
docker compose -f docker-compose.dev.yml exec worker \
  celery -A iam_platform.workers.main:celery_app inspect ping
```

A `pong` means document ingestion will work. Anything else means uploads will be accepted and then sit on **Processing** forever, with nothing in the API's own log to explain why — check `docker compose -f docker-compose.dev.yml logs worker` instead.

### Step 5 — Create your platform administrator account

You can register an account through the API (or the admin console you'll start in Step 6), but a freshly registered account has **no permissions at all** — and that's not something a "better" registration fixes. Every platform-role grant in this system is gated by a self-escalation guard: an actor can only grant permissions they already hold. That's the right rule for day-to-day use, but it means the *very first* platform administrator can never be created through the API — nobody holds any platform permission yet to grant one.

There's a second, unrelated gap in the way too: registration normally requires clicking an emailed verification link, but this project's email sender only ever logs "email queued" to the console — there's no real email provider wired in yet (see [Known gaps](docs/22-deployment-and-operations.md#known-gaps)), in **every** environment including production. So a freshly registered account can't even verify itself.

`scripts/bootstrap_platform_admin.py` handles both in one step, running directly against the database with the same table-owning credentials Alembic migrations use — a deliberate, one-time bypass of the normal API authorization path.

First, register the account you want to make an admin:

```bash
curl -X POST https://localhost:18000/v1/auth/register \
  -H "content-type: application/json" \
  -d '{"email": "admin@lait.co.uk", "password": "Correct-Horse-9!"}'
```

Then bootstrap it, running the script inside a one-off container that reuses the `migrate` service's database credentials — no local Python setup needed for this step:

```bash
docker compose -f docker-compose.dev.yml run --rm migrate python scripts/bootstrap_platform_admin.py admin@lait.co.uk
```

This activates the account (skipping the email verification that can't be completed yet) and grants it a `platform_super_admin` role: `platform.tenants.create`, `platform.tenants.suspend`, `platform.support.impersonate`, `platform.users.read`, `platform.users.manage`, `platform.model_configurations.manage`. It's idempotent — safe to re-run against an account that already holds the role, and re-running after you pull new code picks up any permissions added since.

**Now seed the tenant role catalog too — this step is easy to miss and the console breaks in a non-obvious way without it:**

```bash
docker compose -f docker-compose.dev.yml run --rm migrate python scripts/bootstrap_tenant_catalog.py
```

`bootstrap_platform_admin.py` only seeds the *platform*-scope catalog (fully separate tables, by design). Nothing seeds the *tenant*-scope one — `tenant_permissions` and the built-in Tenant Owner / Tenant Administrator / Member roles — until you run this. Skip it, and creating a tenant still "succeeds": the tenant exists, the owner has an active membership, but there's no "Tenant Owner" role to grant them, so they land with **zero tenant permissions and a nearly-empty sidebar**. `CreateTenant` refuses outright with a 503 if you try to create a tenant before this has run, rather than succeeding silently. Also idempotent.

> **Want a populated console to explore instead of an empty one?** Once both bootstrap commands above have run:
> ```bash
> docker compose -f docker-compose.dev.yml run --rm migrate python scripts/seed_demo_data.py admin@lait.co.uk
> ```
> creates a demo tenant with three members, two AI assistants, and a knowledge base.

### Step 6 — Run the admin console (frontend)

The backend is a headless API — day-to-day administration (tenants, roles, members, AI resources) is done through the Next.js admin console in `frontend/`. This one piece runs outside Docker, directly with Node:

```bash
cd frontend
cp -n .env.example .env.local   # only copies if .env.local doesn't already exist
npm install
npm run dev
```

**Confirm `frontend/.env.local` points at the right place** before you rely on it — open the file and check it reads:

```
BACKEND_API_URL=http://localhost:18000
```

Not `:8000` — that's the container's *internal* port, nothing on your machine is listening there. Pointing at the wrong port is the single most common reason the console loads but every screen fails with a fetch/connection error.

Browse to **http://localhost:3000** and log in with the account you bootstrapped in Step 5. See [frontend/README.md](frontend/README.md) for the console's architecture (it never exposes your JWT to the browser — it proxies every request through a same-origin BFF route that holds tokens in an `httpOnly` cookie).

**Where you land depends on who you are.** `/` routes you by your actual permissions: a platform administrator goes to **/platform** (which works with zero tenants — it's where you create the first one), someone with exactly one tenant goes straight to that tenant's dashboard, and anyone else gets the tenant picker. The sidebar shows every scope you hold, so a platform admin who is also a tenant member sees both sections at once.

**Working with an empty database is expected on a fresh install.** With no tenants yet, go to **Platform → Tenants → New tenant**. The form derives the URL slug from the organization name (you can override it) and warns before you submit if that slug is taken; the owner is chosen from a searchable list of users, not by pasting a UUID. If you need a user to own it first, create one under **Platform → Users → New user**.

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

### Step 7 — Run the automated tests (optional but recommended)

Unlike every previous step, the test suite runs **natively**, not in a container — it needs a local Python environment, and it talks to the same dockerized Postgres/Redis/Qdrant over the ports Step 3 published.

**7a. Set up a virtual environment, once:**

```bash
python3 -m venv .venv

# Linux / macOS / Git Bash on Windows
source .venv/bin/activate
# Windows PowerShell
.venv\Scripts\Activate.ps1

pip install -e ".[dev]"
```

You'll know activation worked because your terminal prompt now starts with `(.venv)`.

**7b. Create the test database, once.** The suite runs against a completely separate database (`iam_platform_test`) so its teardown — which **truncates every table** — can never touch the data you're working with in `iam_platform`. `docker-compose.dev.yml` only creates `iam_platform` automatically; the test one needs creating the first time:

```bash
docker compose -f docker-compose.dev.yml exec -i postgres \
  psql -U postgres -v ON_ERROR_STOP=1 <<'SQL'
CREATE DATABASE iam_platform_test OWNER postgres;
\c iam_platform_test
GRANT CONNECT ON DATABASE iam_platform_test TO app_tenant;
GRANT USAGE ON SCHEMA public TO app_tenant;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_tenant;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO app_tenant;
GRANT CONNECT ON DATABASE iam_platform_test TO app_platform;
GRANT USAGE ON SCHEMA public TO app_platform;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_platform;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO app_platform;
SQL
```

Then migrate it, the same way Step 3 migrated the main one:

```bash
docker compose -f docker-compose.dev.yml run --rm -e DATABASE__NAME=iam_platform_test migrate
```

> **`-i` on the first command matters.** Without it, `docker compose exec` doesn't forward the SQL on your terminal's input to `psql` at all, and the command silently does nothing — no error, no database.

**7c. Run the suite:**

```bash
python -m pytest
```

This runs the full test suite (hundreds of tests) against `iam_platform_test` and the same dockerized Redis.

**Expect around 20 minutes, and don't mistake the quiet for a hang.** The HTTP-level tests each build a complete application — two database connection pools, a Redis pool, an HTTP client — and then wipe every table in `iam_platform_test` on the way out, so a single test costs 10–20 seconds before any of its own work happens. That's deliberate: it's what makes them exercise the real request path instead of a mock.

**Never run two copies at once.** They compete for the same test database and each other's teardown truncations, and the whole thing slows to a crawl — which reads exactly like a deadlock and isn't one.

For a quick check while you're editing code, the subset that doesn't touch the database finishes in seconds:

```bash
python -m pytest -m "not integration"
```

> Only `iam_platform_test` is ever truncated — your real `iam_platform` database (and everything you've clicked together in the console) is untouched by any of this, precisely because the two live in separate databases.

### Everyday development workflow

Once set up, your day-to-day loop is:

1. Make sure everything's running: `docker compose -f docker-compose.dev.yml up -d` (only needed if you'd stopped it)
2. Edit backend code, then rebuild just the API to see your change: `docker compose -f docker-compose.dev.yml up -d --build api` — fast after the first build, since only your source layer needs rebuilding
3. Editing the worker instead? Same idea: `docker compose -f docker-compose.dev.yml up -d --build worker`
4. The frontend hot-reloads on its own (Turbopack) — no restart needed for console changes
5. Watch logs while you work: `docker compose -f docker-compose.dev.yml logs -f api worker`
6. When you're done for the day: `docker compose -f docker-compose.dev.yml down` (stops the containers; add `-v` only if you also want to wipe all the data, including `iam_platform_test`)

---

### Running natively instead of Docker (optional, advanced)

Useful for attaching a debugger, or for a faster edit-run loop than rebuilding an image each time. This still uses Docker for the three datastores — only the API and worker run directly on your machine.

**Start only the datastores** (not `api`/`worker`/`migrate`):

```bash
docker compose -f docker-compose.dev.yml up -d postgres redis qdrant
```

**Set up Python** (same as Step 7a above if you haven't already):

```bash
python3 -m venv .venv
source .venv/bin/activate   # or .venv\Scripts\Activate.ps1 on Windows
pip install -e ".[dev]"
```

**Create the database tables:**

```bash
python -m alembic upgrade head
```

**Run the server:**

```bash
python -m iam_platform.asgi
```

You should see log lines ending with something like `Uvicorn running on http://0.0.0.0:8000` — native, so it's port **8000** here, not 18000. There's no hot reload wired up; after changing code, stop the server (`Ctrl+C`) and run the command again.

**And the background worker, in a terminal of its own:**

```bash
celery -A iam_platform.workers.main:celery_app worker --loglevel=info --pool=threads --concurrency=4
```

It reads the same `.env`, so there's nothing extra to configure — but it does need `OPENAI__API_KEY` and `QDRANT__URL` set, since embedding and indexing are the work it does. You can skip it if you're only working on identity, authorization or tenant administration; you'll notice its absence the moment you upload a document, which will then sit on **Processing** indefinitely.

**The first PDF, Word, Excel, PowerPoint or image you upload this way will be slow** — a minute or two. Docling downloads its layout models from Hugging Face the first time it parses one and caches them under `~/.cache/huggingface` for every run after. CSV, JSON and XML need no models and are fast from the start. (The Docker image bakes those models in at build time, which is why this only affects the native path.)

**Bootstrap your admin account the same way as Step 5**, just calling the script directly instead of through `docker compose run`:

```bash
python scripts/bootstrap_platform_admin.py admin@lait.co.uk
python scripts/bootstrap_tenant_catalog.py
```

**The admin console (Step 6) is unaffected either way** — point `frontend/.env.local`'s `BACKEND_API_URL` at whichever port your API is actually listening on (`8000` here, `18000` for the Docker path).

---
## Part B — Production (run it on a cloud Ubuntu server)

This section assumes you have a fresh Ubuntu server from a cloud provider (AWS, DigitalOcean, Hetzner, Linode, Azure, etc. — any of them work identically from here on) and can connect to it over SSH.

**What you need before starting:**

- An Ubuntu 22.04 or 24.04 server with at least 2 GB RAM
- SSH access to it (`ssh youruser@your-server-ip`)
- (Optional but recommended) a domain name pointed at the server's IP address, for HTTPS
- An OpenAI API key, if you're using the knowledge-base / chatbot features (Step 4d)

Everything runs on this one server: the API, the background worker, PostgreSQL, Redis, Qdrant, and the uploaded documents themselves.

**What you are about to build — six containers on one machine:**

| Container | What it does | Reachable from outside? |
|---|---|---|
| `api` | Serves the API and the embeddable chat widget | Only through Nginx (bound to `127.0.0.1:8100` by default -- see Step 4d) |
| `worker` | Ingests uploaded documents in the background | No |
| `migrate` | Creates/updates database tables, then exits | No — it is a job, not a service |
| `postgres` | The database | No |
| `redis` | Cache, rate limits, and the job queue | No |
| `qdrant` | Vector search over your documents | No |

Only Nginx (ports 80/443) and SSH face the internet. Everything else talks over a private Docker network.

**The ten steps, at a glance:** prepare the server → install Docker → clone the code → create `.env` with your secrets → build and start → verify → create your admin account → add a domain and HTTPS → close the firewall → confirm it survives a reboot.

**Roughly how long:** 20 minutes of typing, plus **20–45 minutes for the first image build** — it installs PyTorch and bakes in the document-parsing models, which is a one-time cost. Later deploys take about a minute. **No AWS account or other cloud service is required.** Secrets live in a `chmod 600` file on the server; uploaded files live in a Docker volume on its disk. Step 4 explains exactly what that trades away, and what to change if you later want a managed secret store instead.

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

You're creating five pieces of secret material, all of which live in one `chmod 600` file on this server:

1. A password for the database's admin (superuser) role
2. A password for the application's normal database role (`app_tenant`)
3. A password for the application's platform database role (`app_platform`)
4. A JWT signing keypair (generate a fresh one — don't reuse your dev keys)
5. A data-encryption key (fresh, likewise)

> **Where these are kept, and what that costs.** This setup stores them in the `.env` file described below rather than a managed secret manager. The application normally refuses to start in production mode with plaintext secrets — `ALLOW_PLAINTEXT_SECRETS=true` is the deliberate opt-out, and it exists so the refusal still catches the case it was built for: someone reaching production *by accident*, having copied a dev `.env` and changed one line.
>
> What you're accepting: anyone who can read that file — or a backup, disk image or snapshot containing it — holds your JWT signing key and your data-encryption key, and can mint valid tokens for any account. Keep it `chmod 600`, keep it out of backups that travel off this server unencrypted, and treat a compromise of the host as a compromise of every credential. If you later want managed secrets, Step 4 is the only step that changes: set `SECRET_PROVIDER=aws_secrets_manager`, drop `ALLOW_PLAINTEXT_SECRETS`, and replace the values with `secret://` references. Nothing else in this guide changes.

**4a. Generate three strong database passwords:**

```bash
openssl rand -base64 32   # run this three times, write down each result
```

> **Stick with `openssl rand -base64 32` specifically — don't substitute a password manager's generator here.** Docker Compose interpolates `${VAR}`/`$VAR` patterns wherever it resolves a value from `.env`, including *inside another value it's already resolved*. A password containing a literal `$` — common output from many password-manager generators — gets silently truncated at that point: `my$ecret` becomes `my`, with only a `"ecret" variable is not set` warning buried in the build log to explain the resulting authentication failures. Base64's alphabet (letters, digits, `+`, `/`, `=`) never contains `$`, so the command above is immune to this by construction.

**4b. Generate a fresh JWT keypair** (don't copy your dev keys to production):

```bash
openssl genrsa -out jwt_private.pem 2048
openssl rsa -in jwt_private.pem -pubout -out jwt_public.pem

python3 - <<'PY'
for name in ["jwt_private.pem", "jwt_public.pem"]:
    with open(name) as f:
        print(name, "->", f.read().strip().replace("
", "\n"))
    print()
PY
```

The keys must be **single-line** values with real line breaks replaced by the two characters `\n` — that's what the script above prints. Keep both handy for Step 4d.

Once they're pasted into `.env` (Step 4d), delete the `.pem` files — leaving a second copy of the signing key lying in the project directory defeats the point of locking down `.env`:

```bash
shred -u jwt_private.pem jwt_public.pem 2>/dev/null || rm -f jwt_private.pem jwt_public.pem
```

**4c. Generate a fresh encryption key:**

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

If `python3` isn't on the server, generate it after the image is built instead:

```bash
docker compose -f docker-compose.prod.yml run --rm --no-deps migrate   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

**4d. Create the production `.env` file.** This file is read by Docker Compose to fill in the values in `docker-compose.prod.yml` — it is **not** the same kind of file as the development `.env`. Create it in the project root:

```bash
nano .env
```

Paste this in, replacing every placeholder with your real values from steps 4a–4d:

```bash
# --- Database passwords (from Step 4a) ---
POSTGRES_SUPERUSER_PASSWORD=paste-your-superuser-password-here
APP_TENANT_PASSWORD=paste-your-app-tenant-password-here
APP_PLATFORM_PASSWORD=paste-your-app-platform-password-here

# --- Application secrets (from Steps 4b and 4c) ---
# Single-line values: the PEMs must have their line breaks written as the two characters `\n`, which is what the script in Step 4b prints for you.
JWT__PRIVATE_KEY_PEM=-----BEGIN PRIVATE KEY-----\nMIIEvQ...\n-----END PRIVATE KEY-----
JWT__PUBLIC_KEY_PEM=-----BEGIN PUBLIC KEY-----\nMIIBIjA...\n-----END PUBLIC KEY-----
ENCRYPTION__DATA_KEY=paste-your-fernet-key-here

# --- AI answering, document ingestion and search ---
# Required if you use the knowledge-base / chatbot / widget features. Without
# OPENAI__API_KEY, uploads still queue and then fail during embedding, and no
# question can be answered.
OPENAI__API_KEY=sk-paste-your-openai-key-here

# Optional. Reranks search results before they reach the model. Without it,
# passages are ranked by embedding similarity alone -- answers get worse, but
# nothing breaks.
COHERE__API_KEY=

# CHAT_REASONING_EFFORT matters more than it looks: on a reasoning model, left
# unset the model decides how long to think and occasionally spends ten seconds
# on a question -- emitting nothing at all meanwhile, so a visitor watches an
# empty chat bubble. Measured on gpt-5.5: unset gave a 2.11s median and a
# 10.80s worst case; "low" gave 1.24s and 1.58s. Leave it BLANK on a
# non-reasoning model, which rejects the parameter outright.
OPENAI__CHAT_REASONING_EFFORT=low

# Where uploaded document bytes are stored. `local` writes to a Docker volume
# shared by the API and the worker, which is the default and needs nothing
# else set. Use `r2` (Cloudflare R2) if you'd rather not keep them on this
# server's disk, and fill in the four STORAGE__R2_* values from .env.example.
STORAGE__MODE=local

# --- Token identity ---
# Goes into the `iss` claim of every token this deployment issues. Left unset
# the code would default to `example.invalid`, so compose now refuses to start
# without it rather than baking a placeholder into live tokens. Use the URL
# users reach this deployment at.
JWT__ISSUER=https://yourdomain.com

# --- Everything else ---

# Which port on THIS SERVER the API answers on, reachable only from the
# server itself (Nginx connects to it over loopback -- see Step 8). Defaults
# to 8100 if you omit this entirely. Change it only if 8100 is *also* already
# taken on this server -- check first:
#   sudo ss -ltnp | grep 8100
# Nothing on the public internet depends on this number, since Nginx is the
# only thing that ever connects to it; if you do change it, use the same
# value in the Nginx `proxy_pass` line in Step 8 and every `curl` example in
# this guide that targets `localhost:8100`.
API_HOST_PORT=8100

CORS_ALLOWED_ORIGINS=["https://yourdomain.com"]

# Where third-party websites reach this API. Only used to build the one-line
# <script> tag tenants paste into their own sites for the chat widget. Set it
# if you use that feature and sit behind a reverse proxy -- left empty, the API
# guesses from the incoming request's Host, which a proxy may not preserve, and
# tenants would be handed a snippet pointing at the wrong hostname.
#
# Matches CORS_ALLOWED_ORIGINS and the Nginx server_name below: this compose
# setup has no separate frontend service (see the note in Part B), so the API
# itself is what answers at the bare domain -- there is no api. subdomain to
# point at unless you add one yourself, with its own DNS record and Nginx
# server block.
PUBLIC_API_BASE_URL=https://yourdomain.com

LOG_LEVEL=INFO
```

Save and exit (in `nano`: `Ctrl+O`, Enter, `Ctrl+X`).

Lock the file down so only you can read it:

```bash
chmod 600 .env
```

> **Do not copy your development `.env` onto the server and edit it down.** Docker Compose reads this file automatically and any leftover key wins over the compose file's own default — silently. A stray `QDRANT__URL=http://localhost:56333` from a dev machine is the one that bites: the containers start, look healthy, and every knowledge-base search fails, because `localhost` inside a container is the container itself. Start from the block above and add only what you need. To see what the containers will actually receive:
>
> ```bash
> docker compose -f docker-compose.prod.yml config
> ```

> **This file is the whole of your secret store.** It is `chmod 600` and git-ignored (already covered by `.gitignore`), and it is the only copy of your JWT signing key and encryption key. Two consequences worth planning for now rather than discovering later: **back it up somewhere encrypted and off this server** — lose it and every existing session token and every stored provider credential becomes undecryptable — and **exclude it from any backup or disk image that travels unencrypted**, because it is enough on its own to impersonate any account.

### Step 5 — Build and start everything

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

This does four things in order:
1. Builds the application image **once**, tagged `iam-platform:latest`. The `migrate` and `worker` services reuse that exact tag rather than building their own copies.
2. Starts PostgreSQL, Redis and Qdrant, and waits until each is genuinely healthy
3. Runs the database migration as a one-time job, which then exits
4. Starts the **API** and the **worker** — both wait for the migration to have finished *successfully* first, so a failed migration stops the rollout rather than leaving the app running against a stale schema

> **Expect the first build to take 20–45 minutes**, and to download roughly 2 GB. It installs CPU PyTorch and bakes in the document-parsing models and a headless browser, so that a worker never downloads them at run time (which would make the first PDF upload on a fresh container extremely slow, and impossible on a server with no route to Hugging Face). This is a one-time cost per server. Subsequent deploys reuse the cached layers and take about a minute, because the dependency install sits *below* the source copy in the image.
>
> If a later build re-downloads PyTorch after you have only changed application code, something has invalidated that layer — check whether `pyproject.toml` changed, since that is the file the dependency layer keys on.

Watch it happen:

```bash
docker compose -f docker-compose.prod.yml logs -f
```

Press `Ctrl+C` to stop watching (this does **not** stop the containers).

### Step 6 — Verify it's working

All five services should be `running`, and `postgres`, `redis`, `qdrant` and `worker` should also show `healthy`:

```bash
docker compose -f docker-compose.prod.yml ps
```

Then check the API:

```bash
curl http://localhost:8100/readyz
```

You should see `{"status":"ready", ...}`. If you see `503` or `"not_ready"`, check the logs:

```bash
docker compose -f docker-compose.prod.yml logs api
docker compose -f docker-compose.prod.yml logs migrate
```

**Check the worker separately — `/readyz` does not cover it.** The API is healthy whether or not anything is consuming the queue, so a dead worker is invisible from the health endpoint. Ask the worker itself:

```bash
docker compose -f docker-compose.prod.yml exec worker \
  celery -A iam_platform.workers.main:celery_app inspect ping
```

A `pong` means ingestion will run. Anything else means uploads will be accepted and then sit on "Processing" for ever, with no error shown to the person who uploaded them:

```bash
docker compose -f docker-compose.prod.yml logs worker
```

### Step 7 — Create your platform administrator account

Same underlying reason as in development (Part A, Step 5): the self-escalation guard means nobody can grant the *first* platform role through the API — nobody holds any platform permission yet to grant one — and this project's email sender doesn't deliver real mail in any environment yet, so a freshly registered account can't self-verify either. `scripts/bootstrap_platform_admin.py` (now baked into the image, see the Dockerfile) handles both by running directly against the database with the migrator role's credentials — the same authority the migration job already uses, and the same one-time, deliberate bypass as in development.

Register the account you want to make an admin:

```bash
curl -X POST http://localhost:8100/v1/auth/register \
  -H "content-type: application/json" \
  -d '{"email": "admin@lait.co.uk", "password": "Correct-Horse-9!"}'
```

(If you haven't put HTTPS in front of the API yet — that's the next step — substitute `http://localhost:8100` here.)

Then bootstrap it, running the script inside a one-off container that reuses the `migrate` service's existing database credentials:

```bash
docker compose -f docker-compose.prod.yml run --rm migrate python scripts/bootstrap_platform_admin.py admin@lait.co.uk
```

This activates the account and grants it a `platform_super_admin` role (`platform.tenants.create`, `platform.tenants.suspend`, `platform.support.impersonate`). It's idempotent — safe to re-run against an account that already holds the role, it'll just report that and do nothing else.

**Now seed the tenant role catalog too, the same way:**

```bash
docker compose -f docker-compose.prod.yml run --rm migrate python scripts/bootstrap_tenant_catalog.py
```

This step is easy to miss and the failure mode isn't obvious: `bootstrap_platform_admin.py` only seeds the *platform*-scope catalog (fully separate tables, by design). Nothing seeds the *tenant*-scope one — `tenant_permissions` and the built-in Tenant Owner / Tenant Administrator / Member roles — until this runs. Skip it, and creating a tenant still "succeeds": the tenant exists, the owner has a membership, but there's no "Tenant Owner" role to grant them, so they land with zero tenant permissions and a nearly-empty console. `CreateTenant` now refuses outright (503) if you try to create a tenant before this step has run, rather than succeeding silently. Also idempotent — safe to re-run.

> **The admin console (`frontend/`) isn't part of this production compose setup.** This guide's `docker-compose.prod.yml` only builds and runs the backend API — there's no frontend service, Dockerfile, or reverse-proxy entry for it here yet. To run it against this server, build it separately (`cd frontend && npm run build && npm run start`, or deploy it to any Node/Next.js host) with `BACKEND_API_URL` pointed at this server's API origin, and put it behind its own domain or path in Nginx. Until then, `https://yourdomain.com/docs` (Step 6/8) and direct API calls are how you administer this deployment.

### Step 8 — Put a real domain and HTTPS in front of it

Right now the API is only reachable on port 8100 (or whatever `API_HOST_PORT` you set), without encryption. For a real deployment you want a domain name with HTTPS, using **Nginx** as a reverse proxy and **Let's Encrypt** (via Certbot) for a free TLS certificate.

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
        proxy_pass http://127.0.0.1:8100;
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

**8e. Confirm the API's port is not exposed to the internet.** `docker-compose.prod.yml` already binds it to the loopback address only:

```yaml
    ports:
      - "127.0.0.1:${API_HOST_PORT:-8100}:8000"
```

Nginx reaches the API over the loopback; nothing outside the server can. Verify from your own machine (**not** over SSH on the server) — this should time out or be refused:

```bash
curl --max-time 5 http://YOUR_SERVER_IP:8100/livez
```

> **Do not drop the `127.0.0.1:` prefix from this line, and do not rely on the firewall to cover it if you do.** Docker publishes ports by writing its own iptables rules in the `DOCKER` chain, and those are evaluated **before** ufw's. A `ufw deny 8100` does not block a Docker-published port. If the API were bound to `0.0.0.0` (no `127.0.0.1:` prefix), it would be reachable from the internet over plain HTTP on whatever `API_HOST_PORT` is set to — bypassing Nginx, your TLS certificate, and the security headers and rate limiting the proxy adds — and `ufw status` would still look correct.

### Step 9 — Lock down the firewall

Now that Nginx is handling public traffic, open only the ports the world actually needs:

```bash
sudo ufw allow 'Nginx Full'   # opens 80 and 443
sudo ufw status
```

The API's port (8100 by default) should **not** appear in `ufw status`. What actually keeps it private is the `127.0.0.1` binding in Step 8e, **not** this firewall — see the warning there. ufw is what protects everything else on the host that isn't published by Docker.

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
docker compose -f docker-compose.prod.yml logs -f worker
```

Ingestion failures appear in the **worker** log, not the API's — an upload that never finishes leaves nothing in the API log to find.

Logs are structured JSON, one line per event — pipe through `jq` if you have it installed for easier reading: `... | jq .`

### Checking health

```bash
curl https://yourdomain.com/readyz
```

For metrics (Prometheus format, only reachable from the server itself per the Nginx config above):

```bash
curl http://localhost:8100/metrics
```

### Deploying an update

```bash
cd ai_agent_by_Claude
git pull
docker compose -f docker-compose.prod.yml up -d --build
```

This rebuilds the image, re-runs migrations (skipping any already applied), and restarts both the API and the worker with zero manual steps. The old container keeps serving traffic until the new one is healthy and ready to take over.

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

### Deleting expired conversations

Each tenant sets how long its conversations are kept (`conversation_retention_days`, 30 by default). **Nothing enforces that on its own** — the purge is a script you schedule, not a background job, because it enumerates every tenant and therefore needs the migrator role rather than the per-tenant credentials the worker runs with.

Run it once to check it works:

```bash
docker compose -f docker-compose.prod.yml run --rm migrate python scripts/purge_expired_conversations.py
```

Then schedule it daily (`crontab -e`, adjusting the path):

```cron
30 3 * * * cd /home/deploy/ai_agent_by_Claude && docker compose -f docker-compose.prod.yml run --rm migrate python scripts/purge_expired_conversations.py >> /var/log/iam-purge.log 2>&1
```

It exits non-zero if any tenant fails, so a cron mail or log check will tell you it stopped working rather than it silently retaining everything for ever.

### Restarting / stopping

```bash
# Apply an .env change. Use `up -d`, NOT `restart` -- `restart` reuses the
# containers with the environment they were created with, so the change
# appears to have been applied and hasn't been.
docker compose -f docker-compose.prod.yml up -d

# Restart just one service
docker compose -f docker-compose.prod.yml restart api
docker compose -f docker-compose.prod.yml restart worker

# Stop everything
docker compose -f docker-compose.prod.yml down

# Stop everything AND delete ALL data (careful!) -- this removes three
# volumes, not one: the database, the Qdrant vectors, and every uploaded
# document. Re-uploading is the only way back.
docker compose -f docker-compose.prod.yml down -v
```

---

## Security checklist before you go live

Go through this list once before pointing real users at the server:

- [ ] `.env` file permissions are `600` (owner-read-only) — `chmod 600 .env`
- [ ] `.env` is not committed to git (`git status` should not show it — it's already in `.gitignore`)
- [ ] `CORS_ALLOWED_ORIGINS` in `.env` lists only your real frontend domain(s), not `*` or `localhost`
- [ ] The API's port (`API_HOST_PORT`, 8100 by default) is bound to `127.0.0.1` only (Step 8e) and is not in `ufw status`'s allowed list
- [ ] `/metrics` is blocked at the Nginx layer (Step 8c) — it has no authentication of its own
- [ ] HTTPS is working (`https://yourdomain.com`, not just `http://`) and HTTP redirects to it
- [ ] You generated **fresh** production secrets rather than reusing development ones
- [ ] The `jwt_private.pem` / `jwt_public.pem` files from Step 4b are deleted — the key belongs only in `.env`
- [ ] `.env` itself is backed up somewhere encrypted and off this server: it is the only copy of the JWT signing key and the encryption key, and losing it makes every stored provider credential undecryptable
- [ ] Database backups are running and are copied somewhere other than the server itself
- [ ] Uploaded documents are backed up too — they live in the `uploads` Docker volume, which `pg_dump` does not cover
- [ ] You've read the **Known gaps** section of [docs/22-deployment-and-operations.md](docs/22-deployment-and-operations.md#known-gaps) so you know what this project deliberately doesn't do yet (rotating the JWT key or the encryption key isn't a supported operation — plan around both)
- [ ] The conversation-retention purge is scheduled (see **Everyday production operations** below) — nothing deletes expired conversations on its own

---

## Troubleshooting

**`password authentication failed for user "app_platform"` (or `app_tenant`), and the password in `.env` genuinely does contain a `$`** — this is the Compose interpolation bug explained in Step 4a's warning above, not a typo. Confirm it's this by looking for a line like `"ecret" variable is not set. Defaulting to a blank string.` in the output of `docker compose -f docker-compose.prod.yml config` (the word after `$` in your password is what shows up in quotes) — that confirms Compose silently truncated the value at the `$` before it ever reached the container.

The fix is to stop using a password containing `$`, not to re-type the same one:

```bash
NEW_PW=$(openssl rand -base64 32)
echo "New password: $NEW_PW"   # save it -- you'll paste it into .env next
docker compose -f docker-compose.prod.yml exec -T postgres \
  psql -U postgres -v pw="$NEW_PW" -c "ALTER ROLE app_platform LOGIN PASSWORD :'pw';"
```

Then update **both** `APP_PLATFORM_PASSWORD` and `DATABASE__PLATFORM_PASSWORD` in `.env` to the same new value (they must always match each other — see Step 4d), and restart so the containers pick it up: `docker compose -f docker-compose.prod.yml up -d`. Repeat for `app_tenant`/`APP_TENANT_PASSWORD` if that one also contains a `$`.

**`password authentication failed for user "app_tenant"` (or `app_platform`), but the password in `.env` is correct and contains no `$`** — the database was created by an older version of `docker/postgres-init/01-roles.sh`, which was a `.sql` file that hard-coded `dev_only_password` for both roles. `.sql` files get no environment expansion, so the roles were created with a password nothing connects with. The script now takes the real passwords.

The init scripts run **only when the data directory is empty**, so fixing the script does nothing to a database that already exists. Either recreate it (destroys all data):

```bash
docker compose -f docker-compose.prod.yml down -v
docker compose -f docker-compose.prod.yml up -d
```

or, to keep the data, set the passwords on the existing roles:

```bash
docker compose -f docker-compose.prod.yml exec postgres\
  psql -U postgres -d iam_platform\
  -v tenant_pw="$(grep '^APP_TENANT_PASSWORD=' .env | cut -d= -f2-)"\
  -v platform_pw="$(grep '^APP_PLATFORM_PASSWORD=' .env | cut -d= -f2-)"\
  -c "ALTER ROLE app_tenant LOGIN PASSWORD :'tenant_pw';"\
  -c "ALTER ROLE app_platform LOGIN PASSWORD :'platform_pw';"
```

**`password authentication failed for user "postgres"` during the migration job** — on an older checkout, the connection URL was assembled without escaping the credentials, so a password containing `#`, `%`, `@`, `/` or `:` was silently mangled (`#` starts a URL fragment and truncated it). It is fixed; pull the latest code and rebuild the image. If you are on the current code and still see it, the database was initialised with a *different* superuser password than the one now in `.env` — the `POSTGRES_PASSWORD` only takes effect on first creation, so recreate the volume or `ALTER ROLE postgres` as above.

**A button in the admin console does nothing / an action returns a 500** — if you're on an older checkout, several separate defects caused exactly this and are now fixed: menu items used the wrong Base UI prop and were inert, the console's BFF proxy crashed on every `204 No Content` reply (which is what most successful actions return), permission-denied tenant actions raised an unmapped exception, and creating a *second* tenant for the same owner violated a unique index. Pull the latest code and rebuild: `docker compose -f docker-compose.dev.yml up -d --build` (migrations run automatically as part of that), then restart `npm run dev`. Running natively instead? `python -m alembic upgrade head`, restart the API and the worker, then restart `npm run dev`.

**The console is empty and the sidebar only shows "My identity"** — that's a database with no data plus an account with no permissions. Two common causes: you never ran `scripts/bootstrap_platform_admin.py` (Part A Step 5 / Part B Step 7), or you ran the backend test suite against the wrong database, whose teardown truncates every table — including your admin account and its role grants (this shouldn't happen if you followed Part A Step 7's separate-test-database setup). Re-run the bootstrap script, then `scripts/seed_demo_data.py` if you want demo content back.

**I created a tenant and its owner's sidebar only shows Dashboard, Assistants, Knowledge bases, and Conversations — no Members, no Roles & permissions, no Provider credentials** — the owner's membership has no role assigned, because `scripts/bootstrap_tenant_catalog.py` (Part A Step 5 / Part B Step 7) was never run and there was no "Tenant Owner" role for `CreateTenant` to grant. Run it now, then assign the role to the affected membership by hand (there's no "repair an existing tenant" button, since this shouldn't happen again — `CreateTenant` now refuses with a 503 instead of creating a tenant this way going forward):

```sql
INSERT INTO tenant_membership_roles (id, tenant_id, membership_id, role_id, granted_by_user_id)
SELECT gen_random_uuid(), '<tenant-id>', '<membership-id>',
       (SELECT id FROM tenant_roles WHERE code = 'tenant_owner' AND tenant_id IS NULL),
       '<your-user-id>';
```

**I upgraded and the Users screen disappeared** — `platform.users.read`/`platform.users.manage` were added after the first release. Re-run `python scripts/bootstrap_platform_admin.py <your-email>`; it's idempotent and picks up permissions added since.

**I registered an account and never got a verification email** — this isn't a misconfiguration on your end. The project's email sender (`ConsoleEmailSender`) only ever logs "email queued" to the console — no real provider (SES, SendGrid, etc.) is wired in yet, in development *or* production. For the platform administrator account, `scripts/bootstrap_platform_admin.py` (Part A Step 5 / Part B Step 7) activates the account directly and sidesteps this entirely. For any other account, someone holding database access has to flip that user's `status` to `'active'` by hand until a real email provider is added — see [docs/22-deployment-and-operations.md](docs/22-deployment-and-operations.md#known-gaps).

**`docker compose` says "command not found"** — you have an old Docker install with the separate `docker-compose` (with a hyphen) tool. Either install the current Docker (Step 2 does this correctly) or substitute `docker-compose` for `docker compose` throughout — the commands are otherwise identical.

**`/readyz` returns 503 / `"not_ready"`** — one of Postgres or Redis isn't reachable yet. Check `docker compose ... ps` to confirm both show as healthy, and check `docker compose ... logs postgres redis`.

**Migration container exits immediately with a permission or validation error** — almost always a `.env` value is missing or malformed. Re-check every value in Step 4e is filled in, especially that `SECRET_PROVIDER` is not left as `env` (production refuses to start with that setting on purpose).

**"refusing to start: environment=production requires a real secret provider"** — `ALLOW_PLAINTEXT_SECRETS=true` is missing from the environment. The compose file sets it by default, so this usually means a stray `ALLOW_PLAINTEXT_SECRETS=false` in your `.env`, or `SECRET_PROVIDER` set to something other than `env` without the matching credentials.

**Uploads stay on "Processing" for ever** — the worker isn't consuming the queue. Check it answers (`docker compose -f docker-compose.prod.yml exec worker celery -A iam_platform.workers.main:celery_app inspect ping`) and read `logs worker`; the API log will show nothing, because the API's part succeeded.

**Uploads fail with a permission error writing to `/var/lib/iam-platform/storage`** — the `uploads` volume was created before the image pre-created that directory, so it is owned by root while the container runs as uid 1001. Recreate just that volume:

```bash
docker compose -f docker-compose.prod.yml down
docker volume ls | grep uploads          # find the exact name
docker volume rm <the-name-it-printed>
docker compose -f docker-compose.prod.yml up -d
```

Any documents already uploaded must be re-uploaded; their rows remain and can be deleted from the console.

**Changes to `.env` don't seem to apply** — Docker Compose only re-reads environment variables when a container restarts, not automatically. Run `docker compose -f docker-compose.prod.yml up -d` again after editing `.env`.

**I forgot to save my JWT keys / encryption key somewhere safe** — if you still have the running containers, the values are in your `.env` file. If you've lost both, you'll need to generate new ones and restart the API; note that this invalidates every currently logged-in user's session, and previously-encrypted provider credentials become unreadable if the encryption key specifically is lost (there is currently no key-rotation procedure — see the checklist above).

**Still stuck?** The deep-dive reference at [docs/22-deployment-and-operations.md](docs/22-deployment-and-operations.md) covers failure modes, scaling, and rollback in more technical detail.
