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
