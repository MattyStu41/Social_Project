(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

  const state = { authRequired: false, authenticated: false };

  function escape(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  async function api(path, opts = {}) {
    const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
    const res = await fetch(path, { credentials: "same-origin", ...opts, headers });
    if (res.status === 401) { state.authenticated = false; render(); throw new Error("Not authenticated"); }
    if (!res.ok) {
      let detail = `${res.status} ${res.statusText}`;
      try { const body = await res.json(); detail = body.detail || JSON.stringify(body); } catch {}
      throw new Error(detail);
    }
    return res.json();
  }

  let toastTimer = 0;
  function toast(msg, kind = "success") {
    const el = $("#toast"); el.textContent = msg; el.className = `toast ${kind}`; el.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => (el.hidden = true), 3500);
  }

  async function boot() {
    try {
      const cfg = await fetch("/api/admin/config").then((r) => r.json());
      state.authRequired = !!cfg.auth_required;
    } catch {}
    if (state.authRequired) {
      try { const s = await fetch("/api/auth/session").then((r) => r.json()); state.authenticated = !!s.authenticated; } catch {}
    } else { state.authenticated = true; }
    setup();
    render();
  }

  function render() {
    if (state.authRequired && !state.authenticated) {
      $("#login").hidden = false; $(".studio-main").hidden = true; return;
    }
    $("#login").hidden = true; $(".studio-main").hidden = false;
  }

  async function submit(e) {
    e.preventDefault();
    $("#form-error").textContent = "";
    const body = $("#body").value.trim();
    const platforms = $$("input[name=platforms]:checked").map((i) => i.value);
    const count = parseInt($("#count").value || "4", 10);
    if (!body) { $("#form-error").textContent = "Source text is required."; return; }
    if (!platforms.length) { $("#form-error").textContent = "Pick at least one platform."; return; }
    $("#submit-btn").disabled = true;
    try {
      const r = await api("/api/repurpose/split", {
        method: "POST",
        body: JSON.stringify({ body, platforms, candidates_per_platform: count }),
      });
      renderResults(r.platforms);
    } catch (err) {
      $("#form-error").textContent = err.message;
    } finally {
      $("#submit-btn").disabled = false;
    }
  }

  function renderResults(platformResults) {
    const panel = $("#results-panel");
    const out = $("#results");
    out.innerHTML = "";
    panel.hidden = false;
    for (const [platform, candidates] of Object.entries(platformResults)) {
      const block = document.createElement("section");
      block.className = "override-block";
      const header = document.createElement("div");
      header.className = "head";
      header.innerHTML = `<div class="name">${escape(platform)}</div><div class="limits">${candidates.length} candidate(s)</div>`;
      block.appendChild(header);
      if (!candidates.length) {
        const empty = document.createElement("p"); empty.className = "muted";
        empty.textContent = "No candidates produced — try adding more source text.";
        block.appendChild(empty); out.appendChild(block); continue;
      }
      for (const c of candidates) {
        const card = document.createElement("div");
        card.style.borderTop = "1px solid var(--border)";
        card.style.padding = "10px 0";
        const fitsClass = c.fits ? "" : "over";
        card.innerHTML = `
          <div style="white-space:pre-wrap;">${escape(c.text)}</div>
          <div class="meter">
            <span class="${fitsClass}">${c.char_count} chars</span>
            <span class="${fitsClass}">${c.hashtag_count} hashtag(s)</span>
            <span>${c.hashtag_suggestions.length ? "Suggested: " + escape(c.hashtag_suggestions.join(" ")) : ""}</span>
          </div>
          <div style="margin-top:8px; display:flex; gap:8px;">
            <button class="ghost" data-act="copy">Copy text</button>
            <a class="ghost" data-act="studio" style="padding:6px 10px;border-radius:6px;border:1px solid var(--border);text-decoration:none;font-size:13px;">Open in Studio</a>
          </div>
        `;
        const copyBtn = card.querySelector('[data-act="copy"]');
        copyBtn.addEventListener("click", async () => {
          try { await navigator.clipboard.writeText(c.text); toast("Copied"); }
          catch { toast("Copy failed", "error"); }
        });
        const link = card.querySelector('[data-act="studio"]');
        // Studio doesn't yet read a query param; for now, copy + redirect.
        link.addEventListener("click", async (e) => {
          e.preventDefault();
          try { await navigator.clipboard.writeText(c.text); } catch {}
          toast("Caption copied — paste in Studio");
          setTimeout(() => (location.href = "/studio"), 600);
        });
        link.href = "/studio";
        block.appendChild(card);
      }
      out.appendChild(block);
    }
  }

  async function onLogin(e) {
    e.preventDefault();
    $("#login-error").textContent = "";
    try {
      await api("/api/auth/login", { method: "POST", body: JSON.stringify({ password: $("#login-password").value }) });
      state.authenticated = true; render();
    } catch (err) { $("#login-error").textContent = err.message; }
  }

  function setup() {
    $("#repurpose-form").addEventListener("submit", submit);
    $("#login-form").addEventListener("submit", onLogin);
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
