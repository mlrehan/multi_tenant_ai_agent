/**
 * Copies the tenant administrator guide into `public/` so the console serves
 * it at /help (see the redirect in next.config.ts).
 *
 * `docs/tenant-admin-guide/` stays the one source: it is also what gets
 * zipped for offline readers, and two hand-maintained copies would drift.
 * The copy in `public/help/` is generated, git-ignored, and rebuilt before
 * every `dev` and `build`, so a stale page cannot be shipped by forgetting a
 * step.
 *
 * Fails loudly when the source is missing: a console built without the guide
 * would otherwise serve a 404 behind a sidebar link that looks fine.
 */

import { cpSync, existsSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const frontend = join(dirname(fileURLToPath(import.meta.url)), "..");
const source = join(frontend, "..", "docs", "tenant-admin-guide");
const target = join(frontend, "public", "help", "tenant-admin-guide");

if (!existsSync(join(source, "index.html"))) {
  console.error(`sync-help: guide not found at ${source}`);
  process.exit(1);
}

// Remove first, so a screenshot deleted from the guide is deleted here too.
rmSync(target, { recursive: true, force: true });
cpSync(source, target, { recursive: true });
console.log(`sync-help: copied the tenant admin guide to ${target}`);
