"use client";

/**
 * The handoff chime, and whether the agent wants it.
 *
 * Split out of the Inbox screen because the two halves ended up in different
 * places: the *toggle* is on the Inbox, where an agent expects to find it, but
 * the *sound* is now played by the shell, which alerts on every screen. Left
 * as component state it would have been unreadable from the shell, and a
 * second copy of the oscillator code would be a second thing to keep in tune.
 *
 * Persisted, so the preference survives a reload. An agent who turned the
 * chime off is telling you something about their office, not about this
 * browser tab, and asking again every morning is how a setting gets ignored.
 */

import { useCallback, useSyncExternalStore } from "react";

const SOUND_KEY = "iamconsole:handoff-sound";

/** Same-tab subscribers. The `storage` event only fires in *other* tabs, so a
 *  toggle would not repaint its own button without this. */
const listeners = new Set<() => void>();

function subscribe(onChange: () => void): () => void {
  listeners.add(onChange);
  window.addEventListener("storage", onChange);
  return () => {
    listeners.delete(onChange);
    window.removeEventListener("storage", onChange);
  };
}

/**
 * The toggle's state, read straight from storage.
 *
 * `useSyncExternalStore` rather than `useState` + an effect: the preference
 * lives outside React, and seeding it in an effect both trips
 * `set-state-in-effect` and renders one frame with the wrong value. The server
 * snapshot is the default, which is also what a browser with storage blocked
 * reports — so the markup React renders on the server and the one it hydrates
 * agree.
 */
export function useHandoffSound(): [boolean, (on: boolean) => void] {
  const enabled = useSyncExternalStore(
    subscribe,
    handoffSoundEnabled,
    () => true,
  );
  const set = useCallback((on: boolean) => setHandoffSoundEnabled(on), []);
  return [enabled, set];
}

/** Defaults to on: an alert nobody enabled is an alert nobody receives, and
 *  the toggle to silence it is one click away on the screen this matters on. */
export function handoffSoundEnabled(): boolean {
  try {
    return window.localStorage.getItem(SOUND_KEY) !== "off";
  } catch {
    // Storage blocked (private mode, site data denied). Falling back to on
    // keeps the product working; the preference simply does not persist.
    return true;
  }
}

export function setHandoffSoundEnabled(on: boolean): void {
  try {
    window.localStorage.setItem(SOUND_KEY, on ? "on" : "off");
  } catch {
    /* Nothing the agent can act on; the toggle still works for this session. */
  }
  listeners.forEach((notify) => notify());
}

/**
 * One shared AudioContext for the whole console, created lazily.
 *
 * **A per-chime context is what silenced the alarm.** Browsers refuse to start
 * an `AudioContext` before the page has had a user gesture: a fresh one
 * created inside a network-event callback is born `suspended`, schedules its
 * oscillators against a clock that never advances, and plays nothing at all --
 * with no error, which is exactly why this failed invisibly. A single
 * long-lived context can instead be *unlocked* once, by any click, and stays
 * usable for every alert afterwards.
 */
let sharedCtx: AudioContext | null = null;

function audioContext(): AudioContext | null {
  if (sharedCtx) return sharedCtx;
  const Ctx =
    window.AudioContext ??
    (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!Ctx) return null;
  try {
    sharedCtx = new Ctx();
    return sharedCtx;
  } catch {
    return null;
  }
}

/**
 * Readies audio on the first user gesture, so an alert that arrives later can
 * actually be heard.
 *
 * Registered once from the app shell. The listeners remove themselves after
 * the first gesture -- the context only needs unlocking once per page, and a
 * permanent document-level handler on every click is a cost with no purpose.
 *
 * `pointerdown` *and* `keydown`: an agent who works by keyboard never fires a
 * pointer event, and would otherwise have a permanently silent console.
 */
export function primeHandoffAudio(): void {
  if (typeof window === "undefined") return;
  const unlock = () => {
    const ctx = audioContext();
    // `resume()` on an already-running context is a no-op, so this is safe to
    // call whether or not the browser actually suspended it.
    void ctx?.resume().catch(() => undefined);
    window.removeEventListener("pointerdown", unlock);
    window.removeEventListener("keydown", unlock);
  };
  window.addEventListener("pointerdown", unlock);
  window.addEventListener("keydown", unlock);
}

/**
 * The handoff alert tone.
 *
 * Three rising two-note pairs, deliberately insistent rather than a polite
 * ping: this is the sound that tells a nursery someone is waiting to speak to
 * a human, and a soft chime is routinely missed in a room with any background
 * noise. A square wave carries further through that noise than a sine at the
 * same gain.
 *
 * Never throws. A blocked or unavailable tone must not surface as an error --
 * the banner, the system notification, the tab title and the vibration all
 * still fired, and the agent can act on those.
 */
