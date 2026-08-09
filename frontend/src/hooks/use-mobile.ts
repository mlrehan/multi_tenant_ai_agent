import * as React from "react"

const MOBILE_BREAKPOINT = 768
const QUERY = `(max-width: ${MOBILE_BREAKPOINT - 1}px)`

/**
 * Rewritten from shadcn's generated version, which set state synchronously
 * inside an effect to seed the initial value -- an eslint error under
 * `react-hooks/set-state-in-effect`. `useSyncExternalStore` is the intended
 * primitive for reading from an external source like matchMedia: it gets
 * the current value during render and subscribes for changes, with no
 * effect-to-state syncing. The server snapshot is `false` so SSR renders
 * the desktop layout, matching the previous `!!isMobile` behaviour when
 * state was still `undefined`.
 */
function subscribe(onChange: () => void) {
  const mql = window.matchMedia(QUERY)
  mql.addEventListener("change", onChange)
  return () => mql.removeEventListener("change", onChange)
}

export function useIsMobile() {
  return React.useSyncExternalStore(
    subscribe,
    () => window.matchMedia(QUERY).matches,
    () => false,
  )
}
