"use client";

import { create } from "zustand";
import { persist } from "zustand/middleware";

interface TenantState {
  currentTenantId: string | null;
  setCurrentTenant: (tenantId: string | null) => void;
}

// Not secret -- the backend re-validates real membership on every request
// regardless of what tenant id the client claims (docs/07-tenant-isolation-
// and-rls.md), so persisting this to localStorage for "remember my last
// tenant" convenience carries no risk beyond UX.
export const useTenantStore = create<TenantState>()(
  persist(
    (set) => ({
      currentTenantId: null,
      setCurrentTenant: (tenantId) => set({ currentTenantId: tenantId }),
    }),
    { name: "iam-current-tenant" },
  ),
);