export function chimeForHandoff(): void {
  try {
    const ctx = audioContext();
    if (!ctx) return;
    // Resumed on every chime, not only at unlock: a context can be suspended
    // again by the browser when a tab is backgrounded, which is precisely when
    // an agent most needs to hear this.
    void ctx.resume().catch(() => undefined);

    const REPEATS = 3;
    const PAIR_SECONDS = 0.46;
    for (let repeat = 0; repeat < REPEATS; repeat += 1) {
      [880, 1174].forEach((freq, i) => {
        const at = ctx.currentTime + repeat * PAIR_SECONDS + i * 0.16;
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.frequency.value = freq;
        osc.type = "square";
        gain.gain.setValueAtTime(0.0001, at);
        gain.gain.exponentialRampToValueAtTime(0.45, at + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, at + 0.22);
        osc.connect(gain).connect(ctx.destination);
        osc.start(at);
        osc.stop(at + 0.24);
      });
    }
    // The context is deliberately **not** closed here. It is shared and
    // reused; closing it would mean the next chime has to create and unlock a
    // new one, which is the bug this whole arrangement exists to avoid.
  } catch {
    /* Autoplay policy or no WebAudio -- the other channels still fired. */
  }
}

/**
 * The repeating alarm: the chime, over and over, until a person stops it.
 *
 * **Why a loop and not a single chime.** One burst is missed by anyone who
 * stepped away from the desk for thirty seconds -- and a visitor waiting to
 * speak to a nursery is exactly the case where "nobody happened to be looking"
 * is the failure being designed against. It keeps going until someone
 * acknowledges it, which is what makes it an alarm rather than a notification.
 *
 * **Three things stop it**, and all three matter:
 *  - the agent dismisses or snoozes it (the point of the controls);
 *  - the queue empties, because a colleague claimed the conversation -- an
 *    alarm for work that is already being handled is how people learn to
 *    ignore alarms;
 *  - `MAX_ALARM_MS` elapses. This last one is a runaway guard, not a feature:
 *    a console left open overnight must not still be sounding at 3am for a
 *    visitor who gave up hours ago. Raise it if that is genuinely wanted, but
 *    do not remove it -- an alarm with no upper bound is one someone
 *    eventually silences by muting the whole machine, permanently.
 */

const REPEAT_INTERVAL_MS = 3000;
const MAX_ALARM_MS = 10 * 60 * 1000;

let alarmTimer: ReturnType<typeof setInterval> | null = null;
let alarmStopAt = 0;
/** Notified whenever the alarm starts or stops, so the UI showing the
 *  Dismiss/Snooze controls can appear and disappear with it rather than
 *  keeping its own duplicate idea of whether the alarm is running. */
const alarmListeners = new Set<(active: boolean) => void>();

function announce(active: boolean): void {
  alarmListeners.forEach((notify) => notify(active));
}

export function subscribeToAlarm(onChange: (active: boolean) => void): () => void {
  alarmListeners.add(onChange);
  return () => {
    alarmListeners.delete(onChange);
  };
}

export function alarmIsSounding(): boolean {
  return alarmTimer !== null;
}

/**
 * Starts the repeating alarm, if it is not already sounding.
 *
 * Idempotent on purpose: a second visitor arriving while the alarm is already
 * going must not start a second overlapping loop, which would double the
 * volume and desynchronise into noise. The queue count in the UI is what
 * communicates "more than one".
 */
export function startHandoffAlarm(): void {
  if (!handoffSoundEnabled()) return;
  if (alarmTimer !== null) return;

  alarmStopAt = Date.now() + MAX_ALARM_MS;
  chimeForHandoff();
  alarmTimer = setInterval(() => {
    if (Date.now() >= alarmStopAt) {
      stopHandoffAlarm();
      return;
    }
    // Re-checked every tick rather than only at start: an agent who silences
    // the chime from the Inbox toggle while it is sounding expects that to
    // take effect now, not after they acknowledge the alert.
    if (!handoffSoundEnabled()) {
      stopHandoffAlarm();
      return;
    }
    chimeForHandoff();
  }, REPEAT_INTERVAL_MS);
  announce(true);
}

export function stopHandoffAlarm(): void {
  if (alarmTimer === null) return;
  clearInterval(alarmTimer);
  alarmTimer = null;
  announce(false);
}

/**
 * Silences the alarm now and lets it return if the visitor is *still* waiting.
 *
 * Distinct from dismissing, and the distinction is the whole point: dismiss
 * says "I am dealing with this", snooze says "not right now" -- and someone
 * who is not dealing with it must be asked again rather than quietly dropped.
 */
export function snoozeHandoffAlarm(minutes: number, stillWaiting: () => boolean): void {
  stopHandoffAlarm();
  setTimeout(
    () => {
      if (stillWaiting()) startHandoffAlarm();
    },
    minutes * 60 * 1000,
  );
}
