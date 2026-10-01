/* PotatoDoc superadmin console.
 * Plain ES2019, no build step. Talks to the same FastAPI origin it is served
 * from (/admin -> ../auth, ../admin). Token lives in localStorage.
 */
(function () {
  "use strict";

  var TOKEN_KEY = "potatodocAdminToken";
  var USER_KEY = "potatodocAdminUser";

  var state = {
    token: localStorage.getItem(TOKEN_KEY) || "",
    user: JSON.parse(localStorage.getItem(USER_KEY) || "null"),
    view: "dashboard",
    status: "", // notice filter
    editingId: null, // notice being edited
  };

  var $ = function (sel) { return document.querySelector(sel); };
  var $$ = function (sel) { return Array.prototype.slice.call(document.querySelectorAll(sel)); };

  /* ---------------- helpers ---------------- */

  function toast(msg, isError) {
    var el = $("#toast");
    el.textContent = msg;
    el.classList.toggle("error", !!isError);
    el.classList.remove("hidden");
    clearTimeout(toast._t);
    toast._t = setTimeout(function () { el.classList.add("hidden"); }, 2800);
  }

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function fmtDate(iso) {
    if (!iso) return "";
    var d = new Date(iso.replace(" ", "T") + (iso.indexOf("Z") === -1 && iso.indexOf("+") === -1 ? "Z" : ""));
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleString(undefined, {
      year: "numeric", month: "short", day: "numeric",
      hour: "numeric", minute: "2-digit",
    });
  }

  /** fetch wrapper: attaches the bearer token, JSON in/out, 401 -> logout. */
  async function api(path, options) {
    var opts = options || {};
    var headers = Object.assign({}, opts.headers || {});
    if (state.token) headers.Authorization = "Bearer " + state.token;
    if (opts.body !== undefined && typeof opts.body !== "string") {
      headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.body);
    }
    var res = await fetch(path, Object.assign({}, opts, { headers: headers }));
    if (res.status === 401) {
      signOut(false);
      throw new Error("Session expired — please sign in again.");
    }
    if (res.status === 204) return null;
    var data = null;
    try { data = await res.json(); } catch (e) { data = null; }
    if (!res.ok) {
      var detail = data && data.detail;
      if (Array.isArray(detail)) detail = detail.map(function (d) { return d.msg; }).join(", ");
      throw new Error(detail || "Request failed (" + res.status + ")");
    }
    return data;
  }

  /* ---------------- auth ---------------- */

  function showLogin() {
    $("#app-view").classList.add("hidden");
    $("#login-view").classList.remove("hidden");
    $("#login-error").classList.add("hidden");
    $("#login-password").value = "";
  }

  function showApp() {
    $("#login-view").classList.add("hidden");
    $("#app-view").classList.remove("hidden");
    var u = state.user || {};
    $("#sidebar-user").innerHTML =
      esc(u.name || "Super Admin") + "<small>" + esc(u.contact || "") + "</small>";
    renderOverview();
    renderUsers();
    renderNotices();
  }

  function signOut(callServer) {
    if (callServer !== false && state.token) {
      fetch("/auth/logout", {
        method: "POST",
        headers: { Authorization: "Bearer " + state.token },
      }).catch(function () {});
    }
    state.token = "";
    state.user = null;
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    showLogin();
  }

  async function handleLogin(e) {
    e.preventDefault();
    var btn = $("#login-btn");
    var errBox = $("#login-error");
    btn.disabled = true;
    btn.textContent = "Signing in…";
    errBox.classList.add("hidden");
    try {
      var data = await api("/auth/login", {
        method: "POST",
        body: {
          contact: $("#login-contact").value.trim(),
          password: $("#login-password").value,
        },
      });
      if (!data.user || data.user.role !== "superadmin") {
        throw new Error("That account is not a superadmin.");
      }
      state.token = data.token;
      state.user = data.user;
      localStorage.setItem(TOKEN_KEY, data.token);
      localStorage.setItem(USER_KEY, JSON.stringify(data.user));
      showApp();
      toast("Signed in as " + (data.user.name || "superadmin"));
    } catch (err) {
      errBox.textContent = err.message || "Sign in failed.";
      errBox.classList.remove("hidden");
    } finally {
      btn.disabled = false;
      btn.textContent = "Sign in";
    }
  }

  /* ---------------- navigation ---------------- */

  var TITLES = { dashboard: "Dashboard", users: "Users", notices: "Notices" };

  function navigate(view) {
    state.view = view;
    $$(".nav-item").forEach(function (el) {
      el.classList.toggle("active", el.dataset.nav === view);
    });
    $$(".view").forEach(function (el) { el.classList.add("hidden"); });
    var target = $("#view-" + view);
    if (target) target.classList.remove("hidden");
    $("#page-title").textContent = TITLES[view] || "Dashboard";
    if (view === "dashboard") renderOverview();
    if (view === "users") renderUsers();
    if (view === "notices") renderNotices();
  }

  /* ---------------- dashboard ---------------- */

  var STAT_LABELS = {
    users: "Farmer accounts",
    sessions: "Active sessions",
    history_items: "Diagnoses recorded",
    notices_published: "Published notices",
    notices_draft: "Draft notices",
  };

  var TREND_LABELS = {
    new_users_7d: "New farmers (7d)",
    new_diagnoses_7d: "Diagnoses (7d)",
    sessions_24h: "Sessions (24h)",
    diagnoses_24h: "Diagnoses (24h)",
  };

  /** Horizontal bars from a {label: count} tally; width proportional to max. */
  function renderBars(el, tally) {
    var keys = Object.keys(tally || {});
    if (!keys.length) {
      el.innerHTML = '<p class="muted">No data yet.</p>';
      return;
    }
    var max = Math.max.apply(null, keys.map(function (k) { return tally[k]; }));
    el.innerHTML = keys.map(function (k) {
      var pct = max > 0 ? Math.max(2, Math.round((tally[k] / max) * 100)) : 0;
      return '<div class="bar-row">' +
        '<span class="bar-label">' + esc(k) + "</span>" +
        '<span class="bar-track"><span class="bar-fill" style="width:' + pct + '%"></span></span>' +
        '<span class="bar-count">' + esc(tally[k]) + "</span></div>";
    }).join("");
  }

  function renderList(el, rowsHtml, emptyHtml) {
    el.innerHTML = rowsHtml || emptyHtml;
  }

  async function renderOverview() {
    var stats = $("#stats");
    try {
      var o = await api("/admin/overview");
      stats.innerHTML = Object.keys(STAT_LABELS).map(function (key) {
        return '<div class="stat"><div class="stat-value">' +
          esc(o.totals[key]) + '</div><div class="stat-label">' +
          esc(STAT_LABELS[key]) + "</div></div>";
      }).join("");
      $("#trends").innerHTML = Object.keys(TREND_LABELS).map(function (key) {
        return '<span class="trend"><b>' + esc(o.trends[key]) + "</b> " +
          esc(TREND_LABELS[key]) + "</span>";
      }).join("");
      renderBars($("#class-bars"), o.class_distribution);
      renderBars($("#model-bars"), o.model_usage);
      renderList($("#recent-users"),
        (o.recent_users || []).map(function (u) {
          return '<button class="list-row" data-user="' + u.id + '">' +
            "<span><b>" + esc(u.name || u.contact) + "</b>" +
            '<small class="muted">' + esc(u.contact) + "</small></span>" +
            '<span class="muted">' + esc(fmtDate(u.created_at)) + "</span></button>";
        }).join(""),
        '<p class="muted">No farmers yet.</p>');
      renderList($("#recent-diagnoses"),
        (o.recent_diagnoses || []).map(function (d) {
          var conf = d.confidence == null ? "" :
            " · " + (d.confidence <= 1 ? Math.round(d.confidence * 100) : Math.round(d.confidence)) + "%";
          return '<div class="list-row"><span><b>' + esc(d.class || "—") + "</b>" +
            '<small class="muted">' + esc(d.user || "—") + " · " + esc(d.model || "—") + esc(conf) +
            "</small></span>" +
            '<span class="muted">' + esc(fmtDate(d.at)) + "</span></div>";
        }).join(""),
        '<p class="muted">No diagnoses yet.</p>');
      $("#stats-updated").textContent = "Updated " + new Date().toLocaleTimeString();
    } catch (err) {
      stats.innerHTML = '<p class="muted">' + esc(err.message) + "</p>";
    }
  }

  /* ---------------- users ---------------- */

  async function renderUsers() {
    var body = $("#users-body");
    try {
      var data = await api("/admin/users");
      if (!data.items.length) {
        body.innerHTML = '<tr><td colspan="5" class="muted">No accounts yet.</td></tr>';
        return;
      }
      body.innerHTML = data.items.map(function (u) {
        var isSelf = state.user && u.id === state.user.id;
        var next = u.role === "superadmin" ? "user" : "superadmin";
        var label = u.role === "superadmin" ? "Make farmer" : "Make superadmin";
        return "<tr>" +
          "<td>" + esc(u.name) + (isSelf ? ' <span class="muted">(you)</span>' : "") + "</td>" +
          "<td>" + esc(u.contact) + "</td>" +
          '<td><span class="badge badge-' + esc(u.role) + '">' + esc(u.role) + "</span></td>" +
          "<td>" + esc(fmtDate(u.created_at)) + "</td>" +
          '<td class="right"><div class="row-actions">' +
          '<button class="btn btn-outline btn-sm" data-role-id="' + u.id +
          '" data-role="' + next + '">' + label + "</button>" +
          "</div></td></tr>";
      }).join("");
    } catch (err) {
      body.innerHTML = '<tr><td colspan="5" class="muted">' + esc(err.message) + "</td></tr>";
    }
  }

  async function changeRole(userId, role) {
    try {
      await api("/admin/users/" + userId + "/role", { method: "PUT", body: { role: role } });
      toast("Role updated to " + role);
      renderUsers();
    } catch (err) {
      toast(err.message, true);
    }
  }

  /* ---------------- notices ---------------- */

  var CAT_LABELS = { update: "Update", announcement: "Announcement", crop_alert: "Crop alert" };

  function resetComposer() {
    state.editingId = null;
    $("#notice-form").reset();
    $("#n-status").value = "published";
    $("#n-category").value = "announcement";
    $("#n-author").value = (state.user && state.user.name) || "Super Admin";
    $("#composer-title").textContent = "New notice";
    $("#n-save").textContent = "Publish notice";
    $("#composer-cancel").classList.add("hidden");
    $("#notice-form-msg").textContent = "";
    $("#notice-form-msg").classList.remove("error");
  }

  async function renderNotices() {
    var list = $("#notices-list");
    try {
      var q = state.status ? "?status=" + encodeURIComponent(state.status) : "";
      var data = await api("/admin/notices" + q);
      if (!data.items.length) {
        list.innerHTML = '<p class="muted">No notices yet — write the first one above.</p>';
        return;
      }
      list.innerHTML = data.items.map(function (n) {
        return '<article class="notice" data-id="' + n.id + '">' +
          '<div class="notice-top">' +
          '<span class="cat cat-' + esc(n.category) + '">' +
          esc(CAT_LABELS[n.category] || n.category) + "</span>" +
          '<h3 class="notice-title">' + esc(n.title) + "</h3>" +
          '<span class="badge badge-' + esc(n.status) + '">' + esc(n.status) + "</span>" +
          "</div>" +
          (n.body ? '<p class="notice-body">' + esc(n.body) + "</p>" : "") +
          '<div class="notice-meta">' +
          "<span>" + esc(n.author_name || "Super Admin") + "</span>" +
          "<span>" + esc(fmtDate(n.created_at)) + "</span>" +
          "<span>" + esc(n.read_count || 0) + " read</span>" +
          '<div class="row-actions">' +
          '<button class="btn btn-outline btn-sm" data-edit="' + n.id + '">Edit</button>' +
          '<button class="btn btn-danger btn-sm" data-delete="' + n.id + '">Delete</button>' +
          "</div></div></article>";
      }).join("");
    } catch (err) {
      list.innerHTML = '<p class="muted">' + esc(err.message) + "</p>";
    }
  }

  async function startEdit(id) {
    try {
      var data = await api("/admin/notices");
      var n = data.items.find(function (x) { return x.id === id; });
      if (!n) { toast("Notice not found.", true); return; }
      state.editingId = id;
      $("#n-title").value = n.title;
      $("#n-body").value = n.body || "";
      $("#n-category").value = n.category;
      $("#n-status").value = n.status;
      $("#n-author").value = n.author_name || "";
      $("#composer-title").textContent = "Edit notice";
      $("#n-save").textContent = "Save changes";
      $("#composer-cancel").classList.remove("hidden");
      $("#view-notices").scrollTop = 0;
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (err) {
      toast(err.message, true);
    }
  }

  async function saveNotice(e) {
    e.preventDefault();
    var msg = $("#notice-form-msg");
    var payload = {
      title: $("#n-title").value.trim(),
      body: $("#n-body").value.trim(),
      category: $("#n-category").value,
      status: $("#n-status").value,
      author_name: $("#n-author").value.trim() || "Super Admin",
    };
    $("#n-save").disabled = true;
    try {
      if (state.editingId) {
        await api("/admin/notices/" + state.editingId, { method: "PUT", body: payload });
        toast("Notice updated");
      } else {
        await api("/admin/notices", { method: "POST", body: payload });
        toast(payload.status === "draft" ? "Draft saved" : "Notice published");
      }
      resetComposer();
      renderNotices();
      renderOverview();
    } catch (err) {
      msg.textContent = err.message;
      msg.classList.add("error");
    } finally {
      $("#n-save").disabled = false;
    }
  }

  async function deleteNotice(id) {
    if (!window.confirm("Delete this notice? Farmers will no longer see it.")) return;
    try {
      await api("/admin/notices/" + id, { method: "DELETE" });
      toast("Notice deleted");
      if (state.editingId === id) resetComposer();
      renderNotices();
      renderOverview();
    } catch (err) {
      toast(err.message, true);
    }
  }

  /* ---------------- wiring ---------------- */

  function boot() {
    $("#login-form").addEventListener("submit", handleLogin);
    $("#signout-btn").addEventListener("click", function () { signOut(true); });

    $$("[data-nav]").forEach(function (el) {
      el.addEventListener("click", function () { navigate(el.dataset.nav); });
    });

    $("#refresh-stats").addEventListener("click", renderOverview);
    $("#recent-users").addEventListener("click", function (e) {
      var btn = e.target.closest("[data-user]");
      if (btn && typeof openUserDetail === "function") openUserDetail(Number(btn.dataset.user));
    });
    $("#users-refresh").addEventListener("click", renderUsers);
    $("#notices-refresh").addEventListener("click", renderNotices);
    $("#notice-form").addEventListener("submit", saveNotice);
    $("#composer-cancel").addEventListener("click", resetComposer);

    $$(".chip[data-status]").forEach(function (chip) {
      chip.addEventListener("click", function () {
        state.status = chip.dataset.status;
        $$(".chip[data-status]").forEach(function (c) {
          c.classList.toggle("active", c === chip);
        });
        renderNotices();
      });
    });

    $("#users-body").addEventListener("click", function (e) {
      var btn = e.target.closest("[data-role-id]");
      if (btn) changeRole(Number(btn.dataset.roleId), btn.dataset.role);
    });

    $("#notices-list").addEventListener("click", function (e) {
      var edit = e.target.closest("[data-edit]");
      if (edit) { startEdit(Number(edit.dataset.edit)); return; }
      var del = e.target.closest("[data-delete]");
      if (del) deleteNotice(Number(del.dataset.delete));
    });

    if (state.token && state.user) showApp();
    else showLogin();
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
