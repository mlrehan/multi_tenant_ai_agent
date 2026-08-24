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
 * The handoff alert tone via WebAudio.
 *
 * No audio file: shipping one for an alert is a network request and an asset
 * to host, and a generated tone cannot 404 or arrive late.
 *
 * **Deliberately insistent, not a polite ping.** This is the sound that tells
 * a nursery someone is waiting to speak to a human, and the previous
 * two-note chime at 0.12 gain was routinely missed in a room with any
 * background noise. It now repeats three times over ~1.4s at a much higher
 * gain, alternating two tones -- a pattern the ear reads as an alarm rather
 * than as a notification, which is the point.
 *
 * Browsers block audio until the page has been interacted with, so the whole
 * thing is wrapped: a blocked tone must never surface as an error, because the
 * system notification, the toast, the tab title and the vibration all still
 * fired.
 */
export function chimeForHandoff(): void {
  try {
    const Ctx =
      window.AudioContext ??
      (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctx) return;
    const ctx = new Ctx();

    // Three rising two-note pairs. `repeat` and the per-note offsets are what
    // make it read as urgent; a single pair is what it used to be.
    const REPEATS = 3;
    const PAIR_SECONDS = 0.46;
    for (let repeat = 0; repeat < REPEATS; repeat += 1) {
      [880, 1174].forEach((freq, i) => {
        const at = ctx.currentTime + repeat * PAIR_SECONDS + i * 0.16;
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.frequency.value = freq;
        // A square wave carries further through room noise than a sine at the
        // same gain, which is the whole reason for the change.
        osc.type = "square";
        gain.gain.setValueAtTime(0.0001, at);
        gain.gain.exponentialRampToValueAtTime(0.45, at + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, at + 0.22);
        osc.connect(gain).connect(ctx.destination);
        osc.start(at);
        osc.stop(at + 0.24);
      });
    }
    // Closed after the last note has finished, or the tail is cut off.
    setTimeout(() => void ctx.close(), REPEATS * PAIR_SECONDS * 1000 + 600);
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
