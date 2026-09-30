#!/usr/bin/env bash
# Deploy the latest code to a production server, in one command.
#
#   scripts/deploy.sh                      # everything except the platform-admin refresh
#   scripts/deploy.sh admin@yourdomain.com # also re-syncs that admin's platform permissions
#
# The steps are DEPLOYMENT.md's "Deploying an update", in the same order:
#   1. back up the database        4. rebuild + restart the console (pm2)
#   2. pull the new code           5. refresh the permission catalogues
#   3. rebuild + restart backend   6. prove the new version is what is running
#
# It stops at the first failure, and it refuses to start if the server's
# checkout has local edits: `git pull` would then leave the old code in place
# while everything after it rebuilt that old code -- the "I deployed and
# nothing changed" failure this script exists to prevent.

set -euo pipefail

ADMIN_EMAIL="${1:-}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

COMPOSE=(docker compose -f docker-compose.prod.yml)
CONSOLE_NAME="iam-console"
CONSOLE_URL="http://127.0.0.1:3100/"
# Same variable the compose file maps the API's host port from.
API_PORT="$(grep -E '^API_HOST_PORT=' .env 2>/dev/null | tail -1 | cut -d= -f2 | tr -d '"'"'"' ' || true)"
API_PORT="${API_PORT:-8100}"

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
fail() { printf '\n\033[1;31mDEPLOY FAILED: %s\033[0m\n' "$*" >&2; exit 1; }
ok()   { printf '\033[1;32m    %s\033[0m\n' "$*"; }

command -v pm2 >/dev/null || fail "pm2 is not installed (DEPLOYMENT.md Step 7a)."
[ -f .env ] || fail "no .env beside docker-compose.prod.yml (DEPLOYMENT.md Step 4)."

# --- 1. Backup -------------------------------------------------------------
step "1/6 Backing up the database"
mkdir -p backups
BACKUP="backups/backup-$(date +%F-%H%M%S).sql.gz"
# pipefail makes a failing pg_dump fail this line, not just an empty file.
"${COMPOSE[@]}" exec -T postgres pg_dump -U postgres --clean --if-exists iam_platform | gzip > "$BACKUP"
[ -s "$BACKUP" ] || fail "backup $BACKUP is empty."
ok "$BACKUP ($(du -h "$BACKUP" | cut -f1))"

# --- 2. Code ---------------------------------------------------------------
step "2/6 Pulling the new code"
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  git status --short --untracked-files=no
  fail "the files above were edited on this server. Commit them, or discard them with 'git checkout -- <file>', then run this again."
fi
BEFORE="$(git rev-parse --short HEAD)"
git pull --ff-only
AFTER="$(git rev-parse --short HEAD)"
if [ "$BEFORE" = "$AFTER" ]; then
  ok "already at $(git log -1 --oneline) -- nothing new was pulled; rebuilding anyway"
else
  ok "$BEFORE -> $(git log -1 --oneline)"
fi

# --- 3. Backend ------------------------------------------------------------
step "3/6 Rebuilding and restarting the API, worker and migrations (several minutes)"
if ! "${COMPOSE[@]}" up -d --build; then
  # The usual cause: Postgres was recreated too and `migrate` connected
  # before it was ready. Retry the migration once, then bring the rest up.
  echo "    'up' failed -- retrying the migration once in case Postgres was still starting"
  sleep 10
  "${COMPOSE[@]}" run --rm migrate || fail "the migration failed. Check: ${COMPOSE[*]} logs migrate. Your backup is $BACKUP."
  "${COMPOSE[@]}" up -d || fail "containers did not start. Check: ${COMPOSE[*]} logs api worker"
fi
ok "backend containers recreated"

# --- 4. Console ------------------------------------------------------------
step "4/6 Rebuilding and restarting the admin console"
pm2 describe "$CONSOLE_NAME" >/dev/null 2>&1 \
  || fail "pm2 has no process named '$CONSOLE_NAME'. Start it once as in DEPLOYMENT.md Step 7a."
(
  cd frontend
  npm ci
  npm run build
)
pm2 restart "$CONSOLE_NAME" --update-env >/dev/null
ok "console rebuilt (build $(cat frontend/.next/BUILD_ID)) and restarted"

# --- 5. Permissions --------------------------------------------------------
step "5/6 Refreshing the permission catalogues"
"${COMPOSE[@]}" run --rm migrate python scripts/bootstrap_tenant_catalog.py
if [ -n "$ADMIN_EMAIL" ]; then
  "${COMPOSE[@]}" run --rm migrate python scripts/bootstrap_platform_admin.py "$ADMIN_EMAIL"
else
  ok "platform admin skipped (pass your admin email as the first argument to include it)"
fi

# --- 6. Verify -------------------------------------------------------------
step "6/6 Checking the new version is the one running"
MIGRATION="$("${COMPOSE[@]}" run --rm migrate python -m alembic current 2>/dev/null | tail -1 || true)"
case "$MIGRATION" in
  *"(head)"*) ok "database schema: $MIGRATION" ;;
  *) fail "database is not at the latest migration (got: '$MIGRATION')." ;;
esac

READY=""
for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${API_PORT}/readyz" 2>/dev/null | grep -q '"ready"'; then
    READY=1; break
  fi
  sleep 2
done
[ -n "$READY" ] || fail "API not ready on port $API_PORT after 60s. Check: ${COMPOSE[*]} logs --tail=100 api"
ok "API ready on 127.0.0.1:${API_PORT}"

CONSOLE_OK=""
for _ in $(seq 1 15); do
  if curl -fsS -o /dev/null "$CONSOLE_URL" 2>/dev/null; then CONSOLE_OK=1; break; fi
  sleep 2
done
[ -n "$CONSOLE_OK" ] || fail "console not answering at $CONSOLE_URL. Check: pm2 logs $CONSOLE_NAME --lines 100"
ok "console answering at $CONSOLE_URL"

"${COMPOSE[@]}" ps
printf '\n\033[1;32mDeployed %s.\033[0m Hard-refresh the browser (Ctrl+Shift+R). Websites embedding the chat widget pick up widget changes within an hour.\n' \
  "$(git log -1 --oneline)"
