"use client";

/**
 * The three ways an agent finds out a visitor is waiting, beyond the chime.
 *
 * They cover different states and none of them replaces another:
 *
 * - **Push** reaches a closed or backgrounded console. It is the only one that
 *   works when the tab is gone, and the only one that needs the user's
 *   permission and a server round trip.
 * - **A badge** is for the tab that is open but not looked at — the count on
 *   the favicon/taskbar and in the document title, so a glance at a window
 *   full of tabs shows the queue is not empty.
 * - **Vibration** is for a phone or tablet in a hand, where a chime may be
 *   muted and a badge is invisible.
 *
 * Everything here degrades silently when unsupported. Safari has no
 * `setAppBadge` until recently, iOS has no `vibrate` at all, and a desktop
 * Firefox user may have denied notifications years ago — none of which is an
 * error worth showing an agent who cannot act on it.
 */

const BADGE_TITLE_PREFIX = /^\(\d+\)\s*/;

/** Converts the server's base64url VAPID key to the `Uint8Array` the
 *  `PushManager` demands. It will not accept the string form, and the padding
 *  and URL-safe characters have to be undone first — a subtly wrong conversion
 *  yields `InvalidCharacterError` rather than anything descriptive. */
function vapidKeyToBytes(base64Url: string): Uint8Array<ArrayBuffer> {
  const padding = "=".repeat((4 - (base64Url.length % 4)) % 4);
  const base64 = (base64Url + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  // Backed by an explicit `ArrayBuffer` rather than `Uint8Array.from`, whose
  // return type is `Uint8Array<ArrayBufferLike>` — and `applicationServerKey`
  // requires an `ArrayBuffer`-backed view, because a `SharedArrayBuffer` one
  // cannot be transferred to the push subsystem.
  const bytes = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i);
  return bytes;
}

export type PushState =
  | "unsupported"
  | "unconfigured"
  | "denied"
  | "prompt"
  | "subscribed";

export function pushIsSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    "Notification" in window
  );
}

async function registerWorker(): Promise<ServiceWorkerRegistration> {
  // `/sw.js` at the origin root, so its scope covers the whole console. A
  // worker served from a subdirectory can only control that subdirectory.
  const registration = await navigator.serviceWorker.register("/sw.js");
  // Waits for activation. Subscribing against a worker that is still
  // installing throws, and the failure reads as a permission problem.
  await navigator.serviceWorker.ready;
  return registration;
}

/** Current state without prompting. Used to render the toggle honestly:
 *  "Enable notifications" and "Notifications blocked" are different buttons. */
export async function readPushState(publicKey: string | null): Promise<PushState> {
  if (!pushIsSupported()) return "unsupported";
  if (!publicKey) return "unconfigured";
  if (Notification.permission === "denied") return "denied";
  try {
    const registration = await navigator.serviceWorker.getRegistration("/sw.js");
    const existing = await registration?.pushManager.getSubscription();
    if (existing) return "subscribed";
  } catch {
    /* No registration yet — that is "prompt", not an error. */
  }
  return Notification.permission === "granted" ? "prompt" : "prompt";
}

export interface SubscribeResult {
  state: PushState;
  /** The endpoint, so the caller can unsubscribe the same browser later. */
  endpoint?: string;
}

/**
 * Asks permission, subscribes, and hands the subscription to the server.
 *
 * **The permission prompt is only ever raised from a click.** Browsers now
 * penalise origins that prompt on load — Chrome shows a quieter permission UI
 * and Firefox suppresses it entirely — so an agent who was never asked would
 * silently get nothing.
 */
export async function subscribeToPush(
  publicKey: string,
  send: (body: { endpoint: string; p256dh_key: string; auth_key: string }) => Promise<void>,
): Promise<SubscribeResult> {
  if (!pushIsSupported()) return { state: "unsupported" };

  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    return { state: permission === "denied" ? "denied" : "prompt" };
  }

  const registration = await registerWorker();
  // Reuse an existing subscription rather than creating a second one: the
  // browser returns the same endpoint anyway, and `subscribe` throws if called
  // with a different applicationServerKey than the live subscription.
  const existing = await registration.pushManager.getSubscription();
  const subscription =
    existing ??
    (await registration.pushManager.subscribe({
      // Required to be true by every current browser: a push that shows no
      // notification is not permitted, which is also why the service worker
      // always calls `showNotification`.
      userVisibleOnly: true,
      applicationServerKey: vapidKeyToBytes(publicKey),
    }));

  const json = subscription.toJSON();
  const p256dh = json.keys?.p256dh;
  const auth = json.keys?.auth;
  if (!subscription.endpoint || !p256dh || !auth) {
    // Without both keys the server cannot encrypt for this browser. Better to
    // report failure than to store a row that can never be delivered to.
    return { state: "prompt" };
  }

  await send({ endpoint: subscription.endpoint, p256dh_key: p256dh, auth_key: auth });
  return { state: "subscribed", endpoint: subscription.endpoint };
}

/** Unsubscribes the browser and tells the server to forget the row.
 *
 *  Both, in that order — dropping only the local subscription would leave the
 *  server sending to an endpoint that now 410s on every handoff. */
