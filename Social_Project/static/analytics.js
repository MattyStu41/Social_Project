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

  async function boot() {
    try {
      const cfg = await fetch("/api/admin/config").then((r) => r.json());
      state.authRequired = !!cfg.auth_required;
    } catch {}
    if (state.authRequired) {
      try { const s = await fetch("/api/auth/session").then((r) => r.json()); state.authenticated = !!s.authenticated; } catch {}
    } else { state.authenticated = true; }
    $("#login-form").addEventListener("submit", onLogin);
    render();
    if (state.authenticated) refresh();
  }

  function render() {
    if (state.authRequired && !state.authenticated) {
      $("#login").hidden = false; $(".studio-main").hidden = true; return;
    }
    $("#login").hidden = true; $(".studio-main").hidden = false;
  }

  async function refresh() {
    try {
      const [weekly, posts] = await Promise.all([
        api("/api/analytics/weekly"), api("/api/analytics/posts?limit=50")
      ]);
      renderWeekly(weekly);
      renderPosts(posts);
    } catch (e) { console.error(e); }
  }

  function renderWeekly(buckets) {
    const c = $("#weekly");
    if (!buckets.length) { c.innerHTML = `<p class="muted">No published posts yet in the look-back window.</p>`; return; }
    c.innerHTML = "";
    const table = document.createElement("table");
    table.style.width = "100%";
    table.style.borderCollapse = "collapse";
    table.innerHTML = `<thead><tr>
      <th style="text-align:left;padding:6px 0">Week</th>
      <th style="text-align:right">Posts</th>
      <th style="text-align:right">Threads</th>
      <th style="text-align:right">Instagram</th>
      <th style="text-align:right">TikTok</th>
      <th style="text-align:right">Samples w/ metrics</th>
    </tr></thead>`;
    const tb = document.createElement("tbody");
    for (const b of buckets) {
      const row = document.createElement("tr");
      row.innerHTML = `
        <td style="padding:6px 0">${escape(b.week)}</td>
        <td style="text-align:right">${b.posts}</td>
        <td style="text-align:right">${b.platforms.threads || 0}</td>
        <td style="text-align:right">${b.platforms.instagram || 0}</td>
        <td style="text-align:right">${b.platforms.tiktok || 0}</td>
        <td style="text-align:right">${b.samples_with_metrics}</td>`;
      tb.appendChild(row);
    }
    table.appendChild(tb); c.appendChild(table);
  }

  function renderPosts(posts) {
    const c = $("#per-post");
    if (!posts.length) { c.innerHTML = `<p class="muted">No posts yet.</p>`; return; }
    c.innerHTML = "";
    for (const p of posts) {
      const el = document.createElement("div");
      el.className = "override-block";
      const samples = p.samples.map(s =>
        `<div class="meter">
          <span>${escape(s.platform)} ${escape(s.interval)} @ ${escape(s.sampled_at)}</span>
          ${s.error
            ? `<span style="color:var(--danger)">${escape(s.error)}</span>`
            : `<code>${escape(JSON.stringify(s.snapshot))}</code>`}
        </div>`
      ).join("");
      el.innerHTML = `
        <div class="head">
          <div class="name">${escape(p.caption || "(no caption)")}</div>
          <div class="limits">${escape(p.platforms.join(", "))} · ${escape(p.status)}</div>
        </div>
        ${samples || `<p class="muted">No samples yet.</p>`}
      `;
      c.appendChild(el);
    }
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
