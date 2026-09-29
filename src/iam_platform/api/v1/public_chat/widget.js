/*
 * The embeddable chat widget.
 *
 * Embedded as:
 *   <script src="https://api.example.com/v1/public/chat/widget.js"
 *           data-public-key="wk_..." async></script>
 *
 * Four constraints shape everything below, and each rules out the obvious
 * approach:
 *
 * 1. It runs on someone else's page. It therefore declares exactly one global
 *    (nothing, in fact -- an IIFE), and renders inside a shadow root so the
 *    host's CSS cannot deform it and its CSS cannot touch the host. A widget
 *    that inherits `* { box-sizing: content-box }` from a 2014 stylesheet is
 *    not a widget anyone will keep on their page.
 *
 * 2. It must stay dependency-free. No framework, no build step, no bundler --
 *    which is why this is a plain `.js` file served by the API rather than a
 *    module inside the Next.js console. It is deliberately readable rather
 *    than minified: at this size gzip does more than terseness would, and the
 *    people who most need to read it are the site owners embedding it.
 *
 * 3. `EventSource` cannot be used, despite this being SSE. It only issues GET
 *    and cannot set an `Authorization` header, and the session token must not
 *    travel in a query string where it lands in access logs and Referer
 *    headers. So the stream is read from `fetch` and the frames are parsed by
 *    hand -- that parser is the only genuinely fiddly part of this file.
 *
 * 4. The public key is an identifier, not a secret. It ships in the page
 *    source and grants nothing on its own: the widget row decides which
 *    knowledge base it reads, the origin allowlist decides which sites may
 *    mint a session, and the daily cap decides how much it may spend.
 */
