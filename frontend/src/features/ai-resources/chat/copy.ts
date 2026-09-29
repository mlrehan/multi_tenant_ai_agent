/** Copies text, reporting whether it worked.
 *
 *  `navigator.clipboard` exists only in a secure context and can be refused by
 *  the browser, so a bare call can fail silently -- and a "Copied" tick shown
 *  for a copy that did not happen is worse than no tick. The textarea fallback
 *  covers plain-HTTP previews; if both fail the caller shows nothing. */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Fall through to the legacy path.
  }
  try {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(area);
    return ok;
  } catch {
    return false;
  }
}
