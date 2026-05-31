(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

  const state = {
    authRequired: false,
    authenticated: false,
    monthStart: monthStart(new Date()),
    posts: [],
    selectedDateKey: null,
    timezone: "UTC",
  };

  function escape(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function monthStart(d) {
    const x = new Date(d); x.setDate(1); x.setHours(0, 0, 0, 0); return x;
  }

  function dateKey(d) {
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
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
    if (res.status === 204) return null;
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
    if (state.authenticated) refreshAll();
  }

  function render() {
    if (state.authRequired && !state.authenticated) {
      $("#login").hidden = false; $(".calendar-main").hidden = true; return;
    }
    $("#login").hidden = true; $(".calendar-main").hidden = false;
    renderMonth();
    renderDayPanel();
  }

  async function refreshAll() {
    try {
      const [posts, settings] = await Promise.all([
        api("/api/posts?limit=500"),
        api("/api/settings").catch(() => ({ timezone: "UTC" })),
      ]);
      state.posts = posts;
      state.timezone = settings.timezone || "UTC";
      $("#tz-input").value = state.timezone;
      render();
    } catch (e) { console.error(e); }
  }

  function renderMonth() {
    const monthStartDate = state.monthStart;
    const label = monthStartDate.toLocaleString(undefined, { month: "long", year: "numeric", timeZone: state.timezone });
    $("#month-label").textContent = label;

    const firstWeekday = monthStartDate.getDay(); // 0 sun
    const start = new Date(monthStartDate);
    start.setDate(1 - firstWeekday);
    const end = new Date(monthStartDate);
    end.setMonth(end.getMonth() + 1); end.setDate(0);
    const endWeekday = end.getDay();
    const gridEnd = new Date(end);
    gridEnd.setDate(end.getDate() + (6 - endWeekday));

    const todayKey = dateKey(new Date());
    const postsByDate = {};
    for (const p of state.posts) {
      const dt = new Date(p.scheduled_at);
      const key = dateKey(dt);
      (postsByDate[key] = postsByDate[key] || []).push(p);
    }

    const grid = $("#cal-grid");
    grid.innerHTML = "";
    for (let d = new Date(start); d <= gridEnd; d.setDate(d.getDate() + 1)) {
      const cell = document.createElement("div");
      cell.className = "cal-cell";
      const key = dateKey(d);
      cell.dataset.date = key;
      if (d.getMonth() !== monthStartDate.getMonth()) cell.classList.add("other-month");
      if (key === todayKey) cell.classList.add("today");
      if (key === state.selectedDateKey) cell.classList.add("selected");

      const header = document.createElement("div");
      header.className = "date";
      header.textContent = `${d.getDate()}`;
      cell.appendChild(header);

      const list = postsByDate[key] || [];
      for (const p of list.slice(0, 4)) {
        const pill = document.createElement("div");
        pill.className = `cal-pill ${p.status}`;
        pill.draggable = true;
        pill.dataset.postId = p.id;
        pill.title = p.caption;
        pill.textContent = `${new Date(p.scheduled_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", timeZone: state.timezone })} · ${p.platforms.join("/")}`;
        pill.addEventListener("dragstart", (e) => {
          e.dataTransfer.setData("text/plain", p.id);
          e.dataTransfer.effectAllowed = "move";
        });
        cell.appendChild(pill);
      }
      if (list.length > 4) {
        const more = document.createElement("div");
        more.className = "date";
        more.textContent = `+${list.length - 4} more`;
        cell.appendChild(more);
      }

      cell.addEventListener("dragover", (e) => { e.preventDefault(); cell.classList.add("drop-target"); });
      cell.addEventListener("dragleave", () => cell.classList.remove("drop-target"));
      cell.addEventListener("drop", async (e) => {
        e.preventDefault();
        cell.classList.remove("drop-target");
        const postId = e.dataTransfer.getData("text/plain");
        const post = state.posts.find((p) => p.id === postId);
        if (!post) return;
        const orig = new Date(post.scheduled_at);
        const target = new Date(key + "T00:00:00");
        // Preserve the original time of day; only the date moves.
        target.setHours(orig.getHours(), orig.getMinutes(), 0, 0);
        try {
          await api(`/api/posts/${postId}`, { method: "PATCH", body: JSON.stringify({ scheduled_at: target.toISOString() }) });
          toast(`Rescheduled to ${key}`);
          refreshAll();
        } catch (err) { toast(err.message, "error"); }
      });

      cell.addEventListener("click", (e) => {
        if (e.target.closest(".cal-pill")) return;
        state.selectedDateKey = key;
        render();
      });

      grid.appendChild(cell);
    }
  }

  function renderDayPanel() {
    const panel = $("#day-panel");
    if (!state.selectedDateKey) { panel.hidden = true; return; }
    panel.hidden = false;
    $("#day-label").textContent = state.selectedDateKey;
    const list = $("#day-posts");
    list.innerHTML = "";
    const matches = state.posts.filter((p) => dateKey(new Date(p.scheduled_at)) === state.selectedDateKey);
    if (!matches.length) { list.innerHTML = `<p class="muted">Nothing scheduled.</p>`; return; }
    for (const p of matches) {
      const el = document.createElement("article");
      el.className = "post";
      const when = new Date(p.scheduled_at).toLocaleString(undefined, { timeZone: state.timezone });
      el.innerHTML = `
        <div class="muted" style="font-size:12px">${escape(when)} · ${p.platforms.join(", ")} · ${p.status}</div>
        <div style="margin-top:6px; white-space:pre-wrap;">${escape(p.caption)}</div>
      `;
      list.appendChild(el);
    }
  }

  async function saveTz() {
    const tz = $("#tz-input").value.trim() || "UTC";
    try {
      // Validate locally first.
      Intl.DateTimeFormat(undefined, { timeZone: tz });
      await api("/api/settings", { method: "PATCH", body: JSON.stringify({ timezone: tz }) });
      state.timezone = tz;
      toast("Timezone saved");
      render();
    } catch (e) {
      toast(`Invalid timezone: ${e.message}`, "error");
    }
  }

  async function onLogin(e) {
    e.preventDefault();
    $("#login-error").textContent = "";
    try {
      await api("/api/auth/login", { method: "POST", body: JSON.stringify({ password: $("#login-password").value }) });
      state.authenticated = true; render(); refreshAll();
    } catch (err) { $("#login-error").textContent = err.message; }
  }

  function setup() {
    $("#prev-month").addEventListener("click", () => {
      state.monthStart.setMonth(state.monthStart.getMonth() - 1); render();
    });
    $("#next-month").addEventListener("click", () => {
      state.monthStart.setMonth(state.monthStart.getMonth() + 1); render();
    });
    $("#save-tz").addEventListener("click", saveTz);
    $("#login-form").addEventListener("submit", onLogin);
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
