/* Tenant Administrator User Guide — interactions.
   Runs offline from file://. Browser storage is optional: every read and write
   is guarded, and the page works fully without it. */
(function () {
  "use strict";

  var store = {
    get: function (k) { try { return window.localStorage.getItem(k); } catch (e) { return null; } },
    set: function (k, v) { try { window.localStorage.setItem(k, v); } catch (e) { /* storage unavailable */ } },
    del: function (k) { try { window.localStorage.removeItem(k); } catch (e) { /* storage unavailable */ } }
  };
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  /* Honour "reduce motion": jumps instead of smooth scrolls. */
  function scrollMode() {
    return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
  }
  var touchFirst = window.matchMedia && window.matchMedia("(hover: none)").matches;

  /* ---------- theme ---------- */
  var root = document.documentElement;
  var savedTheme = store.get("guide-theme");
  if (savedTheme === "light" || savedTheme === "dark") root.setAttribute("data-theme", savedTheme);
  var themeBtn = $("#themeBtn");
  function effectiveTheme() {
    var t = root.getAttribute("data-theme");
    if (t) return t;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  function paintThemeBtn() { if (themeBtn) themeBtn.textContent = effectiveTheme() === "dark" ? "☀ Light" : "☾ Dark"; }
  paintThemeBtn();
  if (themeBtn) themeBtn.addEventListener("click", function () {
    var next = effectiveTheme() === "dark" ? "light" : "dark";
    root.setAttribute("data-theme", next);
    store.set("guide-theme", next);
    paintThemeBtn();
  });

  /* ---------- mobile navigation ---------- */
  var body = document.body;
  function closeNav() { body.classList.remove("nav-open"); }
  var menuBtn = $("#menuBtn");
  if (menuBtn) menuBtn.addEventListener("click", function () {
    body.classList.toggle("nav-open");
    menuBtn.setAttribute("aria-expanded", body.classList.contains("nav-open") ? "true" : "false");
  });
  var backdrop = $(".backdrop");
  if (backdrop) backdrop.addEventListener("click", closeNav);
  $$(".nav a").forEach(function (a) { a.addEventListener("click", function () { if (window.innerWidth <= 900) closeNav(); }); });

  /* ---------- scrollspy ---------- */
  var navLinks = $$(".nav a[href^='#']");
  var byId = {};
  navLinks.forEach(function (a) { byId[a.getAttribute("href").slice(1)] = a; });
  var targets = $$("section.chapter, .content h3[id]").filter(function (el) { return byId[el.id]; });
  function setActive(id) {
    navLinks.forEach(function (a) { a.classList.remove("active"); });
    $$(".nav li.open").forEach(function (li) { li.classList.remove("open"); });
    var link = byId[id];
    if (!link) return;
    link.classList.add("active");
    var li = link.closest("li.chap");
    if (li) {
      li.classList.add("open");
      var chapLink = li.querySelector(":scope > a");
      if (chapLink && chapLink !== link) chapLink.classList.add("active");
    }
    var nav = $(".nav");
    if (nav && window.innerWidth > 900) {
      var r = link.getBoundingClientRect(), nr = nav.getBoundingClientRect();
      if (r.top < nr.top + 40 || r.bottom > nr.bottom - 40) nav.scrollTop += r.top - nr.top - nr.height / 3;
    }
  }
  function onScroll() {
    var y = window.scrollY + 110, current = null;
    for (var i = 0; i < targets.length; i++) {
      if (targets[i].getBoundingClientRect().top + window.scrollY <= y) current = targets[i].id; else break;
    }
    if (current) setActive(current);
    var h = document.documentElement.scrollHeight - window.innerHeight;
    var bar = $(".progress span");
    if (bar) bar.style.width = (h > 0 ? Math.min(100, (window.scrollY / h) * 100) : 0) + "%";
    var top = $(".to-top");
    if (top) {
      // On a phone the button sits over the screenshots, so it appears only
      // while the reader is scrolling back up -- the moment they want it.
      var goingUp = window.scrollY < lastY;
      var wanted = window.scrollY > 900 && (window.innerWidth > 900 || goingUp);
      top.classList.toggle("show", wanted);
    }
    lastY = window.scrollY;
  }
  var lastY = window.scrollY;
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();
  var toTop = $(".to-top");
  if (toTop) toTop.addEventListener("click", function () { window.scrollTo({ top: 0, behavior: scrollMode() }); });

  /* ---------- collapsibles ---------- */
  $$("[data-fold-all]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var scope = document.getElementById(btn.getAttribute("data-fold-all"));
      var open = btn.getAttribute("data-open") === "true";
      $$("details.fold", scope).forEach(function (d) { d.open = open; });
    });
  });

  /* ---------- search ---------- */
  var index = [];
  (function buildIndex() {
    $$("section.chapter").forEach(function (sec) {
      var chapTitle = ($("h2", sec) || {}).textContent || "";
      var blocks = [];
      var current = { id: sec.id, title: chapTitle, text: "" };
      Array.prototype.forEach.call(sec.querySelectorAll("h3[id], p, li, td, dd, dt, summary, figcaption, .c-title"), function (el) {
        if (el.matches("h3[id]")) {
          blocks.push(current);
          current = { id: el.id, title: el.textContent.trim(), text: "" };
        } else {
          current.text += " " + el.textContent.replace(/\s+/g, " ").trim();
        }
      });
      blocks.push(current);
      blocks.forEach(function (b) { if (b.title) index.push({ id: b.id, title: b.title, chapter: chapTitle.trim(), text: b.text }); });
      // Troubleshooting entries are individually addressable.
      $$("details.fold[id]", sec).forEach(function (d) {
        // The summary's own words, without its category tag ("Access", "Answers"…).
        var title = Array.prototype.filter.call($("summary", d).childNodes, function (n) {
          return !(n.nodeType === 1 && n.classList.contains("tag"));
        }).map(function (n) { return n.textContent; }).join("").trim();
        index.push({ id: d.id, title: title, chapter: chapTitle.trim(), text: d.textContent.replace(/\s+/g, " "), fold: true });
      });
    });
  })();

  var input = $("#search"), results = $("#searchResults"), activeIdx = -1;
  function esc(s) { return s.replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function mark(text, terms) {
    var out = esc(text);
    terms.forEach(function (t) { if (t.length > 1) out = out.replace(new RegExp("(" + t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + ")", "ig"), "<mark>$1</mark>"); });
    return out;
  }
  function search(q) {
    var terms = q.toLowerCase().split(/\s+/).filter(Boolean);
    if (!terms.length) return [];
    return index.map(function (e) {
      var t = e.title.toLowerCase(), x = e.text.toLowerCase(), score = 0;
      for (var i = 0; i < terms.length; i++) {
        var inT = t.indexOf(terms[i]) !== -1, inX = x.indexOf(terms[i]) !== -1;
        if (!inT && !inX) return null;
        score += (inT ? 10 : 0) + (inX ? 2 : 0);
      }
      if (e.fold) score += 1;
      var pos = x.indexOf(terms[0]);
      var snip = pos === -1 ? e.text.slice(0, 140) : e.text.slice(Math.max(0, pos - 50), pos + 110);
      return { e: e, score: score, snip: (pos > 50 ? "…" : "") + snip.trim() + "…", terms: terms };
    }).filter(Boolean).sort(function (a, b) { return b.score - a.score; }).slice(0, 12);
  }
  function render(list) {
    activeIdx = -1;
    if (!input.value.trim()) { results.classList.remove("open"); results.innerHTML = ""; return; }
    if (!list.length) { results.innerHTML = '<div class="search-empty">No results. Try a simpler word, such as “upload”, “limit” or “password”.</div>'; results.classList.add("open"); return; }
    results.innerHTML = list.map(function (r) {
      return '<a href="#' + r.e.id + '" data-fold="' + (r.e.fold ? "1" : "") + '"><div class="r-title">' + mark(r.e.title, r.terms) +
        '</div><div class="r-path">' + esc(r.e.chapter) + '</div><div class="r-snip">' + mark(r.snip, r.terms) + "</div></a>";
    }).join("");
    results.classList.add("open");
  }
  function go(a) {
    var id = a.getAttribute("href").slice(1), el = document.getElementById(id);
    if (!el) return;
    var d = el.matches("details") ? el : el.closest("details");
    if (d) d.open = true;
    results.classList.remove("open");
    closeNav();
    el.scrollIntoView({ behavior: scrollMode(), block: "start" });
    var target = el.matches("details") ? $("summary", el) : el;
    target.classList.add("search-hit");
    setTimeout(function () { target.classList.remove("search-hit"); }, 1600);
    history.replaceState(null, "", "#" + id);
  }
  if (input) {
    input.addEventListener("input", function () { render(search(input.value)); });
    input.addEventListener("keydown", function (ev) {
      var items = $$("a", results);
      if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
        if (!items.length) return;
        ev.preventDefault();
        activeIdx = (activeIdx + (ev.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
        items.forEach(function (a, i) { a.classList.toggle("active", i === activeIdx); });
        items[activeIdx].scrollIntoView({ block: "nearest" });
      } else if (ev.key === "Enter") {
        var pick = items[activeIdx >= 0 ? activeIdx : 0];
        if (pick) { ev.preventDefault(); go(pick); }
      } else if (ev.key === "Escape") {
        input.value = ""; render([]); input.blur();
      }
    });
    results.addEventListener("click", function (ev) {
      var a = ev.target.closest("a");
      if (a) { ev.preventDefault(); go(a); }
    });
    document.addEventListener("click", function (ev) {
      if (!ev.target.closest(".search-wrap")) results.classList.remove("open");
    });
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "/" && document.activeElement !== input && !/INPUT|TEXTAREA/.test(document.activeElement.tagName)) {
        ev.preventDefault();
        if (window.innerWidth <= 900) body.classList.add("nav-open");
        input.focus();
      }
    });
  }

  /* ---------- screenshot gallery + lightbox ---------- */
  var shots = $$("figure.shot").map(function (fig) {
    var img = $("img", fig);
    var cap = $("figcaption", fig);
    return { src: img.getAttribute("src"), alt: img.getAttribute("alt") || "", cap: cap ? cap.textContent.replace(/\s+/g, " ").trim() : "", fig: fig };
  });
  var gallery = $("#galleryGrid");
  if (gallery) {
    gallery.innerHTML = shots.map(function (s, i) {
      return '<button type="button" data-shot="' + i + '" aria-label="Enlarge: ' + esc(s.alt) + '"><img src="' + esc(s.src) + '" alt="" loading="lazy"><span>' + esc(s.cap.replace(/^Figure [\d.]+\s*/, "")) + "</span></button>";
    }).join("");
    var count = $("#galleryCount");
    if (count) count.textContent = String(shots.length);
  }
  var lb = $("#lightbox"), lbImg = $("#lbImg"), lbCap = $("#lbCap"), lbIndex = 0, lastFocus = null;
  function openLb(i) {
    lbIndex = (i + shots.length) % shots.length;
    lbImg.src = shots[lbIndex].src;
    lbImg.alt = shots[lbIndex].alt;
    lbCap.textContent = shots[lbIndex].cap + "  (" + (lbIndex + 1) + " of " + shots.length + ")";
    lb.classList.remove("zoomed");
    if (!lb.classList.contains("open")) { lastFocus = document.activeElement; lb.classList.add("open"); $(".lb-close", lb).focus(); }
    document.body.style.overflow = "hidden";
  }
  function closeLb() { lb.classList.remove("open"); document.body.style.overflow = ""; if (lastFocus) lastFocus.focus(); }
  shots.forEach(function (s, i) {
    var frame = $(".frame", s.fig);
    frame.setAttribute("tabindex", "0");
    frame.setAttribute("role", "button");
    frame.setAttribute("aria-label", "Enlarge screenshot: " + s.alt);
    frame.insertAdjacentHTML("beforeend", '<span class="zoom-hint">' + (touchFirst ? "Tap" : "Click") + " to enlarge</span>");
    frame.addEventListener("click", function () { openLb(i); });
    frame.addEventListener("keydown", function (ev) { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); openLb(i); } });
  });
  if (gallery) gallery.addEventListener("click", function (ev) { var b = ev.target.closest("button[data-shot]"); if (b) openLb(Number(b.getAttribute("data-shot"))); });
  if (lb) {
    $(".lb-close", lb).addEventListener("click", closeLb);
    $(".lb-prev", lb).addEventListener("click", function () { openLb(lbIndex - 1); });
    $(".lb-next", lb).addEventListener("click", function () { openLb(lbIndex + 1); });
    lb.addEventListener("click", function (ev) { if (ev.target === lb) closeLb(); });
    // Narrow screens: a tap on the picture toggles reading size (pan to move around).
    lbImg.addEventListener("click", function () {
      if (window.innerWidth <= 900) lb.classList.toggle("zoomed");
    });
    document.addEventListener("keydown", function (ev) {
      if (!lb.classList.contains("open")) return;
      if (ev.key === "Escape") closeLb();
      else if (ev.key === "ArrowLeft") openLb(lbIndex - 1);
      else if (ev.key === "ArrowRight") openLb(lbIndex + 1);
    });
  }

  /* ---------- checklists (remembered in this browser only) ---------- */
  $$(".check-card[data-list]").forEach(function (card) {
    var key = "guide-check-" + card.getAttribute("data-list");
    var saved = {};
    try { saved = JSON.parse(store.get(key) || "{}") || {}; } catch (e) { saved = {}; }
    var boxes = $$("input[type=checkbox]", card);
    var counter = $(".check-count", card);
    function paint() {
      var done = boxes.filter(function (b) { return b.checked; }).length;
      if (counter) counter.textContent = done + " / " + boxes.length;
    }
    boxes.forEach(function (b, i) {
      b.checked = Boolean(saved[i]);
      b.addEventListener("change", function () { saved[i] = b.checked; store.set(key, JSON.stringify(saved)); paint(); });
    });
    var reset = $("[data-reset]", card);
    if (reset) reset.addEventListener("click", function () { boxes.forEach(function (b) { b.checked = false; }); saved = {}; store.del(key); paint(); });
    paint();
  });

  /* ---------- open a deep-linked troubleshooting entry ---------- */
  if (location.hash) {
    var target = document.getElementById(location.hash.slice(1));
    if (target && target.matches("details")) target.open = true;
  }
})();