(function () {
  "use strict";

  // Captured immediately: `document.currentScript` is only meaningful while
  // this script is executing, and is null by the time any callback runs.
  var self = document.currentScript;
  if (!self) {
    // Falls back for the rare loader that injects the script without leaving
    // `currentScript` set (some tag managers evaluate it as a string).
    var all = document.querySelectorAll("script[data-public-key]");
    self = all[all.length - 1];
  }
  if (!self) return;

  var publicKey = self.getAttribute("data-public-key");
  if (!publicKey) {
    console.error("[chat-widget] missing data-public-key on the script tag");
    return;
  }

  // The API origin is taken from where this file was loaded, not configured
  // separately: they are the same host by construction, and a second setting
  // is a second thing to get wrong.
  var apiBase = new URL(self.src, window.location.href).href.replace(
    /\/v1\/public\/chat\/widget\.js.*$/,
    ""
  );

  // `data-` attributes are a per-page *override*, not the source of truth. The
  // tenant configures name, title, avatar and greeting on the console's AI
  // Chatbot screen, and those arrive with the session -- so an embed that sets
  // nothing looks exactly like the preview the tenant just approved. Reading
  // the attributes first and never re-reading the server was the reason the
  // two did not match: the console wrote to a database the widget never asked.
  var overrides = {
    name: self.getAttribute("data-title") || null,
    subtitle: self.getAttribute("data-subtitle") || null,
    greeting: self.getAttribute("data-greeting") || null,
  };
  var accent = self.getAttribute("data-accent") || "#0f766e";

  // Placeholders until the session responds. Deliberately the same defaults
  // the server resolves, so the half-second before the first response does not
  // flash a different name.
  var presentation = {
    chatbot_name: overrides.name || "Nursery Support Assistant",
    chatbot_title: overrides.subtitle || "Parent & Nursery Support",
    avatar_key: "nursery-default",
    greeting: overrides.greeting,
    quick_replies: [],
  };

  var session = null; // { token, expiresAt }
  var busy = false;
  var historyLoaded = false;

  /* ------------------------------------------------------- session storage */

  /** Where the last session token for *this* widget is kept between visits.
   *
   *  Keyed by public key so two embeds on one page (a public site widget and a
   *  portal one, say) never resume into each other's conversation -- the key is
   *  already per-widget and already public, so it leaks nothing that the script
   *  tag beside it does not.
   *
   *  `localStorage`, not `sessionStorage`: the requirement is that a visitor
   *  can come back after closing the browser, and `sessionStorage` is cleared
   *  exactly then. */
  var STORE_KEY = "iamchat:session:" + (publicKey || "");

  /** Reading storage is wrapped because it genuinely throws rather than
   *  returning null: Safari in private mode, and any browser where the user
   *  has blocked site data. A widget that failed to open because history could
   *  not be restored would be worse than one that simply starts fresh. */
  function storedToken() {
    try {
      return window.localStorage.getItem(STORE_KEY);
    } catch (e) {
      return null;
    }
  }

  function rememberToken(token) {
    try {
      window.localStorage.setItem(STORE_KEY, token);
    } catch (e) {
      /* Storage unavailable -- this session still works, it just will not
         survive a reload. Nothing the visitor can act on, so nothing is said. */
    }
  }

  /* ---------------------------------------------------------------- shell */

  var host = document.createElement("div");
  host.setAttribute("data-chat-widget", "");
  // The host element itself is positioned; everything else lives in the
  // shadow root. `z-index` is high but finite -- 2147483647 wins fights with
  // the host page's own overlays, which is rude on a page we are a guest on.
  host.style.cssText =
    "position:fixed;bottom:16px;right:16px;z-index:99999;";
  var root = host.attachShadow({ mode: "open" });

  root.innerHTML =
    "<style>" +
    ":host,*{box-sizing:border-box;}" +
    ".w{font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:#111827;}" +
    ".launch{width:56px;height:56px;border-radius:9999px;border:0;cursor:pointer;" +
    "background:var(--accent);color:#fff;font-size:24px;line-height:1;" +
    "box-shadow:0 8px 24px rgba(0,0,0,.18);}" +
    ".panel{display:none;flex-direction:column;width:min(380px,calc(100vw - 32px));" +
    "height:min(560px,calc(100vh - 96px));background:#fff;border:1px solid #e5e7eb;" +
    "border-radius:14px;box-shadow:0 18px 48px rgba(0,0,0,.22);overflow:hidden;}" +
    ".panel.open{display:flex;}" +
    ".head{display:flex;align-items:center;gap:10px;" +
    "padding:12px 14px;background:var(--accent);color:#fff;font-weight:600;}"
    + ".hdav{width:34px;height:34px;border-radius:50%;background:rgba(255,255,255,.22);display:flex;align-items:center;justify-content:center;flex:0 0 auto;}"
    + ".hdav svg{width:18px;height:18px;}"
    + ".hdtx{min-width:0;flex:1;display:flex;flex-direction:column}.hdnm{font-size:14px;line-height:1.25;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}"
    + ".hdsb{font-size:11.5px;font-weight:400;opacity:.85;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}"
    + ".qr{display:flex;flex-wrap:wrap;gap:6px;margin:2px 0 6px}"
    + ".qrb{border:1px solid var(--accent);background:#fff;color:var(--accent);border-radius:999px;padding:6px 12px;font:inherit;font-size:13px;cursor:pointer}"
    + ".qrb:hover{background:var(--accent);color:#fff}" +
    ".close{background:transparent;border:0;color:#fff;font-size:20px;cursor:pointer;padding:0 4px;flex:0 0 auto;}" +
    ".log{flex:1;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:12px;}" +
    ".msg{max-width:88%;padding:9px 12px;border-radius:12px;white-space:pre-wrap;word-wrap:break-word;}" +
    ".msg.you{align-self:flex-end;background:var(--accent);color:#fff;border-bottom-right-radius:4px;}" +
    ".teams{display:flex;flex-wrap:wrap;gap:6px;margin:2px 0 6px}.team{border:1px solid var(--accent);background:#fff;color:var(--accent);border-radius:999px;padding:6px 12px;font:inherit;font-size:13px;cursor:pointer}.team:hover:not(:disabled){background:var(--accent);color:#fff}.team:disabled{opacity:.45;cursor:default}.msg.bot{align-self:flex-start;background:#f3f4f6;border-bottom-left-radius:4px;}" +
    ".msg.err{align-self:stretch;background:#fef2f2;color:#991b1b;font-size:13px;}" +
    // Measured, not guessed: end to end, ~90% of the wall clock passes before
    // the first token exists (~2s retrieval and reranking, then seconds of the
    // model thinking), and the answer then arrives in well under a second. So
    // the streaming works perfectly and the visitor still watches an empty box
    // for five to eight seconds unless something says otherwise.
    ".msg.wait{display:flex;gap:4px;align-items:center;padding:13px 12px;}" +
    ".msg.wait i{width:6px;height:6px;border-radius:50%;background:#9ca3af;" +
    "animation:blink 1.4s infinite both;}" +
    ".msg.wait i:nth-child(2){animation-delay:.2s;}" +
    ".msg.wait i:nth-child(3){animation-delay:.4s;}" +
    "@keyframes blink{0%,80%,100%{opacity:.25;}40%{opacity:1;}}" +
    "@media (prefers-reduced-motion:reduce){.msg.wait i{animation:none;opacity:.6;}}" +
    ".cites{align-self:flex-start;font-size:12px;color:#6b7280;max-width:88%;}" +
    ".cites a{color:#4b5563;}" +
    ".foot{display:flex;align-items:flex-end;gap:8px;padding:10px;border-top:1px solid #e5e7eb;}" +
    // A textarea, one line by default and growing with its content up to a
    // cap -- sized in script below, because CSS `field-sizing` is not in
    // every browser a visitor might bring.
    ".foot textarea{flex:1;resize:none;min-width:0;max-height:120px;overflow-y:hidden;" +
    "padding:8px 11px;border:1px solid #d1d5db;border-radius:18px;font:inherit;line-height:1.45;}" +
    ".foot textarea:focus{outline:2px solid var(--accent);outline-offset:-1px;}" +
    ".foot button{flex:0 0 auto;width:36px;height:36px;border:0;border-radius:50%;background:var(--accent);" +
    "color:#fff;display:flex;align-items:center;justify-content:center;cursor:pointer;}" +
    ".foot button svg{width:17px;height:17px;}" +
    ".foot button[disabled]{opacity:.4;cursor:default;}" +
    // Rendered answers. `.md` turns off the plain bubble's pre-wrap: the
    // markdown renderer lays out its own paragraphs and line breaks.
    ".msg.md{white-space:normal;}" +
    ".md p{margin:0 0 8px;}.md p:last-child{margin-bottom:0;}" +
    ".md .h{font-weight:600;margin:10px 0 4px;}.md .h:first-child{margin-top:0;}" +
    ".md ul,.md ol{margin:4px 0 8px;padding-left:20px;}.md li{margin:2px 0;}" +
    ".md strong{font-weight:600;}" +
    ".md a{color:var(--accent);text-decoration:underline;text-underline-offset:2px;word-break:break-word;}" +
    ".md .ic{font:12.5px ui-monospace,SFMono-Regular,Menlo,monospace;background:rgba(0,0,0,.06);padding:1px 4px;border-radius:4px;}" +
    ".md pre{margin:6px 0 8px;padding:8px 10px;background:rgba(0,0,0,.06);border-radius:8px;overflow-x:auto;" +
    "font:12.5px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;white-space:pre;}" +
    ".md blockquote{margin:6px 0;padding-left:10px;border-left:3px solid #d1d5db;color:#4b5563;}" +
    ".md hr{border:0;border-top:1px solid #e5e7eb;margin:10px 0;}" +
    ".md .tbl{overflow-x:auto;margin:6px 0 8px;}" +
    ".md table{border-collapse:collapse;font-size:13px;}" +
    ".md th,.md td{border:1px solid #e5e7eb;padding:4px 8px;text-align:left;vertical-align:top;}" +
    ".md th{background:rgba(0,0,0,.04);font-weight:600;}" +
    ".md .cite{display:inline-block;min-width:16px;padding:0 4px;margin:0 1px;border-radius:4px;" +
    "background:rgba(0,0,0,.08);font-size:10.5px;font-weight:600;line-height:16px;text-align:center;vertical-align:1px;}" +
    ".meta{align-self:flex-start;max-width:88%;margin-top:-6px;display:flex;flex-direction:column;gap:6px;}" +
    ".srcs{display:flex;flex-direction:column;gap:4px;}" +
    ".srch{font-size:11px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;color:#6b7280;}" +
    ".srcpill{align-self:flex-start;display:inline-flex;align-items:center;gap:7px;padding:3px 9px 3px 4px;" +
    "border:1px solid #e5e7eb;border-radius:999px;background:transparent;color:inherit;font:inherit;font-size:12px;" +
    "font-weight:500;cursor:pointer;}" +
    ".srcpill:hover{background:rgba(0,0,0,.04);}.srcpill:focus-visible{outline:2px solid var(--accent);outline-offset:1px;}" +
    ".srcstack{display:flex;}.srcstack .srcbadge+.srcbadge{margin-left:-6px;}" +
    ".srcbadge{width:18px;height:18px;border-radius:50%;border:1.5px solid #fff;background:#e5e7eb;color:#4b5563;" +
    "font-size:9.5px;font-weight:700;display:inline-flex;align-items:center;justify-content:center;}" +
    ".srcchev{font-size:10px;color:#6b7280;transition:transform .15s;}.srcpill.open .srcchev{transform:rotate(180deg);}" +
    ".srclist{list-style:none;margin:6px 0 0;padding:4px;border:1px solid #e5e7eb;border-radius:12px;" +
    "display:flex;flex-direction:column;gap:2px;}" +
    ".srclist[hidden]{display:none;}" +
    ".src{display:flex;align-items:flex-start;gap:8px;padding:6px 8px;border-radius:9px;" +
    "font-size:12.5px;color:inherit;text-decoration:none;min-width:0;}" +
    "a.src:hover{background:rgba(0,0,0,.04);}a.src:focus-visible{outline:2px solid var(--accent);}" +
    ".srctx{display:flex;flex-direction:column;min-width:0;}" +
    ".srcm{font-size:11px;color:#6b7280;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}" +
    ".srcn{flex:0 0 auto;min-width:18px;height:18px;padding:0 4px;border-radius:5px;background:rgba(0,0,0,.07);" +
    "font-size:10.5px;font-weight:600;display:flex;align-items:center;justify-content:center;}" +
    ".srct{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0;}" +
    ".acts{display:flex;align-items:center;gap:2px;flex-wrap:wrap;}" +
    ".act{width:28px;height:28px;border:0;border-radius:7px;background:transparent;color:#6b7280;" +
    "display:flex;align-items:center;justify-content:center;cursor:pointer;}" +
    ".act svg{width:15px;height:15px;}" +
    ".act:hover:not(:disabled){background:rgba(0,0,0,.06);color:#111827;}" +
    ".act:disabled{cursor:default;opacity:.45;}.act.on{color:var(--accent);opacity:1;}" +
    ".act:focus-visible{outline:2px solid var(--accent);outline-offset:1px;}" +
    ".note{font-size:11.5px;color:#6b7280;margin-left:4px;}.note.bad{color:#b91c1c;}" +
    ".why{display:flex;gap:6px;width:100%;margin-top:2px;}" +
    ".why input{flex:1;min-width:0;padding:6px 9px;border:1px solid #d1d5db;border-radius:8px;font:inherit;font-size:12.5px;}" +
    ".why button{border:0;border-radius:8px;padding:0 10px;background:var(--accent);color:#fff;font:inherit;font-size:12.5px;cursor:pointer;}" +
    ".why button.skip{background:transparent;color:#6b7280;}" +
    "@media (prefers-color-scheme:dark){" +
    ".panel{background:#111827;border-color:#374151;}.w{color:#e5e7eb;}" +
    ".msg.bot{background:#1f2937;}.foot{border-color:#374151;}" +
    ".foot textarea,.why input{background:#1f2937;border-color:#4b5563;color:#e5e7eb;}" +
    ".src,.srcpill,.srclist,.md th,.md td,.md hr{border-color:#374151;}.srcbadge{background:#374151;color:#e5e7eb;border-color:#111827;}.md blockquote{color:#9ca3af;border-color:#4b5563;}" +
    ".md .ic,.md pre,.md .cite,.srcn{background:rgba(255,255,255,.1);}" +
    ".act:hover:not(:disabled){background:rgba(255,255,255,.08);color:#f3f4f6;}}" +
    "</style>" +
    '<div class="w">' +
    '<div class="panel" part="panel">' +
    // Avatar, then a two-line identity block -- the same shape the console's
    // preview renders, so a tenant who approves the preview gets that.
    '<div class="head"><span class="hdav"></span>' +
    '<span class="hdtx"><span class="hdnm"></span><span class="hdsb"></span></span>' +
    '<button class="close" aria-label="Close">&times;</button></div>' +
    '<div class="log" role="log" aria-live="polite"></div>' +
    '<form class="foot">' +
    '<textarea rows="1" autocomplete="off" placeholder="Type your question…" aria-label="Your question" maxlength="4000"></textarea>' +
    '<button type="submit" aria-label="Send" title="Send (Enter)">' +
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m5 12 7-7 7 7"/><path d="M12 19V5"/></svg>' +
    "</button></form>" +
    "</div>" +
    '<button class="launch" aria-label="Open chat" aria-expanded="false">\u{1F4AC}</button>' +
    "</div>";

  var wrap = root.querySelector(".w");
  wrap.style.setProperty("--accent", accent);

  var panel = root.querySelector(".panel");
  var launch = root.querySelector(".launch");
  var log = root.querySelector(".log");
  var form = root.querySelector(".foot");
  var input = root.querySelector(".foot textarea");
  var send = root.querySelector(".foot button");
  var headAvatar = root.querySelector(".hdav");
  var headName = root.querySelector(".hdnm");
  var headTitle = root.querySelector(".hdsb");

  var opened = false;

  launch.addEventListener("click", function () {
    var open = panel.classList.toggle("open");
    launch.setAttribute("aria-expanded", String(open));
    if (!open) return;
    input.focus();
    if (opened) return;
    opened = true;
    // The session is minted on open rather than on first question, because
    // its response is what tells us what this widget is *called*. Waiting for
    // the first question would show the visitor a placeholder header and then
    // rename it underneath them.
    ensureSession()
      .then(function () {
        return restoreHistory();
      })
      .then(function (resumed) {
        // A returning visitor gets their conversation back, not a greeting and
        // an empty panel. Greeting and quick replies are for a genuinely new
        // conversation: re-greeting someone mid-thread reads as though the
        // assistant has forgotten them, which is exactly what it used to do.
        if (resumed) return;
        // Nor someone who already asked something while the panel was still
        // opening: a greeting appended *under* their question reads as the
        // assistant ignoring it.
        if (log.querySelector(".msg.you")) return;
        bubble("bot", currentGreeting());
        renderQuickReplies(presentation.quick_replies);
      })
      .catch(function (err) {
        bubble("err", (err && err.message) || "This chat is unavailable right now.");
      });
  });
  root.querySelector(".close").addEventListener("click", function () {
    panel.classList.remove("open");
    launch.setAttribute("aria-expanded", "false");
  });

  var AVATAR_SVG = {
    "nursery-default":
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 8V4H8"/><rect width="16" height="12" x="4" y="8" rx="2"/><path d="M2 14h2M20 14h2M15 13v2M9 13v2"/></svg>',
    "nursery-bear":
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12.5 3a2 2 0 0 1 2 2c0 .5-.2 1-.5 1.4A7 7 0 0 1 19 13a7 7 0 0 1-14 0 7 7 0 0 1 5-6.6A2 2 0 0 1 12.5 3Z"/><circle cx="9.5" cy="12" r="1"/><circle cx="14.5" cy="12" r="1"/></svg>',
    "nursery-star":
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11.5 2.9a.5.5 0 0 1 .9 0l2.4 5a.5.5 0 0 0 .4.3l5.4.8a.5.5 0 0 1 .3.9l-3.9 3.8a.5.5 0 0 0-.2.4l1 5.4a.5.5 0 0 1-.8.5l-4.8-2.5a.5.5 0 0 0-.5 0L6.9 20a.5.5 0 0 1-.8-.5l1-5.4a.5.5 0 0 0-.2-.4L3 9.9a.5.5 0 0 1 .3-.9l5.4-.8a.5.5 0 0 0 .4-.3Z"/></svg>',
    "nursery-leaf":
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 20A7 7 0 0 1 9.8 6.1C15.5 5 17 4.48 19 2c1 2 2 4.18 2 8 0 5.5-4.78 10-10 10Z"/><path d="M2 21c0-3 1.85-5.36 5.08-6C9.5 14.52 12 13 13 12"/></svg>',
  };

  function avatarMarkup(key) {
    // Own keys only: `__proto__` or `constructor` must not reach innerHTML
    // as anything other than a table entry.
    return Object.prototype.hasOwnProperty.call(AVATAR_SVG, key)
      ? AVATAR_SVG[key]
      : AVATAR_SVG["nursery-default"];
  }

  /** Paints the header from `presentation`.
   *
   *  Besides the static shell, the avatar is the only place this file writes
   *  innerHTML, and it is safe
   *  for a specific reason: the string comes from the `AVATAR_SVG` table above,
   *  chosen by an *asset key* the server validates against a fixed allowlist.
   *  No value from the response is ever interpolated into markup -- the name
   *  and title go through textContent, because they are tenant-typed free text
   *  rendered on a customer's page. */
  function paintHeader() {
    headAvatar.innerHTML = avatarMarkup(presentation.avatar_key);
    headName.textContent = presentation.chatbot_name;
    headTitle.textContent = presentation.chatbot_title;
    launch.setAttribute("aria-label", "Open chat with " + presentation.chatbot_name);
  }

  function currentGreeting() {
    var configured = (presentation.greeting || "").trim();
    if (configured) return configured;
    // Matches the console preview's own fallback sentence, so an unconfigured
    // greeting reads the same in both places.
    return "Hello! I'm the " + presentation.chatbot_name + ". How can I help?";
  }

  /** The opening prompts, exactly as the server sent them.
   *
   *  The list is server-decided -- including whether "Speak to a person" is in
   *  it -- so this function makes no judgement about the tenant's handoff
   *  policy. Pressing one submits it as an ordinary question, which is what
   *  makes the handoff pill work without a second code path: `wants_a_human`
   *  recognises the wording on the way in. */
  function renderQuickReplies(labels) {
    if (!labels || !labels.length) return;
    var row = document.createElement("div");
    row.className = "qr";
    labels.forEach(function (label) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "qrb";
      btn.textContent = label;
      btn.addEventListener("click", function () {
        // The whole row goes on the first press: they are opening prompts, and
        // leaving them under a live conversation invites a second question
        // while the first is still streaming.
        row.remove();
        submit(label);
      });
      row.appendChild(btn);
    });
    log.appendChild(row);
    log.scrollTop = log.scrollHeight;
  }

  paintHeader();

  function bubble(kind, text, markdown) {
    var el = document.createElement("div");
    el.className = "msg " + kind;
    // textContent, never innerHTML: the answer is model output built from
    // documents a tenant uploaded, so treating it as markup would let a
    // poisoned document run script on a customer's page. `renderMarkdown`
    // keeps that rule -- it builds elements and text nodes, never markup.
    if (markdown) {
      el.classList.add("md");
      renderMarkdown(el, text || "", markdown);
    } else {
      el.textContent = text || "";
    }
    log.appendChild(el);
    log.scrollTop = log.scrollHeight;
    return el;
  }

  /* ------------------------------------------------------------ markdown */

  /* The assistant writes markdown -- bold, lists, tables, links -- and showing
   * the raw asterisks and pipes made every answer look broken.
   *
   * **Built from DOM nodes, never from an HTML string.** This is model output
   * derived from tenant-uploaded documents, rendered on a *customer's*
   * website. A markdown library that emits HTML (and then needs sanitising)
   * would reopen the exact hole this file has always kept shut, so the
   * renderer below creates elements and assigns text only through
   * `textContent` / `createTextNode`. There is no code path in it that
   * interprets any part of the answer as markup.
   *
   * Links are the one place a string becomes an attribute, and only an
   * absolute `http(s):` or `mailto:` URL is accepted -- `javascript:` and
   * `data:` fall through to plain text. They open in a new tab with
   * `noopener noreferrer`, so the linked page gets no handle on this one.
   *
   * Deliberately a subset: paragraphs, headings, bold/italic, inline code and
   * fenced blocks, lists, block quotes, rules, pipe tables, links, bare URLs
   * and `[n]` citation markers. That covers what the model actually writes;
   * a full CommonMark parser would be a dependency this file exists to avoid. */

  var SAFE_HREF = /^(https?:\/\/|mailto:)/i;
  var INLINE_PATTERN = [
    /(`[^`\n]+`)/.source, // 1 inline code
    /(\*\*[^*\n]+\*\*|__[^_\n]+__)/.source, // 2 bold
    /(\*[^*\s][^*\n]*\*|\b_[^_\s][^_\n]*_\b)/.source, // 3 italic
    /(\[[^\]\n]+\]\((?:[^()\s]|\([^()\s]*\))+\))/.source, // 4 [text](url)
    /(https?:\/\/[^\s<>()]*[^\s<>().,;:!?'")\]])/.source, // 5 bare URL
    /(\[\d{1,3}\])/.source, // 6 citation marker
  ].join("|");

  function node(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function link(label, href) {
    if (!SAFE_HREF.test(href)) return document.createTextNode(label);
    var a = node("a", null, label);
    a.href = href;
    a.target = "_blank";
    a.rel = "noopener noreferrer nofollow";
    return a;
  }

  /** What a `[n]` marker becomes: the number of the *source card* it points
   *  at, or null when it is not a real citation (the model invented it).
   *
   *  `cites` is a `label -> card number` map for a live answer -- several
   *  passages of one page share a card, so `[1]` `[2]` and `[4]` can all be
   *  card 1 -- or `true` for a restored answer, whose sources were not kept,
   *  in which case the label is shown as written. */
  function citeNumber(cites, n) {
    if (cites === true) return n;
    if (cites && typeof cites === "object" && Object.prototype.hasOwnProperty.call(cites, n)) {
      return cites[n];
    }
    return null;
  }

  /** The last element added to `parent`, skipping whitespace-only text. */
  function lastContent(parent) {
    var node = parent.lastChild;
    while (node && node.nodeType === 3 && !node.textContent.trim()) node = node.previousSibling;
    return node;
  }

  function appendInline(parent, text, cites) {
    // A fresh RegExp per call: the recursion for bold and italic would
    // otherwise share one `lastIndex` and silently skip text.
    var re = new RegExp(INLINE_PATTERN, "g");
    var last = 0;
    var m;
    while ((m = re.exec(text))) {
      if (m.index > last) parent.appendChild(document.createTextNode(text.slice(last, m.index)));
      var t = m[0];
      if (m[1]) parent.appendChild(node("code", "ic", t.slice(1, -1)));
      else if (m[2]) {
        var b = node("strong");
        appendInline(b, t.slice(2, -2), cites);
        parent.appendChild(b);
      } else if (m[3]) {
        var em = node("em");
        appendInline(em, t.slice(1, -1), cites);
        parent.appendChild(em);
      } else if (m[4]) {
        var cut = t.indexOf("](");
        parent.appendChild(link(t.slice(1, cut), t.slice(cut + 2, -1)));
      } else if (m[5]) parent.appendChild(link(t, t));
      else if (m[6]) {
        var card = citeNumber(cites, t.slice(1, -1));
        if (card === null) parent.appendChild(document.createTextNode(t));
        else {
          // `[1][2]` from one page is one source: a chip for the same card
          // right after another is dropped rather than drawn twice.
          var prev = lastContent(parent);
          if (!(prev && prev.className === "cite" && prev.textContent === card)) {
            var c = node("span", "cite", card);
            c.title = (sourceTitles && sourceTitles[card]) || "Source " + card;
            parent.appendChild(c);
          }
        }
      }
      last = m.index + t.length;
    }
    if (last < text.length) parent.appendChild(document.createTextNode(text.slice(last)));
  }

  function tableCells(line) {
    return line
      .trim()
      .replace(/^\|/, "")
      .replace(/\|$/, "")
      .split("|")
      .map(function (c) {
        return c.trim();
      });
  }

  /* Card number -> title, for chip tooltips during the current render. */
  var sourceTitles = null;

  function renderMarkdown(box, text, cites, titles) {
    sourceTitles = titles || null;
    box.textContent = "";
    var lines = String(text).replace(/\r\n?/g, "\n").split("\n");
    var para = null;
    var i = 0;

    while (i < lines.length) {
      var line = lines[i];

      if (/^\s*```/.test(line)) {
        para = null;
        var code = [];
        i++;
        while (i < lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i++]);
        i++; // the closing fence -- or past the end while it is still streaming
        var pre = node("pre");
        pre.appendChild(node("code", null, code.join("\n")));
        box.appendChild(pre);
        continue;
      }
      if (!line.trim()) {
        para = null;
        i++;
        continue;
      }
      var heading = line.match(/^\s*#{1,6}\s+(.*)$/);
      if (heading) {
        para = null;
        var h = node("div", "h");
        h.setAttribute("role", "heading");
        h.setAttribute("aria-level", "4");
        appendInline(h, heading[1], cites);
        box.appendChild(h);
        i++;
        continue;
      }
      if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) {
        para = null;
        box.appendChild(node("hr"));
        i++;
        continue;
      }
      if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
        para = null;
        var table = node("table");
        var head = node("thead");
        var headRow = node("tr");
        tableCells(line).forEach(function (cell) {
          var th = node("th");
          appendInline(th, cell, cites);
          headRow.appendChild(th);
        });
        head.appendChild(headRow);
        table.appendChild(head);
        var body = node("tbody");
        i += 2;
        while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
          var tr = node("tr");
          tableCells(lines[i++]).forEach(function (cell) {
            var td = node("td");
            appendInline(td, cell, cites);
            tr.appendChild(td);
          });
          body.appendChild(tr);
        }
        table.appendChild(body);
        var scroller = node("div", "tbl");
        scroller.appendChild(table);
        box.appendChild(scroller);
        continue;
      }
      var bullet = line.match(/^\s*[-*+]\s+(.*)$/);
      var numbered = line.match(/^\s*(\d+)[.)]\s+(.*)$/);
      if (bullet || numbered) {
        para = null;
        var ordered = !bullet;
        var list = node(ordered ? "ol" : "ul");
        if (ordered && numbered[1] !== "1") list.start = parseInt(numbered[1], 10);
        while (i < lines.length) {
          // An indented bullet under an item is a nested list, not a sibling
          // -- "Pay by bank transfer" / "reference: your child's name" must
          // keep that relationship. One level deep, which is what the model
          // writes; deeper nesting flattens into that level.
          var nested = /^\s{2,}[-*+]\s+(.*)$/.exec(lines[i]);
          if (nested && list.lastChild) {
            var sub = list.lastChild.lastChild;
            if (!sub || sub.nodeName !== "UL") {
              sub = node("ul");
              list.lastChild.appendChild(sub);
            }
            var subItem = node("li");
            appendInline(subItem, nested[1], cites);
            sub.appendChild(subItem);
            i++;
            continue;
          }
          var item = ordered
            ? lines[i].match(/^\s*\d+[.)]\s+(.*)$/)
            : lines[i].match(/^\s*[-*+]\s+(.*)$/);
          if (item) {
            var li = node("li");
            appendInline(li, item[1], cites);
            list.appendChild(li);
            i++;
          } else if (lines[i].trim() && /^\s{2,}/.test(lines[i]) && list.lastChild) {
            // An indented line under an item -- a wrapped line or a nested
            // bullet. Kept inside the item rather than breaking the list.
            list.lastChild.appendChild(node("br"));
            appendInline(list.lastChild, lines[i].trim().replace(/^[-*+]\s+/, "• "), cites);
            i++;
          } else break;
        }
        box.appendChild(list);
        continue;
      }
      var quote = line.match(/^\s*>\s?(.*)$/);
      if (quote) {
        para = null;
        var bq = node("blockquote");
        appendInline(bq, quote[1], cites);
        box.appendChild(bq);
        i++;
        continue;
      }
      // Ordinary text. Consecutive lines stay in one paragraph with a line
      // break between them: in chat, a single newline from the model is
      // almost always meant as one.
      if (!para) {
        para = node("p");
        box.appendChild(para);
      } else para.appendChild(node("br"));
      appendInline(para, line.trim(), cites);
      i++;
    }
  }

  /* --------------------------------------------------------------- icons */

  /* Lucide outlines, built as SVG elements rather than an HTML string. */
  var ICONS = {
    copy: [
      ["rect", { x: 9, y: 9, width: 13, height: 13, rx: 2 }],
      ["path", { d: "M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" }],
    ],
    check: [["path", { d: "M20 6 9 17l-5-5" }]],
    redo: [
      ["path", { d: "M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" }],
      ["path", { d: "M3 3v5h5" }],
    ],
    up: [
      ["path", { d: "M7 10v12" }],
      ["path", { d: "M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z" }],
    ],
    down: [
      ["path", { d: "M17 14V2" }],
      ["path", { d: "M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22a3.13 3.13 0 0 1-3-3.88Z" }],
    ],
  };

  function icon(name) {
    var NS = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "2");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    svg.setAttribute("aria-hidden", "true");
    ICONS[name].forEach(function (part) {
      var el = document.createElementNS(NS, part[0]);
      Object.keys(part[1]).forEach(function (k) {
        el.setAttribute(k, String(part[1][k]));
      });
      svg.appendChild(el);
    });
    return svg;
  }

  function actButton(name, label) {
    var b = node("button", "act");
    b.type = "button";
    b.setAttribute("aria-label", label);
    b.title = label;
    b.appendChild(icon(name));
    return b;
  }

  /** Copies text; resolves whether it worked, so a tick is only shown for a
   *  copy that happened. `navigator.clipboard` needs a secure context -- a
   *  host page on plain HTTP falls back to the legacy command. */
  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text).then(
        function () {
          return true;
        },
        function () {
          return legacyCopy(text);
        }
      );
    }
    return Promise.resolve(legacyCopy(text));
  }

  function legacyCopy(text) {
    try {
      var area = document.createElement("textarea");
      area.value = text;
      area.setAttribute("readonly", "");
      area.style.cssText = "position:fixed;opacity:0;";
      root.appendChild(area);
      area.select();
      var ok = document.execCommand("copy");
      area.remove();
      return ok;
    } catch (e) {
      return false;
    }
  }

  /* ------------------------------------------------- sources and actions */

  function webUrl(value) {
    if (!value) return null;
    try {
      var u = new URL(String(value));
      return u.protocol === "http:" || u.protocol === "https:" ? u : null;
    } catch (e) {
      return null;
    }
  }

  /** One source per document, in ranked order, plus `label -> card number`.
   *
   *  Retrieval returns *passages*, and five of one web page is ordinary --
   *  shown as they came, the visitor saw the same page five times. The server
   *  now sends `doc`, an ordinal that groups passages of one document without
   *  naming it; a server from before that falls back to grouping by location,
   *  which is right for web pages and merely less precise for uploads.
   *  First appearance is the reranker's order, so card 1 is the best source. */
  function groupSources(citations) {
    var groups = [];
    var byKey = {};
    var numberOf = {};
    (citations || []).forEach(function (c) {
      var key = c.doc ? "d:" + c.doc : "s:" + String(c.source || c.label);
      var g = byKey[key];
      if (!g) {
        var url = webUrl(c.url) || webUrl(c.source);
        g = {
          index: String(groups.length + 1),
          labels: [],
          url: url ? url.toString() : null,
          domain: url ? url.hostname.replace(/^www\./, "") : null,
          // A web page's title is public; an upload's name is never sent.
          title: c.title || (url ? url.hostname.replace(/^www\./, "") : "Document"),
          location: url ? null : c.source || null,
        };
        byKey[key] = g;
        groups.push(g);
      }
      g.labels.push(String(c.label));
      numberOf[String(c.label)] = g.index;
    });
    return { groups: groups, numberOf: numberOf };
  }

  /** "3 web pages", "1 document", "2 web pages · 1 document". */
  function describeSources(groups) {
    var web = groups.filter(function (g) { return !!g.url; }).length;
    var docs = groups.length - web;
    var parts = [];
    if (web) parts.push(web + (web === 1 ? " web page" : " web pages"));
    if (docs) parts.push(docs + (docs === 1 ? " document" : " documents"));
    return parts.join(" · ");
  }

  /** The sources an answer *cited*, as a compact pill that expands.
   *
   *  Only cited documents: listing everything retrieved would imply the
   *  answer rests on pages it never drew on. The card never shows a raw URL
   *  -- title and domain only -- and the full address is the hover tooltip.
   *  Links open in a new tab with no handle on this page.
   *
   *  Letter badges, not favicons: fetching a site icon would be an outside
   *  request from the host page carrying which sources this visitor read. */
  function renderSources(parent, citations, cited) {
    if (!citations || !cited || !cited.length) return;
    var groups = groupSources(citations).groups.filter(function (g) {
      return g.labels.some(function (l) { return cited.indexOf(l) !== -1; });
    });
    if (!groups.length) return;

    var box = node("div", "srcs");
    var pill = node("button", "srcpill");
    pill.type = "button";
    pill.setAttribute("aria-expanded", "false");
    var stack = node("span", "srcstack");
    groups.slice(0, 3).forEach(function (g) {
      var badge = node("span", "srcbadge", g.url ? (g.domain || "?").replace(/[^a-z0-9]/gi, "").charAt(0).toUpperCase() : "≡");
      badge.setAttribute("aria-hidden", "true");
      stack.appendChild(badge);
    });
    pill.appendChild(stack);
    pill.appendChild(node("span", null, describeSources(groups)));
    var chevron = node("span", "srcchev", "▾");
    chevron.setAttribute("aria-hidden", "true");
    pill.appendChild(chevron);

    var list = node("ol", "srclist");
    list.hidden = true;
    groups.forEach(function (g, i) {
      var li = node("li");
      var card = node(g.url ? "a" : "div", "src");
      if (g.url) {
        card.href = g.url;
        card.target = "_blank";
        card.rel = "noopener noreferrer nofollow";
        card.title = g.url;
        card.setAttribute("aria-label", "Source " + (i + 1) + ": " + g.title + ", " + g.domain + ". Opens in a new tab.");
      }
      card.appendChild(node("span", "srcn", String(i + 1)));
      var text = node("span", "srctx");
      text.appendChild(node("span", "srct", g.title));
      text.appendChild(
        node("span", "srcm", g.url ? g.domain + " · Web page" : ["Document", g.location].filter(Boolean).join(" · "))
      );
      card.appendChild(text);
      li.appendChild(card);
      list.appendChild(li);
    });

    pill.addEventListener("click", function () {
      var open = list.hidden;
      list.hidden = !open;
      pill.setAttribute("aria-expanded", String(open));
      pill.classList.toggle("open", open);
      if (open) log.scrollTop = Math.min(log.scrollHeight, log.scrollTop + list.offsetHeight);
    });

    box.appendChild(pill);
    box.appendChild(list);
    parent.appendChild(box);
  }

  /** Posts a rating. Resolves on success; rejects with a message to show. */
  async function sendFeedback(rating, question, answer, comment) {
    var s = await ensureSession();
    var res = await apiFetch("/v1/public/chat/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: "Bearer " + s.token },
      body: JSON.stringify({
        rating: rating,
        question: question.slice(0, 4000),
        answer: answer.slice(0, 20000),
        comment: comment || null,
      }),
    });
    if (!res.ok) {
      var body = await res.json().catch(function () {
        return null;
      });
      throw new Error((body && body.detail) || "Feedback could not be sent.");
    }
  }

  /** The row under a finished answer: copy, regenerate (latest only) and
   *  thumbs up/down. Feedback locks once sent -- it is stored as a record of
   *  what the visitor thought, not as a setting to flip. */
  function renderActions(meta, question, answer, canRegenerate) {
    var row = node("div", "acts");
    var copy = actButton("copy", "Copy answer");
    copy.addEventListener("click", function () {
      copyText(answer).then(function (ok) {
        if (!ok) return;
        copy.textContent = "";
        copy.appendChild(icon("check"));
        copy.setAttribute("aria-label", "Copied");
        setTimeout(function () {
          copy.textContent = "";
          copy.appendChild(icon("copy"));
          copy.setAttribute("aria-label", "Copy answer");
        }, 1500);
      });
    });
    row.appendChild(copy);

    if (canRegenerate) {
      var redo = actButton("redo", "Regenerate answer");
      redo.className += " regen";
      redo.addEventListener("click", function () {
        if (busy || handedOff) return;
        regenerate();
      });
      row.appendChild(redo);
    }

    var up = actButton("up", "Helpful");
    var down = actButton("down", "Not helpful");
    var note = node("span", "note");
    note.setAttribute("role", "status");
    var why = null;

    function lock(chosen) {
      up.disabled = true;
      down.disabled = true;
      chosen.classList.add("on");
      chosen.setAttribute("aria-pressed", "true");
    }
    function submitRating(rating, comment) {
      up.disabled = true;
      down.disabled = true;
      note.className = "note";
      note.textContent = "";
      sendFeedback(rating, question, answer, comment).then(
        function () {
          lock(rating === "up" ? up : down);
          if (why) {
            why.remove();
            why = null;
          }
          note.textContent = "Thanks for your feedback";
        },
        function (err) {
          up.disabled = false;
          down.disabled = false;
          note.className = "note bad";
          note.textContent = (err && err.message !== "network" && err.message) || "Feedback could not be sent.";
        }
      );
    }

    up.addEventListener("click", function () {
      submitRating("up", null);
    });
    down.addEventListener("click", function () {
      if (why) return;
      // Optional reason. A bare thumbs-down says something was wrong and
      // nothing about what, which is the part a tenant can act on.
      why = node("div", "why");
      var box = node("input");
      box.type = "text";
      box.maxLength = 1000;
      box.placeholder = "What was wrong? (optional)";
      box.setAttribute("aria-label", "What was wrong with this answer? Optional");
      var go = node("button", null, "Send");
      go.type = "button";
      var skip = node("button", "skip", "Skip");
      skip.type = "button";
      go.addEventListener("click", function () {
        submitRating("down", box.value.trim() || null);
      });
      skip.addEventListener("click", function () {
        submitRating("down", null);
      });
      box.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && !e.isComposing) {
          e.preventDefault();
          submitRating("down", box.value.trim() || null);
        }
      });
      why.appendChild(box);
      why.appendChild(go);
      why.appendChild(skip);
      meta.appendChild(why);
      box.focus();
    });

    row.appendChild(up);
    row.appendChild(down);
    row.appendChild(note);
    meta.appendChild(row);
  }



  /* -------------------------------------------------------- chat history */

  /* How many turns a history page holds. Small on purpose: a returning visitor
   * wants the end of the conversation on screen immediately, not a thousand-row
   * transcript rendered before the panel can open. */
  var HISTORY_PAGE = 10;

  var oldestSeq = null;
  var moreHistory = false;
  var loadingHistory = false;

  /** Renders a turn *above* everything already drawn.
   *
   *  `insertBefore` against the first child rather than `prepend`, because the
   *  log's first child is not always a message -- the greeting and the
   *  quick-reply row live there too, and they must stay at the top of the
   *  thread where they belong. */
  function bubbleBefore(kind, text, anchor, markdown) {
    var el = document.createElement("div");
    el.className = "msg " + kind;
    // textContent for the same reason `bubble` uses it: this is a colleague's
    // free text and model output built from tenant documents, rendered on
    // someone else's page. An AI answer goes through the same DOM-building
    // renderer as a live one, so a restored thread looks like the original.
    if (markdown) {
      el.classList.add("md");
      renderMarkdown(el, text || "", true);
    } else {
      el.textContent = text || "";
    }
    log.insertBefore(el, anchor || log.firstChild);
    return el;
  }

  /** Loads the page of turns above the oldest one on screen.
   *
   *  **Scroll position is preserved by height difference, not by remembering a
   *  row.** Prepending grows the log upward, so an untouched `scrollTop` would
   *  slide the visitor's reading position down by exactly the height added.
   *  Measuring before and after and adding the difference keeps whatever they
   *  were looking at under their eyes -- the standard behaviour, and the only
   *  one that survives rows of different heights.
   *
   *  Paged by `seq` cursor rather than offset: the thread is live, and an
   *  offset counts from a position that moves every time a new message
   *  arrives. */
  async function loadOlderHistory() {
    if (loadingHistory || !moreHistory || oldestSeq === null) return;
    loadingHistory = true;
    var heightBefore = log.scrollHeight;
    var topBefore = log.scrollTop;
    try {
      var s = await ensureSession();
      var res = await apiFetch(
        "/v1/public/chat/messages?history=" + HISTORY_PAGE + "&before=" + oldestSeq,
        { headers: { Authorization: "Bearer " + s.token } }
      );
      if (!res.ok) return;
      var body = await res.json();
      var turns = body.messages || [];
      if (!turns.length) {
        moreHistory = false;
        return;
      }
      // Inserted before a fixed anchor and walked oldest-first, so the page
      // keeps its own order instead of being reversed by repeated prepending.
      var anchor = log.firstChild;
      turns.forEach(function (m) {
        bubbleBefore(m.author === "visitor" ? "you" : "bot", m.content, anchor, m.author === "ai");
      });
      oldestSeq = turns[0].seq;
      moreHistory = body.has_more === true;
      log.scrollTop = topBefore + (log.scrollHeight - heightBefore);
    } catch (e) {
      /* Older turns are still on the server; the visitor can try again by
         scrolling. Nothing here is worth an error bubble in their chat. */
    } finally {
      loadingHistory = false;
    }
  }

  // Infinite scroll upward. The threshold is generous so the fetch starts
  // before the visitor hits the very top -- arriving at a hard stop and *then*
  // waiting is what makes an infinite scroll feel broken.
  log.addEventListener("scroll", function () {
    if (log.scrollTop < 80) void loadOlderHistory();
  });

  /* ---------------------------------------------------- typing indicator */

  /* A colleague composing a reply, shown the way every chat app shows it: a
   * bubble in the thread that is replaced, not appended to. It is deliberately
   * *not* a message -- nothing here is ever added to `log` as a turn, so it
   * cannot end up in the transcript, the retention sweep or the model's
   * prompt. Same reason the server keeps it in a short-lived cache key. */
  var typingEl = null;

  function showAgentTyping(on) {
    if (on && !typingEl) {
      typingEl = document.createElement("div");
      typingEl.className = "msg bot wait";
      // Built element by element, matching how the assistant's own waiting
      // dots are made -- this file writes innerHTML only for fixed markup (the
      // static shell and the avatar table), never for anything built at run
      // time, and there is no reason to start here. Reusing the same treatment also means
      // "someone is composing" looks the same whoever is composing, and the
      // dots are elements rather than text so a screen reader announces
      // nothing: `aria-live` on the log would otherwise read out the wait.
      for (var d = 0; d < 3; d++) typingEl.appendChild(document.createElement("i"));
      log.appendChild(typingEl);
      log.scrollTop = log.scrollHeight;
    } else if (!on && typingEl) {
      typingEl.remove();
      typingEl = null;
    }
  }

  /* The visitor's own heartbeat, throttled.
   *
   * One request per keystroke would be an unbounded write rate on an anonymous
   * endpoint driven by how fast someone types. Instead "still typing" is
   * re-asserted at most every three seconds, against a server key that lives
   * eight -- so a single dropped request cannot make a live typist flicker,
   * and a closed laptop stops the indicator without having to report anything.
   *
   * The stop is sent explicitly rather than left to the key expiring: an
   * indicator still showing under a message that has already arrived reads as
   * a second message coming that never does. */
  var lastTypingPing = 0;
  var typingIdleTimer = null;

  async function postTyping(on) {
    try {
      var s = await ensureSession();
      await apiFetch("/v1/public/chat/typing", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: "Bearer " + s.token,
        },
        body: JSON.stringify({ typing: !!on }),
      });
    } catch (e) {
      /* An indicator nobody sees is not worth surfacing an error for. */
    }
  }

  function noteVisitorTyping() {
    // Only while a colleague is on the other end. In AI mode there is nobody
    // to show it to, and sending anyway would be pure traffic.
    if (!handedOff) return;
    var now = Date.now();
    if (now - lastTypingPing > 3000) {
      lastTypingPing = now;
      postTyping(true);
    }
    // Stops shortly after the last keystroke, rather than waiting for the
    // server key to lapse -- someone who pauses to think has stopped typing.
    if (typingIdleTimer) clearTimeout(typingIdleTimer);
    typingIdleTimer = setTimeout(stopVisitorTyping, 3500);
  }

  function stopVisitorTyping() {
    if (typingIdleTimer) {
      clearTimeout(typingIdleTimer);
      typingIdleTimer = null;
    }
    if (lastTypingPing === 0) return;
    lastTypingPing = 0;
    postTyping(false);
  }

  input.addEventListener("input", function () {
    // An emptied box is not typing -- the visitor deleted what they had.
    if (!input.value) stopVisitorTyping();
    else noteVisitorTyping();
  });

  // A closed tab or a navigation away must end the indicator too. `keepalive`
  // is what lets the request outlive the page; without it the browser cancels
  // it on unload and the colleague watches a ghost indicator until the key
  // lapses.
  window.addEventListener("pagehide", function () {
    if (lastTypingPing === 0 || !session) return;
    try {
      apiFetch("/v1/public/chat/typing", {
        method: "POST",
        keepalive: true,
        headers: {
          "Content-Type": "application/json",
          Authorization: "Bearer " + session.token,
        },
        body: JSON.stringify({ typing: false }),
      });
    } catch (e) {
      /* Best effort by definition -- the page is going away. */
    }
  });

  var handedOff = false;
  var lastSeq = 0;
  var pollTimer = null;
  var polling = false;

  /** Replays the stored conversation into the panel on open.
   *
   *  The turns are already in Postgres -- every widget exchange has been
   *  persisted since conversations gained a retention window -- and the same
   *  endpoint the human leg polls returns them, filtered to what a visitor may
   *  see. What was missing was any way for the browser to *find* them again:
   *  the session id lived only in a JavaScript variable, so a refresh minted a
   *  new one and the previous thread became unreachable.
   *
   *  Resolves true when something was restored, so the caller knows whether to
   *  greet. Failure resolves false rather than rejecting: an unavailable
   *  history is a reason to start a fresh conversation, not to refuse to open
   *  the chat at all. */
  async function restoreHistory() {
    if (historyLoaded) return false;
    historyLoaded = true;
    try {
      var s = await ensureSession();
      // The newest page only. Reading the whole thread was fine for a first
      // conversation and quietly worse every visit after: a returning visitor
      // paid for rendering every turn they had ever exchanged before the panel
      // could open. Older turns arrive by scrolling up.
      var res = await apiFetch("/v1/public/chat/messages?history=" + HISTORY_PAGE, {
        headers: { Authorization: "Bearer " + s.token },
      });
      if (!res.ok) return false;
      var body = await res.json();
      var turns = body.messages || [];
      if (!turns.length) return false;

      // Where scrolling up continues from, and whether there is anything above
      // to continue to.
      oldestSeq = turns[0].seq;
      moreHistory = body.has_more === true;

      turns.forEach(function (m) {
        // The cursor has to end up past everything drawn here, or the first
        // poll of a still-open handoff would render the whole thread again
        // underneath itself.
        if (m.seq > lastSeq) lastSeq = m.seq;
        bubble(m.author === "visitor" ? "you" : "bot", m.content, m.author === "ai" ? true : null);
      });
      log.scrollTop = log.scrollHeight;

      // A conversation a colleague had actually taken is still theirs:
      // restoring the transcript but not the mode would put the visitor's next
      // message to the model while an agent waited on it.
      //
      // A thread merely *queued* is different, and the difference matters now
      // that sessions are durable. Re-entering human mode for one nobody had
      // claimed left the visitor talking to an empty queue with the assistant
      // silenced -- on this visit and every future one, because the session
      // resumes. So the AI keeps answering while they wait; the queue entry
      // stands, and the poll below hands over the moment an agent claims it.
      if (body.with_human === true) enterHumanMode();
      return true;
    } catch (e) {
      return false;
    }
  }

  function startAgentPolling() {
    if (pollTimer) return;
    // Polled, not streamed: an SSE subscription on an anonymous surface is a
    // connection a stranger can hold open for free. Polling runs only while a
    // conversation is actually with a human, so an AI-only session costs
    // nothing extra.
    pollTimer = setInterval(pollAgentMessages, 4000);
    pollAgentMessages();
  }

  async function pollAgentMessages() {
    // **One poll at a time.** `lastSeq` only advances once a response comes
    // back, so on a slow request the next interval tick fires with the cursor
    // unchanged, asks for the same range, and renders the same reply a second
    // time. Seen live: an agent's message appeared twice in the visitor's
    // widget. `setInterval` does not wait for an async callback, so the guard
    // has to be here rather than in the scheduling.
    if (polling) return;
    polling = true;
    try {
      var s = await ensureSession();
      var res = await apiFetch("/v1/public/chat/messages?after=" + lastSeq, {
        headers: { Authorization: "Bearer " + s.token },
      });
      if (!res.ok) return;
      var body = await res.json();
      (body.messages || []).forEach(function (m) {
        // Compared *before* the cursor moves: a response that overlapped an
        // earlier one can carry turns already on screen, and the flag above
        // cannot help there because the duplicate is inside one response set.
        if (m.seq <= lastSeq) return;
        lastSeq = m.seq;
        // The visitor's own turns come back too (one thread, one ordering);
        // skip them so a reply is not echoed underneath itself.
        if (m.author === "visitor") return;
        // The assistant's answers were streamed into the panel as they were
        // generated. The poll runs alongside that while a transfer is queued,
        // so re-rendering them here showed the visitor every AI reply twice.
        // This poll exists to deliver what a *colleague* said.
        if (m.author === "ai") return;
        // `bubble` sets textContent, never innerHTML -- this is a
        // colleague's free text rendered on a customer's page.
        bubble("bot", m.content);
      });
      showAgentTyping(body.agent_typing === true);
      // Handing back is the agent's decision and happens entirely server-side,
      // so this poll -- already running -- is where the widget finds out. The
      // messages above are drained first: a colleague's parting reply and the
      // "back with the assistant" marker arrive in the same response as the
      // flag that ends human mode, and returning early would lose them.
      // The server decides which side owns the conversation, including when the
      // fallback hands it back after a minute of tenant silence; the widget
      // only follows. Both directions, so a claim mid-wait and a timeout are
      // handled by the same line.
      if (body.with_human === false) resumeAi();
      else enterHumanMode();
    } catch (e) {
      /* A failed poll is skipped; the next one recovers. */
    } finally {
      // `finally`, so a thrown request does not wedge polling off for good --
      // the catch above already swallows it, and a permanently-stuck flag
      // would silently end the conversation from the visitor's side.
      polling = false;
    }
  }

  /** Puts the assistant back in charge on the visitor's side.
   *
   *  `handedOff` was a one-way latch -- set when a team was chosen and cleared
   *  by nothing -- so once a conversation had been escalated, every later
   *  message went to the human leg for the rest of the session. After an agent
   *  pressed "Return to AI" the server refused those posts, correctly, because
   *  no colleague owned the thread any more; the widget ignored the refusal and
   *  the visitor was left typing into a panel that took their words and never
   *  answered. */
  /** Hands the visitor's side over to the colleague who owns the thread. */
  function enterHumanMode() {
    if (handedOff) return;
    handedOff = true;
    input.placeholder = "Reply to the team…";
    startAgentPolling();
  }

  function resumeAi() {
    if (!handedOff) return;
    handedOff = false;
    // Neither indicator means anything once the assistant owns the thread
    // again, and a stale one would sit there until the visitor reloaded.
    showAgentTyping(false);
    stopVisitorTyping();
    input.placeholder = "Type your question…";
    if (pollTimer) {
      // Nothing left to poll for -- replies come back through `ask` again, and
      // a timer left running would keep an anonymous session making requests
      // for as long as the tab stays open.
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  async function sendToAgent(text) {
    // The message is here; there is nothing left to be composing.
    stopVisitorTyping();
    // The widget's own bubble helper -- "me" was a class this stylesheet does
    // not define, so the visitor's reply would have rendered unstyled.
    bubble("you", text);
    try {
      var s = await ensureSession();
      var res = await apiFetch("/v1/public/chat/message", {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: "Bearer " + s.token },
        body: JSON.stringify({ content: text }),
      });
      if (res.ok) {
        var d = await res.json();
        // Claim our own seq so the next poll does not echo this message back.
        lastSeq = Math.max(lastSeq, d.seq);
        return;
      }
      // The server refuses this endpoint once the AI owns the thread again.
      // Polling is every four seconds, so a message typed in that window
      // legitimately races the handover -- and a non-ok reply used to be
      // dropped on the floor, losing what the visitor said. Catch up to the
      // state the server just reported and put the question to the assistant
      // instead, which is where it was always going to end up.
      if (res.status === 404) {
        resumeAi();
        await askAndRender(text);
        return;
      }
      bubble("err", "That message could not be sent.");
    } catch (e) {
      bubble("err", "That message could not be sent.");
    }
  }

  function renderTeams(offer) {
    if (!offer.teams || !offer.teams.length) return;
    var row = document.createElement("div");
    row.className = "teams";
    offer.teams.forEach(function (team) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "team";
      btn.textContent = team.label;
      if (team.description) btn.title = team.description;
      btn.addEventListener("click", function () {
        // Disable the whole row on the first press. Without this a double
        // click sends two transfers, and the second would land on a
        // conversation an agent may already have claimed.
        row.querySelectorAll("button").forEach(function (b) { b.disabled = true; });
        chooseTeam(team, offer.reason || "");
      });
      row.appendChild(btn);
    });
    log.appendChild(row);
    log.scrollTop = log.scrollHeight;
  }

  async function chooseTeam(team, reason) {
    var bubble = document.createElement("div");
    bubble.className = "msg bot";
    bubble.textContent = "Transferring…";
    log.appendChild(bubble);
    log.scrollTop = log.scrollHeight;
    try {
      // Goes through the same session and fetch helpers the ask path uses --
      // `ensureSession` mints or reuses the token, `apiFetch` resolves the API
      // origin from this script's own src. Reaching for a bare `fetch` here
      // would be a second, silently diverging way to call the same API.
      var s = await ensureSession();
      var res = await apiFetch("/v1/public/chat/handoff", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: "Bearer " + s.token,
        },
        body: JSON.stringify({ team_id: team.id, reason: reason }),
      });
      if (!res.ok) {
        // Errors carry their own message where the server chose to give one
        // (a 409 when handoff is switched off, a 404 for a team that is gone).
        var detail = await res.json().catch(function () { return null; });
        bubble.textContent =
          (detail && detail.detail) ||
          "Sorry, the transfer could not be completed.";
        return;
      }
      var ok = await res.json();
      bubble.textContent = ok.message;
      // The AI stops, but the *conversation* does not. The composer stays
      // usable so the visitor can answer the colleague -- messages now go to
      // the human leg, never to the model.
      enterHumanMode();
      // Start polling from what the conversation already held at the moment
      // of transfer, not from 0. `lastSeq` is otherwise still its initial
      // value here -- nothing before this point ever advances it -- so the
      // first poll would re-fetch every turn already answered live and
      // render each one a second time.
      if (typeof ok.last_seq === "number") lastSeq = Math.max(lastSeq, ok.last_seq);
      startAgentPolling();
    } catch (e) {
      bubble.textContent = "Sorry, the transfer could not be completed.";
    }
  }

  function renderCitations(citations) {
    if (!citations || !citations.length) return;
    var el = document.createElement("div");
    el.className = "cites";
    el.appendChild(document.createTextNode("Sources: "));
    citations.forEach(function (c, i) {
      if (i) el.appendChild(document.createTextNode(" · "));
      var src = c.source || "";
      if (/^https?:\/\//i.test(src)) {
        var a = document.createElement("a");
        a.href = src;
        a.target = "_blank";
        // Denies the opened page access to `window.opener`, and keeps this
        // page's URL out of its Referer.
        a.rel = "noopener noreferrer";
        a.textContent = "[" + c.label + "] " + src;
        el.appendChild(a);
      } else {
        el.appendChild(
          document.createTextNode("[" + c.label + "]" + (src ? " " + src : ""))
        );
      }
    });
    log.appendChild(el);
    log.scrollTop = log.scrollHeight;
  }

  /* ------------------------------------------------------------- network */

  function apiFetch(path, options) {
    return fetch(apiBase + path, options).catch(function (err) {
      // A blocked CORS response is indistinguishable from an outage to page
      // JS -- the browser hands both to us as a bare TypeError, on purpose.
      // The most likely cause by far is an origin the tenant has not added,
      // so say so rather than leaving the site owner reading a stack trace.
      console.error(
        "[chat-widget] request to " +
          apiBase +
          path +
          " failed. If this page's origin (" +
          window.location.origin +
          ") is not in the widget's allowed origins, the browser blocks the " +
          "response and reports it as a network error.",
        err
      );
      throw new Error("network");
    });
  }

  /* One mint at a time. Opening the panel mints a session, and a visitor who
   * types (or taps a quick reply) before that finishes used to start a
   * *second* mint in parallel -- two sessions for one visitor, observed live
   * as two simultaneous `POST /session`s. Memory and the stored conversation
   * are keyed by session, so the first question could land in a different
   * conversation from everything after it. Every caller now shares the
   * request already in flight. */
  var sessionInFlight = null;

  function ensureSession() {
    // Re-minted a minute early: a token that expires between this check and
    // the request arriving would fail for a reason the visitor cannot act on.
    if (session && session.expiresAt - Date.now() > 60000) return Promise.resolve(session);
    if (!sessionInFlight) {
      sessionInFlight = mintSession().then(
        function (s) {
          sessionInFlight = null;
          return s;
        },
        function (err) {
          sessionInFlight = null;
          throw err;
        }
      );
    }
    return sessionInFlight;
  }

  async function mintSession() {

    var res = await apiFetch("/v1/public/chat/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // The last token this browser held, so the server can carry the session
      // id forward and the visitor keeps the thread they already had. Without
      // it every mint produced a new session id, and since a conversation is
      // found by that id, a page refresh silently started a new conversation
      // and orphaned the old one -- still in Postgres, never visible again.
      body: JSON.stringify({ public_key: publicKey, resume_token: storedToken() }),
    });
    if (!res.ok) {
      throw new Error(
        res.status === 404
          ? "This chat is not available."
          : res.status === 403
          ? "This chat is not enabled for this website."
          : "This chat could not be started."
      );
    }
    var data = await res.json();
    session = {
      token: data.session_token,
      expiresAt: new Date(data.expires_at).getTime(),
    };
    // Stored on every mint, not only the first: the token rotates as it nears
    // expiry, and keeping only the original would mean the stored one aged out
    // and stopped resuming after a single session's lifetime.
    rememberToken(session.token);

    // The console is the source of truth; the script tag can override per page,
    // because whoever pastes the snippet may be running one embed on a parent
    // portal and another on the public site. Applied on every mint, not just
    // the first, so renaming the bot in the console reaches an open tab within
    // one token lifetime rather than never.
    presentation = {
      chatbot_name: overrides.name || data.chatbot_name,
      chatbot_title: overrides.subtitle || data.chatbot_title,
      avatar_key: data.avatar_key,
      greeting: overrides.greeting || data.greeting,
      quick_replies: data.quick_replies || [],
    };
    paintHeader();
    return session;
  }

  /* The latest answered turn: what "Regenerate" re-asks. Only the newest
   * answer offers it -- regenerating an older one would rewrite the middle of
   * a conversation the visitor has already moved past. */
  var lastTurn = null; // { question, bubble, meta }

  function withdrawRegenerate() {
    if (!lastTurn) return;
    var button = lastTurn.meta && lastTurn.meta.querySelector(".regen");
    if (button) button.remove();
    lastTurn = null;
  }

  async function ask(question) {
    var s = await ensureSession();
    var res = await apiFetch("/v1/public/chat/ask", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: "Bearer " + s.token,
      },
      body: JSON.stringify({ question: question }),
    });

    // One message for every allowance -- this widget's day, the organisation's
    // day, or its month. "Tomorrow" was wrong for the monthly one, and which
    // limit it was is the organisation's business, not the visitor's.
    if (res.status === 429) throw new Error("Sorry, this chat can't answer any more questions right now. Please try again later.");
    if (res.status === 401 || res.status === 404) {
      session = null; // stale or revoked -- next question starts fresh
      throw new Error("This chat session has ended. Please try again.");
    }
    // A 400 is the guardrail refusing the *question*, and its message is
    // written for the person who typed it. "Something went wrong" would read
    // as a fault and invite them to retry the identical blocked question --
    // the same defect that once made every widget error look alike.
    if (res.status === 400) {
      var refusal = await res.json().catch(function () { return null; });
      throw new Error(
        (refusal && refusal.detail) || "That question can't be answered here."
      );
    }
    if (!res.ok || !res.body) throw new Error("Sorry, something went wrong.");

    var answer = bubble("bot", "");
    // Three pulsing dots until the first character lands. Built from elements
    // rather than an animated character so a screen reader announces nothing
    // here -- `aria-live` on the log would otherwise read out the wait itself.
    answer.className = "msg bot wait";
    for (var d = 0; d < 3; d++) answer.appendChild(document.createElement("i"));
    var streaming = false;

    var reader = res.body.getReader();
    var decoder = new TextDecoder();
    var buffer = "";
    var citations = null;
    var offered = null; // label -> source card number, for offered labels
    var titles = null; // card number -> title, for chip tooltips
    var cited = [];
    var handoff = null;
    var text = "";
    var failed = false;

    // Re-rendering the markdown on every token would lay the answer out
    // dozens of times a second; once per animation frame is all the eye sees.
    var paintQueued = false;
    function paint() {
      if (paintQueued) return;
      paintQueued = true;
      requestAnimationFrame(function () {
        paintQueued = false;
        renderMarkdown(answer, text, offered, titles);
        log.scrollTop = log.scrollHeight;
      });
    }

    while (true) {
      var step = await reader.read();
      if (step.done) break;
      buffer += decoder.decode(step.value, { stream: true });

      // SSE frames are separated by a blank line. A frame can straddle two
      // network chunks, so the tail stays in the buffer until its terminator
      // arrives -- parsing per chunk would split tokens mid-word.
      var cut;
      while ((cut = buffer.indexOf("\n\n")) !== -1) {
        var frame = buffer.slice(0, cut);
        buffer = buffer.slice(cut + 2);

        var event = "message";
        var payload = "";
        frame.split("\n").forEach(function (line) {
          if (line.indexOf("event:") === 0) event = line.slice(6).trim();
          else if (line.indexOf("data:") === 0) payload += line.slice(5).trim();
        });
        if (!payload) continue;

        var body;
        try {
          body = JSON.parse(payload);
        } catch (e) {
          continue;
        }

        if (event === "sources") {
          citations = body.citations || [];
          // Passages of one document share a card, so `[1]` `[2]` and `[4]`
          // may all become chip 1 -- the same numbering the source list uses.
          var grouped = groupSources(citations);
          offered = grouped.numberOf;
          titles = {};
          grouped.groups.forEach(function (g) {
            titles[g.index] = g.title;
          });
        } else if (event === "handoff") {
          // A transfer offer arrives instead of an answer. Same bubble the
          // dots were in, so the visitor sees one reply, not two.
          answer.className = "msg bot";
          answer.textContent = body.message;
          streaming = true;
          handoff = body;
        } else if (event === "token") {
          if (!streaming) {
            // Drops the dots. `textContent = ""` also removes the <i>s, so the
            // indicator cannot survive into the answer.
            answer.className = "msg bot md";
            answer.textContent = "";
            streaming = true;
          }
          text += body.text;
          paint();
        } else if (event === "done") {
          // Which of the offered sources the answer actually cited. Only
          // these are shown -- see `renderSources`.
          cited = (body.cited || []).map(String);
        } else if (event === "error") {
          failed = true;
          citations = null;
          if (!text) {
            // Assigning textContent also drops the waiting dots; the class has
            // to go too or the message renders in the indicator's flex layout.
            answer.className = "msg bot";
            answer.textContent = "Sorry, the answer could not be completed.";
          }
        }
        log.scrollTop = log.scrollHeight;
      }
    }

    if (handoff) {
      // Buttons, not a typed reply: the visitor's choice goes back as a team
      // id the server validates against that tenant's own teams, so a transfer
      // can never be aimed by typing a team name into the box.
      renderTeams(handoff);
      return;
    }
    if (!text) {
      if (!failed) {
        // A stream that ended without a single token. The dots must not be
        // left pulsing forever -- that is the one state worse than a plain
        // failure, because it never resolves and the visitor keeps waiting.
        answer.className = "msg bot";
        answer.textContent = "Sorry, no answer came back.";
      }
      return;
    }

    // One last paint now rather than in a frame, so the finished answer is
    // laid out before the sources and actions go in beneath it.
    renderMarkdown(answer, text, offered, titles);
    var meta = node("div", "meta");
    log.appendChild(meta);
    // Only after the stream ends, and only the citations the answer actually
    // used -- listing every retrieved passage would imply the answer rests on
    // sources it never cited.
    renderSources(meta, citations, cited);
    if (!failed) {
      lastTurn = { question: question, bubble: answer, meta: meta };
      renderActions(meta, question, text, true);
    }
    log.scrollTop = log.scrollHeight;
  }

  /** Re-asks the latest question and replaces its answer.
   *
   *  It costs exactly what the original did -- a full answer and one unit of
   *  the daily allowance -- and, because the server records every question,
   *  the visitor's history holds the question twice. That is the honest
   *  record of what happened, so it is not hidden. */
  function regenerate() {
    if (!lastTurn || busy || handedOff) return;
    var turn = lastTurn;
    lastTurn = null;
    turn.bubble.remove();
    if (turn.meta) turn.meta.remove();
    return askAndRender(turn.question);
  }

  /* ------------------------------------------------------------ composer */

  /* One line by default, growing with its content up to a cap. Sized here
   * rather than with CSS `field-sizing`, which not every visitor's browser
   * supports. */
  var INPUT_MAX_PX = 120;

  function sizeInput() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, INPUT_MAX_PX) + "px";
    input.style.overflowY = input.scrollHeight > INPUT_MAX_PX ? "auto" : "hidden";
  }

  /** An empty box cannot be sent, and nothing can while an answer streams. */
  function syncSend() {
    send.disabled = busy || !input.value.trim();
  }

  input.addEventListener("input", function () {
    sizeInput();
    syncSend();
  });

  // Enter sends; Shift+Enter is a new line. Except mid-composition: for
  // Japanese, Chinese or Korean input, Enter confirms a character in the IME,
  // and treating that as "send" would fire a half-typed question.
  input.addEventListener("keydown", function (e) {
    if (e.key !== "Enter" || e.shiftKey || e.isComposing) return;
    e.preventDefault();
    if (typeof form.requestSubmit === "function") form.requestSubmit();
    else form.dispatchEvent(new Event("submit", { cancelable: true }));
  });

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var question = input.value.trim();
    if (!question) return;
    input.value = "";
    sizeInput();
    syncSend();
    submit(question);
  });

  syncSend();

  /** One path for everything the visitor says, typed or pressed.
   *
   *  A quick-reply pill that called `ask` directly would skip the handoff
   *  branch below, so "Speak to a person" would reach the model as a question
   *  instead of transferring -- the pill would look right and do nothing. */
  function submit(question) {
    if (!question || busy) return;

    // A new question makes the previous answer history: it keeps copy and
    // feedback, but can no longer be regenerated.
    withdrawRegenerate();

    // Once a colleague owns the conversation the visitor is talking to them,
    // not to the model. Routing this through `ask` would put the assistant
    // back in a thread a human is handling -- and would spend AI quota on
    // human-to-human traffic.
    if (handedOff) {
      sendToAgent(question);
      return;
    }

    bubble("you", question);
    return askAndRender(question);
  }

  /** `ask`, plus the busy state and error rendering around it.
   *
   *  Shared so the handover race in `sendToAgent` reaches the model exactly the
   *  way a typed question does -- a second copy of this would be a second place
   *  for the composer to stay disabled after a failure. It deliberately does
   *  *not* echo the visitor's bubble: both callers have already drawn it, and
   *  drawing it here would double it on the fallback path. */
  function askAndRender(question) {
    busy = true;
    send.disabled = true;

    return ask(question)
      .catch(function (err) {
        bubble(
          "err",
          err && err.message && err.message !== "network"
            ? err.message
            : "Sorry, this chat is unavailable right now."
        );
      })
      .then(function () {
        busy = false;
        syncSend();
        input.focus();
      });
  }

  // `document.body` is null if the script is in <head> without `defer`.
  if (document.body) document.body.appendChild(host);
  else document.addEventListener("DOMContentLoaded", function () {
    document.body.appendChild(host);
  });
})();
