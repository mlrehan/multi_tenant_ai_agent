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

  var title = self.getAttribute("data-title") || "Ask a question";
  var accent = self.getAttribute("data-accent") || "#4f46e5";
  var greeting =
    self.getAttribute("data-greeting") ||
    "Hi! Ask me anything and I'll answer from our documentation.";

  var session = null; // { token, expiresAt }
  var busy = false;

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
    ".head{display:flex;align-items:center;justify-content:space-between;gap:8px;" +
    "padding:12px 14px;background:var(--accent);color:#fff;font-weight:600;}" +
    ".close{background:transparent;border:0;color:#fff;font-size:20px;cursor:pointer;padding:0 4px;}" +
    ".log{flex:1;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:12px;}" +
    ".msg{max-width:88%;padding:9px 12px;border-radius:12px;white-space:pre-wrap;word-wrap:break-word;}" +
    ".msg.you{align-self:flex-end;background:var(--accent);color:#fff;border-bottom-right-radius:4px;}" +
    ".msg.bot{align-self:flex-start;background:#f3f4f6;border-bottom-left-radius:4px;}" +
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
    ".foot{display:flex;gap:8px;padding:10px;border-top:1px solid #e5e7eb;}" +
    ".foot input{flex:1;padding:9px 11px;border:1px solid #d1d5db;border-radius:9px;font:inherit;min-width:0;}" +
    ".foot input:focus{outline:2px solid var(--accent);outline-offset:-1px;}" +
    ".foot button{border:0;border-radius:9px;padding:0 15px;background:var(--accent);" +
    "color:#fff;font:inherit;font-weight:600;cursor:pointer;}" +
    ".foot button[disabled]{opacity:.5;cursor:default;}" +
    "@media (prefers-color-scheme:dark){" +
    ".panel{background:#111827;border-color:#374151;}.w{color:#e5e7eb;}" +
    ".msg.bot{background:#1f2937;}.foot{border-color:#374151;}" +
    ".foot input{background:#1f2937;border-color:#4b5563;color:#e5e7eb;}}" +
    "</style>" +
    '<div class="w">' +
    '<div class="panel" part="panel">' +
    '<div class="head"><span class="t"></span>' +
    '<button class="close" aria-label="Close">&times;</button></div>' +
    '<div class="log" role="log" aria-live="polite"></div>' +
    '<form class="foot">' +
    '<input type="text" autocomplete="off" placeholder="Type your question…" aria-label="Your question">' +
    '<button type="submit">Send</button></form>' +
    "</div>" +
    '<button class="launch" aria-label="Open chat" aria-expanded="false">\u{1F4AC}</button>' +
    "</div>";

  var wrap = root.querySelector(".w");
  wrap.style.setProperty("--accent", accent);
  root.querySelector(".t").textContent = title;

  var panel = root.querySelector(".panel");
  var launch = root.querySelector(".launch");
  var log = root.querySelector(".log");
  var form = root.querySelector(".foot");
  var input = root.querySelector(".foot input");
  var send = root.querySelector(".foot button");

  launch.addEventListener("click", function () {
    var open = panel.classList.toggle("open");
    launch.setAttribute("aria-expanded", String(open));
    if (open) {
      if (!log.childElementCount) bubble("bot", greeting);
      input.focus();
    }
  });
  root.querySelector(".close").addEventListener("click", function () {
    panel.classList.remove("open");
    launch.setAttribute("aria-expanded", "false");
  });

  function bubble(kind, text) {
    var el = document.createElement("div");
    el.className = "msg " + kind;
    // textContent, never innerHTML: the answer is model output built from
    // documents a tenant uploaded, so treating it as markup would let a
    // poisoned document run script on a customer's page.
    el.textContent = text || "";
    log.appendChild(el);
    log.scrollTop = log.scrollHeight;
    return el;
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

  async function ensureSession() {
    // Re-minted a minute early: a token that expires between this check and
    // the request arriving would fail for a reason the visitor cannot act on.
    if (session && session.expiresAt - Date.now() > 60000) return session;

    var res = await apiFetch("/v1/public/chat/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ public_key: publicKey }),
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
    return session;
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

    if (res.status === 429) throw new Error("This chat has reached its daily limit. Please try again tomorrow.");
    if (res.status === 401 || res.status === 404) {
      session = null; // stale or revoked -- next question starts fresh
      throw new Error("This chat session has ended. Please try again.");
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

        if (event === "sources") citations = body.citations;
        else if (event === "token") {
          if (!streaming) {
            // Drops the dots. `textContent = ""` also removes the <i>s, so the
            // indicator cannot survive into the answer.
            answer.className = "msg bot";
            answer.textContent = "";
            streaming = true;
          }
          answer.textContent += body.text;
        } else if (event === "error") {
          // Assigning textContent also drops the waiting dots; the class has
          // to go too or the message renders in the indicator's flex layout.
          answer.className = "msg bot";
          answer.textContent = answer.textContent || "Sorry, the answer could not be completed.";
          citations = null;
        }
        log.scrollTop = log.scrollHeight;
      }
    }

    if (!answer.textContent) {
      // A stream that ended without a single token. The dots must not be left
      // pulsing forever -- that is the one state worse than a plain failure,
      // because it never resolves and the visitor keeps waiting.
      answer.className = "msg bot";
      answer.textContent = "Sorry, no answer came back.";
    }
    // Only after the stream ends, and only the citations the answer actually
    // used -- listing every retrieved passage would imply the answer rests on
    // sources it never cited.
    if (citations) renderCitations(citations);
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var question = input.value.trim();
    if (!question || busy) return;

    input.value = "";
    bubble("you", question);
    busy = true;
    send.disabled = true;

    ask(question)
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
        send.disabled = false;
        input.focus();
      });
  });

  // `document.body` is null if the script is in <head> without `defer`.
  if (document.body) document.body.appendChild(host);
  else document.addEventListener("DOMContentLoaded", function () {
    document.body.appendChild(host);
  });
})();
