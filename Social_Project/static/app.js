(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const state = {
    authRequired: false,
    authenticated: false,
    filter: "all",
    posts: [],
    platforms: {},
    available: {},
    editingId: null,
    alerts: [],
    drafts: [],
  };

  const CAPTION_LIMITS = { threads: 500, instagram: 2200, tiktok: 2200 };

  // ---------- API ----------
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
        detail = body.detail || JSON.stringify(body);
      } catch {}
      throw new Error(detail);
    }
    if (res.status === 204) return null;
    return res.json();
  }

  // ---------- Toast ----------
  let toastTimer = 0;
  function toast(message, kind = "success") {
    const el = $("#toast");
    el.textContent = message;
    el.className = `toast ${kind}`;
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (el.hidden = true), 3500);
  }

  // ---------- Boot ----------
  async function boot() {
    try {
      const config = await fetch("/api/admin/config").then((r) => r.json());
      state.authRequired = !!config.auth_required;
      state.available = config.platforms_available || {};
      $("#version").textContent = `v${config.version}`;
    } catch (e) {
      console.error(e);
    }

    if (state.authRequired) {
      try {
        const s = await fetch("/api/auth/session").then((r) => r.json());
        state.authenticated = !!s.authenticated;
      } catch {}
    } else {
      state.authenticated = true;
    }

    setupHandlers();
    render();
    if (state.authenticated) refreshAll();
    handleConnectedQuery();
  }

  function handleConnectedQuery() {
    const params = new URLSearchParams(location.search);
    if (params.get("connected")) {
      toast(`${params.get("connected")} connected`, "success");
    }
    if (params.get("error")) {
      toast(`OAuth error: ${params.get("error")}`, "error");
    }
    if (params.size) {
      history.replaceState({}, "", location.pathname);
    }
  }

  // ---------- Render ----------
  function render() {
    if (state.authRequired && !state.authenticated) {
      $("#login").hidden = false;
      $(".main").hidden = true;
      $("#logout-btn").hidden = true;
      return;
    }
    $("#login").hidden = true;
    $(".main").hidden = false;
    $("#logout-btn").hidden = !state.authRequired;
    renderAlerts();
    renderPlatforms();
    renderAccountSelectors();
    renderDrafts();
    renderPosts();
  }

  function renderDrafts() {
    const panel = $("#drafts-panel");
    const list = $("#drafts-list");
    list.innerHTML = "";
    if (!state.drafts.length) { panel.hidden = true; return; }
    panel.hidden = false;
    for (const d of state.drafts) {
      const el = document.createElement("article");
      el.className = "post";
      const url = d.source_url ? `<a href="${escape(d.source_url)}" target="_blank" rel="noopener">${escape(d.source_url)}</a>` : "";
      const quotes = (d.pull_quotes || []).map((q, i) =>
        `<label style="display:block;margin:6px 0;cursor:pointer">
           <input type="radio" name="quote-${d.id}" value="${i}" ${i === 0 ? "checked" : ""} />
           <span style="white-space:pre-wrap">${escape(q)}</span>
         </label>`).join("");
      el.dataset.draftId = d.id;
      el.innerHTML = `
        <div class="post-head">
          <div class="post-meta">
            <span class="badge pending">${escape(d.source_kind)}</span>
            <span>${escape(d.title || "(untitled)")}</span>
            <span class="muted">${escape(new Date(d.created_at).toLocaleString())}</span>
          </div>
          <div class="post-actions">
            <button class="ghost" data-act="promote">Promote</button>
            <button class="danger" data-act="discard">Discard</button>
          </div>
        </div>
        <div class="muted" style="margin:6px 0">${url}</div>
        <div>${quotes || "<em class='muted'>No pull-quote candidates extracted.</em>"}</div>
      `;
      list.appendChild(el);
    }
  }

  function renderAlerts() {
    const panel = $("#alerts-panel");
    const list = $("#alerts-list");
    list.innerHTML = "";
    if (!state.alerts.length) {
      panel.hidden = true;
      return;
    }
    panel.hidden = false;
    for (const a of state.alerts) {
      const el = document.createElement("div");
      el.className = "alert";
      el.innerHTML = `
        <span class="sev ${escape(a.severity)}">${escape(a.severity)}</span>
        <div>
          <div class="title">${escape(a.title)}</div>
          <div class="detail">${escape(a.detail)}</div>
        </div>`;
      list.appendChild(el);
    }
  }

  function renderPlatforms() {
    const grid = $("#platforms-grid");
    const meta = [
      { id: "threads", name: "Threads", desc: "Fully auto-published" },
      { id: "instagram", name: "Instagram", desc: "Auto-published (Business/Creator + Page)" },
      { id: "tiktok", name: "TikTok", desc: "Uploaded to drafts" },
    ];
    grid.innerHTML = "";
    for (const m of meta) {
      // D16: platforms[id] returns {accounts: [...], breaker: {...}}.
      const p = state.platforms[m.id] || { accounts: [], breaker: { paused: false } };
      const credsMissing = !state.available[m.id];
      const breaker = p.breaker || { paused: false };
      const paused = breaker.paused;
      const accounts = p.accounts || [];

      const card = document.createElement("div");
      card.className = "platform-card";
      card.style.flexDirection = "column";
      card.style.alignItems = "stretch";

      const dotClass = paused ? "expired" : accounts.length ? "connected" : "";
      const header = document.createElement("div");
      header.innerHTML = `
        <div class="name"><span class="status-dot ${dotClass}"></span>${m.name}</div>
        <div class="label">${m.desc}</div>`;
      card.appendChild(header);

      if (credsMissing) {
        const note = document.createElement("div");
        note.className = "label";
        note.style.marginTop = "8px";
        note.textContent = "Credentials not configured";
        card.appendChild(note);
        grid.appendChild(card);
        continue;
      }

      if (paused) {
        const banner = document.createElement("div");
        banner.className = "label";
        banner.style.color = "var(--danger)";
        banner.style.marginTop = "6px";
        banner.textContent = `Paused until ${new Date(breaker.paused_until).toLocaleTimeString()} (${breaker.consecutive_failures} consecutive failures). Last error: ${breaker.last_error || "n/a"}`;
        card.appendChild(banner);
        const resume = document.createElement("button");
        resume.className = "ghost";
        resume.textContent = "Resume now";
        resume.style.marginTop = "6px";
        resume.style.alignSelf = "flex-start";
        resume.addEventListener("click", () => resumePlatform(m.id));
        card.appendChild(resume);
      }

      if (!accounts.length) {
        const empty = document.createElement("div");
        empty.className = "label";
        empty.style.marginTop = "8px";
        empty.textContent = "No accounts connected.";
        card.appendChild(empty);
      } else {
        for (const a of accounts) {
          const row = document.createElement("div");
          row.style.display = "flex";
          row.style.justifyContent = "space-between";
          row.style.alignItems = "center";
          row.style.gap = "8px";
          row.style.padding = "6px 0";
          row.style.borderTop = "1px solid var(--border)";
          const expHint = (a.expires_in_days !== null && a.expires_in_days !== undefined)
            ? (a.expired ? "expired" : `${a.expires_in_days}d to expiry`)
            : "no expiry on file";
          row.innerHTML = `
            <div>
              <div style="font-weight:600">${escape(a.account_label || a.user_id || a.account_id)}</div>
              <div class="label">${escape(expHint)}</div>
            </div>`;
          const disconnect = document.createElement("button");
          disconnect.className = "danger";
          disconnect.textContent = "Disconnect";
          disconnect.addEventListener("click", () =>
            disconnectAccount(m.id, a.account_id, a.account_label || a.user_id)
          );
          row.appendChild(disconnect);
          card.appendChild(row);
        }
      }

      const connect = document.createElement("a");
      connect.href = `/api/auth/${m.id}/login`;
      connect.className = "ghost";
      connect.style.padding = "8px 12px";
      connect.style.borderRadius = "8px";
      connect.style.textDecoration = "none";
      connect.style.border = "1px solid var(--border)";
      connect.style.marginTop = "10px";
      connect.style.alignSelf = "flex-start";
      connect.textContent = accounts.length ? "Connect another" : "Connect";
      card.appendChild(connect);

      grid.appendChild(card);
    }
  }

  async function disconnectAccount(platform, accountId, label) {
    if (!confirm(`Disconnect ${platform} account ${label || accountId}?`)) return;
    try {
      await api(`/api/auth/${platform}/disconnect?account_id=${encodeURIComponent(accountId)}`, {
        method: "DELETE",
      });
      toast(`${platform} account disconnected`);
      refreshAll();
    } catch (e) {
      toast(e.message, "error");
    }
  }

  async function onDraftsClick(e) {
    const btn = e.target.closest("button[data-act]");
    if (!btn) return;
    const article = e.target.closest("article[data-draft-id]");
    if (!article) return;
    const id = article.dataset.draftId;
    const draft = state.drafts.find((d) => d.id === id);
    if (!draft) return;
    const action = btn.dataset.act;
    try {
      if (action === "discard") {
        if (!confirm("Discard this draft?")) return;
        await api(`/api/drafts/${id}`, { method: "DELETE" });
        toast("Draft discarded");
        refreshAll();
      } else if (action === "promote") {
        const checked = article.querySelector(`input[name="quote-${id}"]:checked`);
        const idx = checked ? parseInt(checked.value, 10) : 0;
        const caption = (draft.pull_quotes || [])[idx] || draft.suggested_caption || draft.title || "";
        const finalCaption = draft.source_url ? `${caption}\n\n${draft.source_url}` : caption;
        // Copy into the compose form and let the operator pick platforms + time.
        $("#caption").value = finalCaption;
        $("#media-url").value = draft.media_url || "";
        updateCaptionCounter();
        window.scrollTo({ top: $("#post-form").offsetTop - 20, behavior: "smooth" });
        toast("Quote loaded into compose — pick platforms and schedule");
      }
    } catch (err) {
      toast(err.message, "error");
    }
  }

  async function resumePlatform(platform) {
    try {
      await api(`/api/platforms/${platform}/resume`, { method: "POST" });
      toast(`${platform} resumed`);
      refreshAll();
    } catch (e) {
      toast(e.message, "error");
    }
  }

  function renderPosts() {
    const list = $("#posts-list");
    const filtered =
      state.filter === "all" ? state.posts : state.posts.filter((p) => p.status === state.filter);

    list.innerHTML = "";
    $("#posts-empty").hidden = filtered.length > 0;

    for (const post of filtered) {
      const el = document.createElement("article");
      el.className = "post";
      const when = new Date(post.scheduled_at).toLocaleString();
      const platforms = post.platforms
        .map((p) => {
          const result = (post.platform_results || {})[p];
          const cls = !result ? "" : result.success ? "ok" : "fail";
          return `<span class="tag ${cls}">${p}</span>`;
        })
        .join("");

      const actions = [];
      if (post.status === "pending") {
        actions.push(`<button class="ghost" data-action="publish-now">Publish now</button>`);
        actions.push(`<button class="ghost" data-action="edit">Edit</button>`);
        actions.push(`<button class="danger" data-action="delete">Delete</button>`);
      } else if (post.status === "failed") {
        actions.push(`<button class="ghost" data-action="retry">Retry</button>`);
        actions.push(`<button class="ghost" data-action="edit">Edit</button>`);
        actions.push(`<button class="danger" data-action="delete">Delete</button>`);
      } else if (post.status === "published") {
        actions.push(`<button class="danger" data-action="delete">Remove</button>`);
      }

      el.innerHTML = `
        <div class="post-head">
          <div class="post-meta">
            <span class="badge ${post.status}">${post.status}</span>
            <span>${when}</span>
            <span class="post-platforms">${platforms}</span>
          </div>
          <div class="post-actions">${actions.join("")}</div>
        </div>
        <div class="post-caption"></div>
        ${post.last_error ? `<div class="post-error">${escape(post.last_error)}</div>` : ""}
      `;
      el.querySelector(".post-caption").textContent = post.caption;
      el.dataset.id = post.id;
      list.appendChild(el);
    }
  }

  function escape(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  // ---------- Data ----------
  async function refreshAll() {
    try {
      const [platforms, posts, alerts, drafts] = await Promise.all([
        api("/api/platforms/status"),
        api("/api/posts"),
        api("/api/admin/alerts").catch(() => ({ alerts: [] })),
        api("/api/drafts").catch(() => []),
      ]);
      state.platforms = platforms;
      state.posts = posts;
      state.alerts = (alerts && alerts.alerts) || [];
      state.drafts = Array.isArray(drafts) ? drafts : [];
      render();
    } catch (e) {
      console.error(e);
    }
  }

  async function disconnectPlatform(platform) {
    // Retained for back-compat; the new card UI uses disconnectAccount.
    if (!confirm(`Disconnect ${platform}?`)) return;
    try {
      await api(`/api/auth/${platform}/disconnect`, { method: "DELETE" });
      toast(`${platform} disconnected`);
      refreshAll();
    } catch (e) {
      toast(e.message, "error");
    }
  }

  // ---------- Form ----------
  function captionLimitFor(platforms) {
    if (!platforms.length) return 2200;
    return Math.min(...platforms.map((p) => CAPTION_LIMITS[p] || 2200));
  }

  function checkedPlatforms() {
    return $$("input[name=platforms]:checked").map((i) => i.value);
  }

  function updateCaptionCounter() {
    const limit = captionLimitFor(checkedPlatforms());
    const len = $("#caption").value.length;
    $("#caption-max").textContent = limit;
    $("#caption-count").textContent = len;
    $("#caption").maxLength = limit;
    $("#caption-count").parentElement.classList.toggle("over", len > limit);
  }

  function renderAccountSelectors() {
    // D16: for each checked platform that has >1 connected account, render a
    // selector. Default is "first account". Stored in a hidden state map.
    const container = $("#account-selectors");
    if (!container) return;
    container.innerHTML = "";
    const checked = checkedPlatforms();
    for (const p of checked) {
      const info = state.platforms[p];
      const accounts = (info && info.accounts) || [];
      if (accounts.length < 2) continue;
      const row = document.createElement("label");
      row.className = "field";
      row.innerHTML = `<span>${p} account</span>`;
      const sel = document.createElement("select");
      sel.dataset.platform = p;
      sel.name = `account-${p}`;
      for (const a of accounts) {
        const opt = document.createElement("option");
        opt.value = a.account_id;
        opt.textContent = a.account_label || a.user_id || a.account_id;
        sel.appendChild(opt);
      }
      row.appendChild(sel);
      container.appendChild(row);
    }
  }

  function selectedAccounts() {
    const out = {};
    for (const sel of $$("#account-selectors select")) {
      out[sel.dataset.platform] = sel.value;
    }
    return out;
  }

  function resetForm() {
    state.editingId = null;
    $("#post-id").value = "";
    $("#post-form").reset();
    $("#compose-title").textContent = "New scheduled post";
    $("#submit-btn").textContent = "Schedule post";
    $("#reset-btn").hidden = true;
    $("#form-error").textContent = "";
    updateCaptionCounter();
    primeDateTimeDefault();
  }

  function primeDateTimeDefault() {
    const d = new Date(Date.now() + 60 * 60 * 1000); // +1 hour
    const pad = (n) => String(n).padStart(2, "0");
    $("#scheduled-date").value = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    $("#scheduled-time").value = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  function fillForm(post) {
    state.editingId = post.id;
    $("#post-id").value = post.id;
    $("#caption").value = post.caption;
    $("#media-url").value = post.media_url || "";
    $("#media-type").value = post.media_type || "";
    $$("input[name=platforms]").forEach((c) => (c.checked = post.platforms.includes(c.value)));
    const d = new Date(post.scheduled_at);
    const pad = (n) => String(n).padStart(2, "0");
    $("#scheduled-date").value = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    $("#scheduled-time").value = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
    $("#compose-title").textContent = "Edit scheduled post";
    $("#submit-btn").textContent = "Save changes";
    $("#reset-btn").hidden = false;
    updateCaptionCounter();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  async function submitForm(e) {
    e.preventDefault();
    $("#form-error").textContent = "";
    const platforms = checkedPlatforms();
    if (!platforms.length) {
      $("#form-error").textContent = "Pick at least one platform.";
      return;
    }
    const caption = $("#caption").value.trim();
    const media_url = $("#media-url").value.trim() || null;
    const media_type = $("#media-type").value || null;
    const date = $("#scheduled-date").value;
    const time = $("#scheduled-time").value;
    if (!date || !time) {
      $("#form-error").textContent = "Date and time are required.";
      return;
    }
    const scheduled_at = new Date(`${date}T${time}`).toISOString();

    const platform_accounts = selectedAccounts();
    const payload = {
      caption,
      platforms,
      media_url,
      media_type,
      scheduled_at,
      ...(Object.keys(platform_accounts).length ? { platform_accounts } : {}),
    };

    $("#submit-btn").disabled = true;
    try {
      if (state.editingId) {
        await api(`/api/posts/${state.editingId}`, {
          method: "PATCH",
          body: JSON.stringify(payload),
        });
        toast("Post updated");
      } else {
        await api("/api/posts", { method: "POST", body: JSON.stringify(payload) });
        toast("Post scheduled");
      }
      resetForm();
      refreshAll();
    } catch (e) {
      $("#form-error").textContent = e.message;
    } finally {
      $("#submit-btn").disabled = false;
    }
  }

  // ---------- Post actions ----------
  async function onListClick(e) {
    const btn = e.target.closest("button[data-action]");
    if (!btn) return;
    const article = e.target.closest(".post");
    if (!article) return;
    const id = article.dataset.id;
    const post = state.posts.find((p) => p.id === id);
    if (!post) return;
    const action = btn.dataset.action;
    try {
      if (action === "delete") {
        if (!confirm("Delete this post?")) return;
        await api(`/api/posts/${id}`, { method: "DELETE" });
        toast("Deleted");
      } else if (action === "publish-now") {
        if (!confirm("Publish this post immediately?")) return;
        await api(`/api/posts/${id}/publish-now`, { method: "POST" });
        toast("Publish triggered");
      } else if (action === "retry") {
        await api(`/api/posts/${id}/retry`, { method: "POST" });
        toast("Retry queued");
      } else if (action === "edit") {
        fillForm(post);
        return;
      }
      refreshAll();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  // ---------- Auth ----------
  async function onLogin(e) {
    e.preventDefault();
    $("#login-error").textContent = "";
    const password = $("#login-password").value;
    try {
      await api("/api/auth/login", { method: "POST", body: JSON.stringify({ password }) });
      state.authenticated = true;
      render();
      refreshAll();
    } catch (err) {
      $("#login-error").textContent = err.message;
    }
  }

  async function onLogout() {
    try {
      await api("/api/auth/logout", { method: "POST" });
    } catch {}
    state.authenticated = false;
    state.posts = [];
    state.platforms = {};
    render();
  }

  // ---------- Setup ----------
  function setupHandlers() {
    $("#post-form").addEventListener("submit", submitForm);
    $("#reset-btn").addEventListener("click", resetForm);
    $("#caption").addEventListener("input", updateCaptionCounter);
    $$("input[name=platforms]").forEach((cb) =>
      cb.addEventListener("change", () => {
        updateCaptionCounter();
        renderAccountSelectors();
      })
    );
    $("#posts-list").addEventListener("click", onListClick);
    $("#drafts-list").addEventListener("click", onDraftsClick);
    $$(".filters .chip").forEach((c) =>
      c.addEventListener("click", () => {
        $$(".filters .chip").forEach((b) => b.classList.remove("active"));
        c.classList.add("active");
        state.filter = c.dataset.filter;
        renderPosts();
      })
    );
    $("#login-form").addEventListener("submit", onLogin);
    $("#logout-btn").addEventListener("click", onLogout);

    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "local";
    $("#tz-hint").textContent = `(${tz})`;

    primeDateTimeDefault();
    updateCaptionCounter();

    // Poll for updates while logged in.
    setInterval(() => {
      if (state.authenticated && !document.hidden) refreshAll();
    }, 15000);
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
