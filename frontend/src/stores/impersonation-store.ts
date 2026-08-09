"use client";

import { create } from "zustand";
import { persist } from "zustand/middleware";

interface ImpersonationDetails {
  tenantId: string;
  targetUserId: string;
  reason: string;
  /** ISO timestamp, computed client-side from `expires_in` at start time. */
  expiresAt: string;
}

// Deliberately does NOT store an impersonation-session id: the backend's
// StartImpersonationResponse never returns one (only access_token/
// expires_in), so the only authoritative source is the `act.imp_sid` claim
// inside the access token itself -- decoded server-side and exposed via
// GET /api/session (see lib/jwt-decode.ts). This store holds only the
// display-only details the JWT doesn't carry (reason, a human-picked
// expiry countdown); components needing the session id to call
// /end must read it from useSession(), not from here.

interface ImpersonationState {
  active: ImpersonationDetails | null;
  start: (details: ImpersonationDetails) => void;
  end: () => void;
}

// sessionStorage, not localStorage: an impersonation session should not
// silently resume in a tab reopened days later. It survives a reload
// within the same tab (so the banner doesn't flicker away), which is the
// whole point of persisting it at all.
export const useImpersonationStore = create<ImpersonationState>()(
  persist(
    (set) => ({
      active: null,
      start: (details) => set({ active: details }),
      end: () => set({ active: null }),
    }),
    {
      name: "iam-impersonation",
      storage: {
        getItem: (name) => {
          const value = sessionStorage.getItem(name);
          return value ? JSON.parse(value) : null;
        },
        setItem: (name, value) => sessionStorage.setItem(name, JSON.stringify(value)),
        removeItem: (name) => sessionStorage.removeItem(name),
      },
    },
  ),
);
