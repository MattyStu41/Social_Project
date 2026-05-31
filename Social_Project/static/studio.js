(() => {
  "use strict";

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

  const LIMITS = {
    threads: { caption: 500, hashtags: 30 },
    instagram: { caption: 2200, hashtags: 30 },
    tiktok: { caption: 2200, hashtags: 100 },
  };
  const PLATFORMS = ["threads", "instagram", "tiktok"];

  const state = {
    authRequired: false,
    authenticated: false,
    overrides: {}, // platform → { enabled, caption, media_url, media_type }
    accounts: {}, // platform → list of {account_id, account_label, ...}
    selectedAccount: {}, // platform → account_id
  };

  function escape(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function countHashtags(s) {
    const re = /(?<![\w&])#([A-Za-z][\w]{0,99})/g;
    return (s.match(re) || []).length;
  }

  async function api(path, opts = {}) {
    const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
    const res = await fetch(path, { credentials: "same-origin", ...opts, headers });
    if (res.status === 401) {
      state.authenticated = false;
      render();
      throw new Error("Not authenticated");
    }
    if (!res.ok) {
      let detail = `${res.status} ${res.statusText}`;
      try {
        const body = await res.json();
        detail = body.detail ? (Array.isArray(body.detail) ? body.detail.map(d => d.msg || JSON.stringify(d)).join("; ") : body.detail) : JSON.stringify(body);
      } catch {}
      throw new Error(detail);
    }
    if (res.status === 204) return null;
    return res.json();
  }

  let toastTimer = 0;
  function toast(message, kind = "success") {
    const el = $("#toast");
    el.textContent = message;
    el.className = `toast ${kind}`;
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (el.hidden = true), 3500);
  }

  async function boot() {
    try {
      const config = await fetch("/api/admin/config").then((r) => r.json());
      state.authRequired = !!config.auth_required;
      $("#version").textContent = `v${config.version}`;
    } catch (e) { console.error(e); }

    if (state.authRequired) {
      try {
        const s = await fetch("/api/auth/session").then((r) => r.json());
        state.authenticated = !!s.authenticated;
      } catch {}
    } else {
      state.authenticated = true;
    }

    setup();
    if (state.authenticated) await loadAccounts();
    render();
  }

  async function loadAccounts() {
    try {
      const platforms = await api("/api/platforms/status");
      const out = {};
      for (const [p, info] of Object.entries(platforms)) {
        out[p] = (info && info.accounts) || [];
      }
      state.accounts = out;
    } catch (e) { console.error("loadAccounts:", e); }
  }

  function render() {
    if (state.authRequired && !state.authenticated) {
      $("#login").hidden = false;
      $(".studio-main").hidden = true;
      $("#logout-btn").hidden = true;
      return;
    }
    $("#login").hidden = true;
    $(".studio-main").hidden = false;
    $("#logout-btn").hidden = !state.authRequired;
    renderOverrides();
    updateMeters();
  }

  function selectedPlatforms() {
    return $$("input[name=platforms]:checked").map((i) => i.value);
  }

  function renderOverrides() {
    const sel = selectedPlatforms();
    const c = $("#overrides-container");
    c.innerHTML = "";
    if (!sel.length) {
      c.innerHTML = `<p class="muted">No platforms selected.</p>`;
      return;
    }
    for (const p of sel) {
      const block = document.createElement("div");
      block.className = "override-block";
      block.dataset.platform = p;
      const lim = LIMITS[p];
      const ov = state.overrides[p] || {};
      const accounts = state.accounts[p] || [];
      const accountPicker = accounts.length > 1
        ? `<label style="display:block; margin-top:8px; font-size:13px">
             Account:
             <select data-role="account" style="margin-left:6px; padding:4px 8px; border-radius:6px; background:var(--panel); color:var(--text); border:1px solid var(--border);">
               ${accounts.map(a => `<option value="${escape(a.account_id)}" ${state.selectedAccount[p] === a.account_id ? "selected" : ""}>${escape(a.account_label || a.user_id || a.account_id)}</option>`).join("")}
             </select>
           </label>`
        : (accounts.length === 1
            ? `<div class="label" style="margin-top:4px">Account: ${escape(accounts[0].account_label || accounts[0].user_id || accounts[0].account_id)}</div>`
            : `<div class="label" style="margin-top:4px; color:var(--danger)">No ${p} account connected.</div>`);
      block.innerHTML = `
        <div class="head">
          <div>
            <div class="name">${p}</div>
            <label class="override-toggle"><input type="checkbox" data-role="enable" ${ov.enabled ? "checked" : ""} />
              Override base caption / media for ${p}
            </label>
            ${accountPicker}
          </div>
          <div class="limits">Caption ≤ ${lim.caption} · Hashtags ≤ ${lim.hashtags}${p === "tiktok" ? " · caption set in TikTok app" : ""}</div>
        </div>
        <textarea data-role="caption" placeholder="Override caption for ${p}…" ${ov.enabled ? "" : "disabled"}>${escape(ov.caption || "")}</textarea>
        <div class="meter">
          <span><span data-role="meter-chars">0</span> / ${lim.caption} chars</span>
          <span><span data-role="meter-tags">0</span> / ${lim.hashtags} hashtags</span>
        </div>
        <div class="row two" style="margin-top: 8px">
          <label class="field" style="margin: 0">
            <span>Override media URL</span>
            <input type="url" data-role="media_url" placeholder="leave blank to use base" value="${escape(ov.media_url || "")}" ${ov.enabled ? "" : "disabled"} />
          </label>
          <label class="field" style="margin: 0">
            <span>Override media type</span>
            <select data-role="media_type" ${ov.enabled ? "" : "disabled"}>
              <option value="">(inherit)</option>
              <option value="IMAGE" ${ov.media_type === "IMAGE" ? "selected" : ""}>Image</option>
              <option value="VIDEO" ${ov.media_type === "VIDEO" ? "selected" : ""}>Video</option>
            </select>
          </label>
        </div>
      `;
      block.querySelector('[data-role="enable"]').addEventListener("change", (e) => {
        state.overrides[p] = state.overrides[p] || {};
        state.overrides[p].enabled = e.target.checked;
        renderOverrides();
      });
      const accountSel = block.querySelector('[data-role="account"]');
      if (accountSel) {
        accountSel.addEventListener("change", () => {
          state.selectedAccount[p] = accountSel.value;
        });
        if (!state.selectedAccount[p] && accounts.length) {
          state.selectedAccount[p] = accounts[0].account_id;
        }
      } else if (accounts.length === 1 && !state.selectedAccount[p]) {
        state.selectedAccount[p] = accounts[0].account_id;
      }
      const ta = block.querySelector('[data-role="caption"]');
      ta.addEventListener("input", () => {
        state.overrides[p] = state.overrides[p] || {};
        state.overrides[p].caption = ta.value;
        updateMeters();
      });
      const mu = block.querySelector('[data-role="media_url"]');
      mu.addEventListener("input", () => {
        state.overrides[p] = state.overrides[p] || {};
        state.overrides[p].media_url = mu.value;
      });
      const mt = block.querySelector('[data-role="media_type"]');
      mt.addEventListener("change", () => {
        state.overrides[p] = state.overrides[p] || {};
        state.overrides[p].media_type = mt.value;
      });
      c.appendChild(block);
    }
    updateMeters();
  }

  function updateMeters() {
    const base = $("#caption").value;
    $("#caption-count").textContent = base.length;
    $("#caption-tags").textContent = countHashtags(base);
    for (const block of $$(".override-block")) {
      const p = block.dataset.platform;
      const ov = state.overrides[p] || {};
      const effective = ov.enabled && ov.caption !== undefined && ov.caption !== "" ? ov.caption : base;
      const lim = LIMITS[p];
      const chars = effective.length;
      const tags = countHashtags(effective);
      const cm = block.querySelector('[data-role="meter-chars"]');
      const tm = block.querySelector('[data-role="meter-tags"]');
      cm.textContent = chars;
      tm.textContent = tags;
      cm.parentElement.classList.toggle("over", chars > lim.caption);
      tm.parentElement.classList.toggle("over", tags > lim.hashtags);
      block.classList.toggle("disabled", !ov.enabled);
    }
  }

  function primeDateTimeDefault() {
    const d = new Date(Date.now() + 60 * 60 * 1000);
    const pad = (n) => String(n).padStart(2, "0");
    $("#scheduled-date").value = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    $("#scheduled-time").value = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  async function submit(e) {
    e.preventDefault();
    $("#form-error").textContent = "";
    const platforms = selectedPlatforms();
    if (!platforms.length) {
      $("#form-error").textContent = "Pick at least one platform.";
      return;
    }
    const caption = $("#caption").value.trim();
    if (!caption) {
      $("#form-error").textContent = "Base caption is required.";
      return;
    }
    const media_url = $("#media-url").value.trim() || null;
    const media_type = $("#media-type").value || null;
    const date = $("#scheduled-date").value;
    const time = $("#scheduled-time").value;
    if (!date || !time) {
      $("#form-error").textContent = "Date and time are required.";
      return;
    }
    const scheduled_at = new Date(`${date}T${time}`).toISOString();

    const platform_overrides = {};
    for (const p of platforms) {
      const ov = state.overrides[p];
      if (!ov || !ov.enabled) continue;
      const block = {};
      if (ov.caption !== undefined && ov.caption !== "") block.caption = ov.caption;
      if (ov.media_url !== undefined && ov.media_url !== "") block.media_url = ov.media_url;
      if (ov.media_type !== undefined && ov.media_type !== "") block.media_type = ov.media_type;
      if (Object.keys(block).length > 0) platform_overrides[p] = block;
    }

    const platform_accounts = {};
    for (const p of platforms) {
      const accounts = state.accounts[p] || [];
      // Only send a pin when there's more than one account; for the single-
      // account case the scheduler picks it automatically.
      if (accounts.length > 1 && state.selectedAccount[p]) {
        platform_accounts[p] = state.selectedAccount[p];
      }
    }
    const payload = {
      caption,
      platforms,
      media_url,
      media_type,
      scheduled_at,
      platform_overrides,
      ...(Object.keys(platform_accounts).length ? { platform_accounts } : {}),
    };
    $("#submit-btn").disabled = true;
    try {
      await api("/api/posts", { method: "POST", body: JSON.stringify(payload) });
      toast("Scheduled — heading back to dashboard");
      setTimeout(() => (location.href = "/"), 600);
    } catch (err) {
      $("#form-error").textContent = err.message;
    } finally {
      $("#submit-btn").disabled = false;
    }
  }

  async function onLogin(e) {
    e.preventDefault();
    $("#login-error").textContent = "";
    const password = $("#login-password").value;
    try {
      await api("/api/auth/login", { method: "POST", body: JSON.stringify({ password }) });
      state.authenticated = true;
      render();
    } catch (err) {
      $("#login-error").textContent = err.message;
    }
  }

  function setup() {
    $("#studio-form").addEventListener("submit", submit);
    $("#caption").addEventListener("input", updateMeters);
    $$("input[name=platforms]").forEach((cb) =>
      cb.addEventListener("change", () => { renderOverrides(); })
    );
    $("#login-form").addEventListener("submit", onLogin);
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "local";
    $("#tz-hint").textContent = `(${tz})`;
    primeDateTimeDefault();
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