export async function unsubscribeFromPush(
  forget: (endpoint: string) => Promise<void>,
): Promise<void> {
  if (!pushIsSupported()) return;
  const registration = await navigator.serviceWorker.getRegistration("/sw.js");
  const subscription = await registration?.pushManager.getSubscription();
  if (!subscription) return;
  const { endpoint } = subscription;
  await subscription.unsubscribe();
  try {
    await forget(endpoint);
  } catch {
    /* The browser has already stopped receiving; a stale server row will be
       pruned by the first 410 it earns. Not worth an error the agent cannot
       act on. */
  }
}

/**
 * The waiting count, shown on the app icon and in the tab title.
 *
 * Two mechanisms because they cover different windows. `setAppBadge` puts a
 * dot or count on the taskbar/dock icon and works even when the tab is not
 * visible, but it is unsupported in Firefox and older Safari. The title prefix
 * works everywhere and is what an agent sees when scanning tab labels.
 */
export function setQueueBadge(count: number): void {
  if (typeof document !== "undefined") {
    const base = document.title.replace(BADGE_TITLE_PREFIX, "");
    document.title = count > 0 ? `(${count}) ${base}` : base;
  }
  const nav = navigator as Navigator & {
    setAppBadge?: (count?: number) => Promise<void>;
    clearAppBadge?: () => Promise<void>;
  };
  // Both calls return promises that reject on unsupported platforms rather
  // than throwing synchronously, so the catch is required and not defensive
  // padding.
  if (count > 0) {
    void nav.setAppBadge?.(count).catch(() => undefined);
  } else {
    void nav.clearAppBadge?.().catch(() => undefined);
  }
}

/**
 * A system notification for a handoff, shown by the page itself.
 *
 * **This is what reaches an agent whose window is minimised or who is on
 * another tab.** The chime is inaudible on a muted machine, the toast is
 * painted inside a window nobody is looking at, and the title badge is only
 * seen by someone already scanning their tabs — none of them leave the page.
 * A notification is drawn by the operating system, so it arrives whether or
 * not the console is the visible window.
 *
 * Distinct from Web Push, and needed alongside it: push is delivered by the
 * browser's push service and reaches a console that is *closed*, but it only
 * works for an agent who has both granted permission and registered a
 * subscription. This path needs nothing but permission, and covers the far
 * commoner case of a console that is open and simply not in front of you.
 *
 * Shown **only when the console is not what the agent is looking at**, which
 * is deliberately wider than "hidden". `visibilityState` reports a minimised
 * window and a background tab, but a browser sitting behind the agent's mail
 * client is still `visible` — the exact case of "working on something else"
 * that this is for. `hasFocus()` is what distinguishes the two, so both are
 * checked; an agent watching the queue see a row appear gets the toast and the
 * chime and no OS panel over their work.
 *
 * Routed through the service-worker registration when one exists, because
 * `new Notification()` is deprecated on some platforms and outright throws on
 * Android Chrome. Falling back to the constructor keeps desktop browsers that
 * have not registered a worker — an agent who has never opened the Inbox —
 * still getting alerted.
 */
export async function showHandoffNotification(
  body = "Someone has asked to speak with your team.",
): Promise<void> {
  if (typeof window === "undefined" || !("Notification" in window)) return;
  if (Notification.permission !== "granted") return;
  const watching =
    typeof document !== "undefined" &&
    document.visibilityState === "visible" &&
    document.hasFocus();
  if (watching) return;

  const options: NotificationOptions & { vibrate?: number[]; renotify?: boolean } = {
    body,
    // Same tag as the pushed one, so a handoff that arrives by both routes
    // replaces itself rather than showing the agent two identical panels.
    tag: "handoff",
    renotify: true,
    vibrate: [180, 90, 180],
    icon: "/icon-192.png",
    badge: "/icon-badge.png",
  };

  try {
    const registration = await navigator.serviceWorker?.getRegistration("/sw.js");
    if (registration) {
      await registration.showNotification("A visitor is waiting", options);
      return;
    }
    new Notification("A visitor is waiting", options);
  } catch {
    /* Permission revoked mid-session, or a platform that refuses the
       constructor. The in-page toast and chime still fired. */
  }
}

/**
 * A short buzz for a device being held.
 *
 * Only when the page is **hidden** — a visible tab already got the chime and
 * the toast, and buzzing someone who is looking straight at the queue is just
 * noise. Chrome also refuses `vibrate` without a prior user gesture on the
 * page, which the sound toggle or any click satisfies; the refusal is a console
 * warning, not an exception, so nothing here needs to handle it.
 */
export function vibrateForHandoff(): void {
  if (typeof document === "undefined" || typeof navigator === "undefined") return;
  if (document.visibilityState === "visible") return;
  const nav = navigator as Navigator & { vibrate?: (pattern: number | number[]) => boolean };
  // Same pattern the service worker uses, so a push-driven buzz and an
  // SSE-driven one feel identical rather than like two different events.
  nav.vibrate?.([180, 90, 180]);
}
