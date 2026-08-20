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
 * A short two-tone chime via WebAudio.
 *
 * No audio file: shipping one for a two-note alert is a network request and an
 * asset to host. Browsers block audio until the page has been interacted with,
 * so this is wrapped — a blocked chime must never surface as an error, the
 * toast and the system notification already carry the message.
 */
export function chimeForHandoff(): void {
  try {
    const Ctx =
      window.AudioContext ??
      (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctx) return;
    const ctx = new Ctx();
    [880, 1174].forEach((freq, i) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.frequency.value = freq;
      osc.type = "sine";
      gain.gain.setValueAtTime(0.0001, ctx.currentTime + i * 0.12);
      gain.gain.exponentialRampToValueAtTime(0.12, ctx.currentTime + i * 0.12 + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + i * 0.12 + 0.18);
      osc.connect(gain).connect(ctx.destination);
      osc.start(ctx.currentTime + i * 0.12);
      osc.stop(ctx.currentTime + i * 0.12 + 0.2);
    });
    setTimeout(() => void ctx.close(), 800);
  } catch {
    /* Autoplay policy or no WebAudio — the other channels still fired. */
  }
}
