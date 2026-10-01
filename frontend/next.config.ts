import type { NextConfig } from "next";

/** Where `scripts/sync-help.mjs` puts the tenant administrator guide. */
const GUIDE = "/help/tenant-admin-guide/index.html";

const nextConfig: NextConfig = {
  /**
   * `/help` is the short address a tenant admin shares or emails.
   *
   * It is public on purpose: the person it is sent to may have no account.
   * Redirects run before `proxy.ts`, and the destination ends in `.html`,
   * which the proxy's matcher skips, so the sign-in redirect never sees it.
   *
   * A redirect rather than a rewrite: the guide loads `assets/...` with
   * relative paths, which only resolve when the address bar shows the real
   * folder. 307, not 308, so the guide can move without browsers having
   * cached the old target forever. A `#section` fragment survives the
   * redirect, so `/help#troubleshooting` works as a link.
   */
  async redirects() {
    return [
      { source: "/help", destination: GUIDE, permanent: false },
      { source: "/help/tenant-admin-guide", destination: GUIDE, permanent: false },
    ];
  },
  /**
   * Shareable is not the same as indexed. The screenshots show a real
   * organisation's console, so search engines are asked to stay out.
   */
  async headers() {
    return [
      {
        source: "/help/:path*",
        headers: [{ key: "X-Robots-Tag", value: "noindex, nofollow" }],
      },
    ];
  },
};

export default nextConfig;
