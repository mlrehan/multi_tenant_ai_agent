/** Pulls `:tenantId` out of a `/tenant/:tenantId/...` pathname, or `null`
 * outside that section (e.g. `/platform/...`, `/select-tenant`). Kept as a
 * plain string match rather than route params so the app shell (sidebar,
 * topbar) can derive it once from `usePathname()` without needing every
 * layout in the tree to forward a `tenantId` prop down through Next's
 * route-group layout nesting. */
export function extractTenantIdFromPath(pathname: string): string | null {
  const match = pathname.match(/^\/tenant\/([^/]+)/);
  return match ? match[1] : null;
}
