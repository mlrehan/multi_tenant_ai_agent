"""Deletes conversations past each tenant's retention window.

Run on a schedule -- a Kubernetes CronJob, a systemd timer, or cron:

    python -m scripts.purge_expired_conversations          # delete
    python -m scripts.purge_expired_conversations --dry-run # report only

**Why a script rather than a Celery task.** Retention has to visit every
tenant, and the worker connects only as `app_tenant` by design
(docs/18-schema-rls-and-migrations.md: "Application code and worker code only
ever connect as app_platform" is precisely what workers must *not* do). Giving
the worker a BYPASSRLS connection so it could enumerate tenants would trade a
real isolation guarantee for scheduling convenience. This script uses the
migrator role for the tenant list only, then does every delete through the
ordinary tenant-scoped unit of work -- so the deletion itself still runs under
RLS, with the tenant predicate the application layer applies.

**One rule, one implementation.** The per-tenant work is
`PurgeExpiredConversations`, the same use case the tests cover. A set-based
`DELETE ... USING tenant_chatbot_settings` would be faster and would be a
second copy of the retention rule, free to drift from the first.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import text

from iam_platform.application.ai_resources.purge_conversations import (
    PurgeExpiredConversations,
)
from iam_platform.bootstrap import build_container
from iam_platform.core.config import Settings
from iam_platform.infrastructure.db.session import build_engine_from_dsn


async def main(dry_run: bool) -> int:
    settings = Settings()  # type: ignore[call-arg]

    # Tenant list only. Nothing is deleted through this connection.
    listing_engine = build_engine_from_dsn(
        settings.database.migrator_dsn, settings.database
    )
    try:
        async with listing_engine.connect() as conn:
            tenant_ids = [
                row[0]
                for row in (
                    await conn.execute(text("SELECT id FROM tenants ORDER BY created_at"))
                ).all()
            ]
    finally:
        await listing_engine.dispose()

    container = await build_container(settings)
    total = 0
    failures = 0
    try:
        use_case = PurgeExpiredConversations(
            container.ai_resource_uow_factory, container.clock
        )
        for tenant_id in tenant_ids:
            if dry_run:
                # Deliberately not "run it and roll back": that would take the
                # same locks and leave the operator believing a dry run is
                # free. Reported from the settings instead.
                print(f"  {tenant_id}  (dry run -- nothing deleted)")
                continue
            try:
                result = await use_case.execute(tenant_id=tenant_id)
            except Exception as exc:  # one tenant's failure is not the sweep's
                failures += 1
                print(f"  {tenant_id}  FAILED: {exc}", file=sys.stderr)
                continue
            total += result.deleted
            if result.deleted:
                print(
                    f"  {tenant_id}  deleted {result.deleted} "
                    f"(retention {result.retention_days}d)"
                )
    finally:
        await container.shutdown()

    # **A failed sweep must not read as a clean one.** The per-tenant `except`
    # above exists so one tenant cannot stop the others -- but on its own it
    # turned a wiring bug into a cheerful "purged 0", which is exactly how a
    # retention job silently stops retaining anything. The count is on the
    # summary line and the exit status is non-zero, so a scheduler notices.
    print(
        f"purged {total} conversation(s) across {len(tenant_ids)} tenant(s)"
        + (f" -- {failures} tenant(s) FAILED" if failures else "")
    )
    return 1 if failures else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="list tenants without deleting"
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.dry_run)))
