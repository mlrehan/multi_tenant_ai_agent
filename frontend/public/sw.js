/* Service worker for agent handoff notifications.
 *
 * Deliberately tiny and dependency-free. A service worker intercepts every
 * request on this origin for as long as it is installed, so the less it does
 * the less there is to get wrong -- this one has no `fetch` handler at all,
 * which means it never sits in the path of a console request or caches a stale
 * page. It exists only to receive pushes and to focus a tab when one is
 * clicked.
 *
 * Registered from the Inbox screen rather than app-wide: an agent who never
 * opens the queue has nothing to be notified about.
 */

// Bumped to force an update when this file changes; the browser compares the
// script byte-for-byte, so a version comment is enough.
const SW_VERSION = "handoff-notify-1";

self.addEventListener("install", () => {
  // Replace the previous worker immediately instead of waiting for every tab
  // to close. A notification fix that only lands after the agent quits the
  // browser is a fix nobody receives.
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("push", (event) => {
  // A push with no data, or unparseable data, still means *something* is
  // waiting -- the server only ever sends one kind. Falling back to a generic
  // notice beats showing nothing, which is what a thrown error here does.
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch (err) {
    payload = {};
  }

  const title = payload.title || "A visitor is waiting";
  const body = payload.body || "Someone has asked to speak with your team.";
  const url = typeof payload.url === "string" ? payload.url : "/";

  event.waitUntil(
    self.registration.showNotification(title, {
      body,
      // `tag` collapses repeats: two waiting visitors for the same team
      // replace each other rather than stacking six alerts an agent has to
      // dismiss one by one.
      tag: payload.tag || "handoff",
      renotify: true,
      // **Vibration.** Two short pulses with a gap -- long enough to feel
      // deliberate rather than a stray buzz, short enough not to be annoying
      // when several arrive. Ignored on desktop and on iOS, which is fine:
      // the notification itself still shows.
      vibrate: [180, 90, 180],
      // Not `requireInteraction`: an alert that will not go away until
      // clicked is the kind people disable entirely.
      badge: "/icon-badge.png",
      icon: "/icon-192.png",
      data: { url },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || "/";

  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({
        type: "window",
        includeUncontrolled: true,
      });
      // Focus an existing console tab rather than opening a second one. An
      // agent with the queue already open does not want a duplicate; they want
      // the window they were using brought forward.
      for (const client of windows) {
        if (new URL(client.url).origin === self.location.origin) {
          await client.focus();
          // Resolved against this origin, so a payload cannot navigate an
          // agent's authenticated tab to another site.
          const absolute = new URL(target, self.location.origin).href;
          if ("navigate" in client) {
            try {
              await client.navigate(absolute);
            } catch (err) {
              /* Focus already succeeded; navigation is the nicety. */
            }
          }
          return;
        }
      }
      await self.clients.openWindow(new URL(target, self.location.origin).href);
    })(),
  );
});
