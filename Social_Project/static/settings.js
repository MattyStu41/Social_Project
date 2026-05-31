(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);

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
    $("#login-form").addEventListener("submit", onLogin);
    $("#save-prefs").addEventListener("click", savePrefs);
    $("#preview-purge").addEventListener("click", previewPurge);
    $("#run-purge").addEventListener("click", runPurge);
    render();
    if (state.authenticated) refresh();
  }

  function render() {
    if (state.authRequired && !state.authenticated) { $("#login").hidden = false; $(".studio-main").hidden = true; return; }
    $("#login").hidden = true; $(".studio-main").hidden = false;
  }

  async function refresh() {
    try {
      const s = await api("/api/settings");
      $("#timezone").value = s.timezone;
      $("#retention").value = s.retention_days;
      const audit = await api("/api/settings/audit");
      renderAudit(audit);
    } catch (e) { console.error(e); }
  }

  function renderAudit(items) {
    const c = $("#audit");
    if (!items.length) { c.innerHTML = `<p class="muted">No audit entries yet.</p>`; return; }
    c.innerHTML = "";
    for (const it of items) {
      const el = document.createElement("div");
      el.className = "override-block";
      el.innerHTML = `
        <div class="head"><div class="name">${escape(it.action)}</div>
          <div class="limits">${escape(new Date(it.at).toLocaleString())}</div></div>
        <pre style="white-space:pre-wrap; font-size:12px; margin:8px 0 0;">${escape(JSON.stringify(it.payload, null, 2))}</pre>`;
      c.appendChild(el);
    }
  }

  async function savePrefs() {
    try {
      await api("/api/settings", { method: "PATCH", body: JSON.stringify({
        timezone: $("#timezone").value.trim(),
        retention_days: parseInt($("#retention").value, 10) || undefined,
      })});
      $("#prefs-status").textContent = "Saved.";
      toast("Preferences saved");
    } catch (e) { toast(e.message, "error"); }
  }

  async function previewPurge() {
    const days = parseInt($("#purge-days").value, 10) || undefined;
    try {
      const r = await api(`/api/settings/purge${days ? "?days=" + days : ""}`);
      $("#purge-status").textContent = `Would delete ${r.candidate_count} post(s) older than ${r.threshold} (retention ${r.retention_days}d).`;
    } catch (e) { toast(e.message, "error"); }
  }

  async function runPurge() {
    if (!confirm("Permanently delete published posts older than the retention threshold? This cannot be undone.")) return;
    const days = parseInt($("#purge-days").value, 10) || undefined;
    try {
      const r = await api("/api/settings/purge", {
        method: "POST",
        body: JSON.stringify({ retention_days: days, confirm: true }),
      });
      $("#purge-status").textContent = `Deleted ${r.deleted_count} post(s). Audit id: ${r.audit_id}`;
      toast(`Purged ${r.deleted_count} post(s)`);
      refresh();
    } catch (e) { toast(e.message, "error"); }
  }

  async function onLogin(e) {
    e.preventDefault();
    try {
      await api("/api/auth/login", { method: "POST", body: JSON.stringify({ password: $("#login-password").value }) });
      state.authenticated = true; render(); refresh();
    } catch (err) { $("#login-error").textContent = err.message; }
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
