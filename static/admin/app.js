/* PotatoDoc superadmin console.
 * Plain ES2019, no build step. Talks to the same FastAPI origin it is served
 * from (/admin -> ../auth, ../admin). Token lives in localStorage.
 */
(function () {
  "use strict";

  var TOKEN_KEY = "potatodocAdminToken";
  var USER_KEY = "potatodocAdminUser";
  var MAX_NOTICE_IMAGES = 6;

  var state = {
    token: localStorage.getItem(TOKEN_KEY) || "",
    user: JSON.parse(localStorage.getItem(USER_KEY) || "null"),
    view: "dashboard",
    status: "", // notice filter
    editingId: null, // notice being edited
    search: "", // users filter (client side)
    userDetailId: null,
    ticketStatus: "", // ticket filter: "" | open | resolved
    ticketId: null, // thread open in the Tickets view
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

  function fmtDay(iso) {
    if (!iso) return "";
    var d = new Date(iso.replace(" ", "T") + (iso.indexOf("Z") === -1 && iso.indexOf("+") === -1 ? "Z" : ""));
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleDateString(undefined, {
      year: "numeric", month: "short", day: "numeric",
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

  /**
   * Authed image bytes -> object URL. A bare <img src> cannot send the
   * Authorization header, and draft images are admin-only, so the panel
   * fetches with the token and swaps in a blob URL. Panel lists are small,
   * so sequential hydration is fine.
   */
  async function imageUrl(path) {
    var res = await fetch(path, {
      headers: state.token ? { Authorization: "Bearer " + state.token } : {},
    });
    if (res.status === 401) {
      signOut(false);
      throw new Error("Session expired — please sign in again.");
    }
    if (!res.ok) throw new Error("Image failed to load (" + res.status + ")");
    return URL.createObjectURL(await res.blob());
  }

  function hydrateThumbs(root) {
    var imgs = root.querySelectorAll("img[data-thumb]");
    Array.prototype.forEach.call(imgs, function (el) {
      imageUrl(el.dataset.thumb)
        .then(function (url) { el.src = url; })
        .catch(function () { el.classList.add("thumb-missing"); });
    });
  }

  /** Multipart upload — api() only speaks JSON, so this talks to fetch directly. */
  async function uploadNoticeImage(noticeId, file) {
    var fd = new FormData();
    fd.append("file", file, file.name || "image.jpg");
    var res = await fetch("/admin/notices/" + noticeId + "/images", {
      method: "POST",
      body: fd,
      headers: state.token ? { Authorization: "Bearer " + state.token } : {},
    });
    if (res.status === 401) {
      signOut(false);
      throw new Error("Session expired — please sign in again.");
    }
    var data = null;
    try { data = await res.json(); } catch (e) { data = null; }
    if (!res.ok) throw new Error((data && data.detail) || "Upload failed (" + res.status + ")");
    return data;
  }

  /** Thumbnails while editing: each has a remove button keyed by index. */
  function renderThumbEditor(count) {
    var el = $("#n-thumbs");
    if (!count || !state.editingId) {
      el.innerHTML = "";
      el.classList.add("hidden");
      return;
    }
    el.classList.remove("hidden");
    el.innerHTML = Array.from({ length: count }, function (_, i) {
      return (
        '<span class="thumb-slot">' +
        '<img class="notice-thumb" alt="" data-thumb="/admin/notices/' +
        state.editingId + "/images/" + i + '">' +
        '<button type="button" class="thumb-rm" data-rmimg="' + i + '" title="Remove image">&times;</button>' +
        "</span>"
      );
    }).join("");
    hydrateThumbs(el);
  }

  async function removeNoticeImage(index) {
    if (!state.editingId) return;
    if (!window.confirm("Remove this image from the notice?")) return;
    try {
      await api("/admin/notices/" + state.editingId + "/images/" + index, { method: "DELETE" });
      var data = await api("/admin/notices");
      var n = data.items.find(function (x) { return x.id === state.editingId; });
      renderThumbEditor(n ? n.image_count : 0);
      renderNotices();
      toast("Image removed");
    } catch (err) {
      toast(err.message, true);
    }
  }

  /* ---------------- auth ---------------- */

  function showLogin() {
    $("#app-view").classList.add("hidden");
    $("#login-view").classList.remove("hidden");
    $("#login-error").classList.add("hidden");
    $("#login-password").value = "";
    document.title = "PotatoDoc · Superadmin";
  }

  function showApp() {
    $("#login-view").classList.add("hidden");
    $("#app-view").classList.remove("hidden");
    var u = state.user || {};
    $("#sidebar-user").innerHTML =
      esc(u.name || "Super Admin") + "<small>" + esc(u.contact || "") + "</small>";
    document.title = TITLES[state.view] || "Dashboard";
    renderOverview();
    renderUsers();
    renderNotices();
    renderTickets();
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

  var TITLES = {
    dashboard: "Dashboard",
    users: "Users",
    notices: "Notices",
    tickets: "Tickets",
    models: "Models",
    "user-detail": "Farmer Detail",
  };

  function navigate(view) {
    state.view = view;
    $$(".nav-item").forEach(function (el) {
      el.classList.toggle("active", el.dataset.nav === view);
    });
    $$(".view").forEach(function (el) { el.classList.add("hidden"); });
    var target = $("#view-" + view);
    if (target) target.classList.remove("hidden");
    $("#page-title").textContent = TITLES[view] || "Dashboard";
    document.title = TITLES[view] || "Dashboard";
    if (view === "dashboard") renderOverview();
    if (view === "users") renderUsers();
    if (view === "notices") renderNotices();
    if (view === "tickets") renderTickets();
    if (view === "models") renderModels();
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
      var q = state.search.trim().toLowerCase();
      var items = data.items;
      if (q) {
        items = items.filter(function (u) {
          return String(u.name || "").toLowerCase().indexOf(q) !== -1 ||
            String(u.contact || "").toLowerCase().indexOf(q) !== -1;
        });
      }
      if (!items.length) {
        body.innerHTML = '<tr><td colspan="7" class="muted">' +
          (q ? "No farmers match “" + esc(state.search) + "”." : "No accounts yet.") +
          "</td></tr>";
        return;
      }
      body.innerHTML = items.map(function (u) {
        var isSelf = state.user && u.id === state.user.id;
        var next = u.role === "superadmin" ? "user" : "superadmin";
        var label = u.role === "superadmin" ? "Make farmer" : "Make superadmin";
        var banned = u.status === "banned";
        var statusBadge = '<span class="badge badge-' + (banned ? "banned" : "active") + '">' +
          (banned ? "banned" : "active") + "</span>";
        var banBtn = (u.role !== "superadmin" && !isSelf)
          ? '<button class="btn btn-outline btn-sm" data-ban-id="' + u.id +
            '" data-ban-next="' + (banned ? "active" : "banned") + '">' +
            (banned ? "Unban" : "Ban") + "</button>"
          : "";
        return "<tr>" +
          "<td>" + avatarHtml(u.photo, u.name || u.contact) +
          '<button class="linklike" data-user="' + u.id + '">' + esc(u.name) + "</button>" +
          (isSelf ? ' <span class="muted">(you)</span>' : "") + "</td>" +
          "<td>" + esc(u.contact) + "</td>" +
          '<td><span class="badge badge-' + esc(u.role) + '">' + esc(u.role) + "</span> " +
          statusBadge + "</td>" +
          "<td>" + esc(u.history_count) + "</td>" +
          "<td>" + esc(fmtDate(u.last_diagnosis_at) || "—") + "</td>" +
          "<td>" + esc(fmtDate(u.created_at)) + "</td>" +
          '<td class="right"><div class="row-actions">' +
          '<button class="btn btn-outline btn-sm" data-role-id="' + u.id +
          '" data-role="' + next + '">' + label + "</button>" +
          banBtn +
          "</div></td></tr>";
      }).join("");
    } catch (err) {
      body.innerHTML = '<tr><td colspan="7" class="muted">' + esc(err.message) + "</td></tr>";
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

  /** Ban / unban a farmer (PUT /admin/users/{id}/status). A ban revokes every
   * live session server-side and blocks the token from then on. */
  async function changeStatus(userId, status) {
    try {
      await api("/admin/users/" + userId + "/status", {
        method: "PUT",
        body: { status: status },
      });
      toast(status === "banned"
        ? "Farmer banned — signed out everywhere"
        : "Farmer unbanned — they can sign in again");
      renderUsers();
      if (state.userDetailId === userId) openUserDetail(userId);
    } catch (err) {
      toast(err.message, true);
    }
  }

  /* ---------------- user detail (drill-down) ---------------- */

  async function openUserDetail(userId) {
    state.userDetailId = userId;
    navigate("user-detail");
    $("#ud-history-body").innerHTML = '<tr><td colspan="5" class="muted">Loading…</td></tr>';
    try {
      var u = await api("/admin/users/" + userId);
      $("#ud-name").innerHTML =
        avatarHtml(u.photo, u.name || u.contact, true) + esc(u.name || u.contact);
      $("#ud-meta").textContent = u.contact || "";
      $("#ud-role").textContent =
        u.role === "superadmin" ? "Superadmin" : "User";
      $("#ud-joined").textContent = "Joined " + (fmtDay(u.created_at) || "—");
      $("#ud-cards").innerHTML = [
        ["Diagnoses", u.history_count],
        ["sessions", u.session_count],
        ["notice read", u.notices_read],
        ["Last session", fmtDate(u.last_session_at) || "—", true],
        ["Last Diagnosis", fmtDate(u.last_diagnosis_at) || "—", true],
      ].map(function (c) {
        return '<div class="stat"><div class="stat-value' + (c[2] ? " small" : "") + '">' + esc(c[1]) +
          '</div><div class="stat-label">' + esc(c[0]) + "</div></div>";
      }).join("");
      renderBars($("#ud-class-bars"), u.class_distribution);
      renderBars($("#ud-model-bars"), u.model_usage);
    } catch (err) {
      toast(err.message, true);
      navigate("users");
      return;
    }
    try {
      var h = await api("/admin/users/" + userId + "/history");
      var body = $("#ud-history-body");
      if (!h.items.length) {
        body.innerHTML = '<tr><td colspan="5" class="muted">No diagnoses yet.</td></tr>';
        return;
      }
      body.innerHTML = h.items.map(function (it) {
        var conf = "—";
        if (it.confidence != null) {
          var pct = it.confidence <= 1 ? it.confidence * 100 : it.confidence;
          conf = Math.round(pct) + "%";
        }
        return '<tr class="row-link" data-hid="' + esc(it.id) + '">' +
          "<td>" + esc(it.class || "—") + "</td><td>" + esc(conf) +
          "</td><td>" + esc(it.model || "—") + "</td><td>" +
          (it.is_unknown ? "yes" : "no") + "</td><td>" +
          esc(fmtDate(it.timestamp || it.created_at)) + "</td></tr>";
      }).join("");
    } catch (err) {
      $("#ud-history-body").innerHTML =
        '<tr><td colspan="5" class="muted">' + esc(err.message) + "</td></tr>";
    }
  }

  /* ---------------- notices ---------------- */

  var CAT_LABELS = {
    update: "Update",
    announcement: "Announcement",
    crop_alert: "Crop alert",
    new_product: "New product",
    medicine: "Medicine",
  };

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
    $("#n-images").value = "";
    $("#n-thumbs").innerHTML = "";
    $("#n-thumbs").classList.add("hidden");
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
          (n.image_count
            ? '<div class="thumb-row">' +
              Array.from({ length: n.image_count }, function (_, i) {
                return (
                  '<span class="thumb-slot">' +
                  '<img class="notice-thumb" alt="" data-thumb="/admin/notices/' +
                  n.id + "/images/" + i + '">' +
                  "</span>"
                );
              }).join("") +
              "</div>"
            : "") +
          '<div class="notice-meta">' +
          "<span>" + esc(n.author_name || "Super Admin") + "</span>" +
          "<span>" + esc(fmtDate(n.created_at)) + "</span>" +
          "<span>" + esc(n.read_count || 0) + " read</span>" +
          '<div class="row-actions">' +
          '<button class="btn btn-outline btn-sm" data-edit="' + n.id + '">Edit</button>' +
          '<button class="btn btn-danger btn-sm" data-delete="' + n.id + '">Delete</button>' +
          "</div></div></article>";
      }).join("");
      hydrateThumbs(list);
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
      renderThumbEditor(n.image_count || 0);
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
      var saved;
      if (state.editingId) {
        saved = await api("/admin/notices/" + state.editingId, { method: "PUT", body: payload });
        toast("Notice updated");
      } else {
        saved = await api("/admin/notices", { method: "POST", body: payload });
        toast(payload.status === "draft" ? "Draft saved" : "Notice published");
      }
      // Snapshot the files before resetComposer() clears the input.
      var pending = Array.prototype.slice.call($("#n-images").files || []);
      var existing = state.editingId ? saved.image_count || 0 : 0;
      var room = MAX_NOTICE_IMAGES - existing;
      if (pending.length > room) {
        pending = pending.slice(0, room > 0 ? room : 0);
        toast("A notice can have at most 6 images.");
      }
      var uploadErr = null;
      for (var i = 0; i < pending.length && !uploadErr; i++) {
        try {
          await uploadNoticeImage(saved.id, pending[i]);
        } catch (imgErr) {
          uploadErr = imgErr;
        }
      }
      resetComposer();
      renderNotices();
      renderOverview();
      if (uploadErr) {
        toast("Notice saved, but an image failed: " + uploadErr.message, true);
      } else if (pending.length) {
        toast(
          pending.length === 1
            ? "Notice saved with 1 image"
            : "Notice saved with " + pending.length + " images"
        );
      }
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

  /* ---------------- support tickets ---------------- */

  var TK_STATUS_LABELS = { open: "Open", resolved: "Resolved" };

  /** "Salina Kunwar" -> "SK" (fallback avatar when the farmer has no photo). */
  function initialsOf(label) {
    var src = String(label || "?").trim();
    var parts = src.split(/\s+/);
    var out = parts.length > 1 && parts[1]
      ? parts[0][0] + parts[1][0]
      : src.slice(0, 2);
    return out.toUpperCase();
  }

  /** Farmer avatar: base64 photo from /admin/users, else an initials disc. */
  function avatarHtml(photo, label, big) {
    var size = big ? " avatar-lg" : "";
    if (photo) {
      return '<img class="avatar' + size + '" src="' + photo + '" alt="">';
    }
    return '<span class="avatar-empty' + size + '">' +
      esc(initialsOf(label)) + "</span>";
  }

  async function renderTickets() {
    var list = $("#tickets-list");
    try {
      var data = await api("/admin/tickets");
      var all = data.items || [];
      // Sidebar badge = open tickets (always computed from the full list).
      var openCount = all.filter(function (t) { return t.status === "open"; }).length;
      var badge = $("#nav-tickets-badge");
      badge.textContent = String(openCount);
      badge.classList.toggle("hidden", !openCount);

      var items = state.ticketStatus
        ? all.filter(function (t) { return t.status === state.ticketStatus; })
        : all;
      if (!items.length) {
        list.innerHTML = '<p class="muted">' +
          (state.ticketStatus
            ? "No " + esc(state.ticketStatus) + " tickets."
            : "No tickets yet — farmers open them from Profile → Help & Feedback.") +
          "</p>";
        return;
      }
      list.innerHTML = items.map(function (tk) {
        var f = tk.farmer || {};
        var preview = tk.last_body ? tk.last_body.slice(0, 110) : "";
        return '<button class="list-row" data-ticket="' + tk.id + '">' +
          "<span>" + avatarHtml(f.photo, f.name || f.contact) +
          "<b>" + esc(tk.subject) + "</b>" +
          '<small class="muted">' + esc(f.name || "—") + " · " + esc(f.contact || "") +
          (preview ? " · " + esc(preview) + (tk.last_body.length > 110 ? "…" : "") : "") +
          "</small></span>" +
          '<span class="muted"><span class="badge badge-' + esc(tk.status) + '">' +
          esc(TK_STATUS_LABELS[tk.status] || tk.status) + "</span><br>" +
          esc(fmtDate(tk.updated_at)) + " · " + esc(tk.message_count) + " msg</span>" +
          "</button>";
      }).join("");
    } catch (err) {
      list.innerHTML = '<p class="muted">' + esc(err.message) + "</p>";
    }
  }

  function renderTicketThread(tk) {
    var f = tk.farmer || {};
    $("#tk-subject").textContent = tk.subject;
    $("#tk-meta").innerHTML =
      avatarHtml(f.photo, f.name || f.contact, true) +
      "<b>" + esc(f.name || "Farmer") + "</b> · " + esc(f.contact || "") +
      ' <span class="badge badge-' + esc(tk.status) + '">' +
      esc(TK_STATUS_LABELS[tk.status] || tk.status) + "</span>" +
      " · opened " + esc(fmtDate(tk.created_at));
    $("#tk-toggle").textContent =
      tk.status === "resolved" ? "Reopen ticket" : "Mark resolved";

    var box = $("#tk-messages");
    if (!tk.messages || !tk.messages.length) {
      box.innerHTML = '<p class="muted">No messages.</p>';
      return;
    }
    box.innerHTML = tk.messages.map(function (m) {
      var mine = m.from === "admin"; // "admin" = this panel speaking
      return '<div class="chat-row' + (mine ? " mine" : "") + '">' +
        '<div class="chat-bubble">' +
        (mine ? "" : '<div class="chat-author">' + esc(m.author) + "</div>") +
        esc(m.body) +
        '<div class="chat-time">' + esc(fmtDate(m.created_at)) + "</div>" +
        "</div></div>";
    }).join("");
    box.scrollTop = box.scrollHeight;
  }

  async function openTicket(id) {
    state.ticketId = id;
    $("#ticket-thread").classList.remove("hidden");
    $("#tk-messages").innerHTML = '<p class="muted">Loading…</p>';
    $("#tk-msg").textContent = "";
    try {
      var tk = await api("/admin/tickets/" + id);
      renderTicketThread(tk);
      $("#ticket-thread").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (err) {
      $("#tk-messages").innerHTML = '<p class="muted">' + esc(err.message) + "</p>";
    }
  }

  async function sendTicketReply(e) {
    e.preventDefault();
    var input = $("#tk-reply");
    var msg = $("#tk-msg");
    var body = input.value.trim();
    msg.classList.remove("error");
    if (!body) {
      msg.textContent = "Write a reply first.";
      msg.classList.add("error");
      return;
    }
    var btn = $("#tk-send");
    btn.disabled = true;
    try {
      var tk = await api("/admin/tickets/" + state.ticketId + "/messages", {
        method: "POST",
        body: { body: body },
      });
      input.value = "";
      renderTicketThread(tk);
      msg.textContent = "Reply sent — the farmer sees it in the app.";
      renderTickets();
    } catch (err) {
      msg.textContent = err.message;
      msg.classList.add("error");
    } finally {
      btn.disabled = false;
    }
  }

  async function toggleTicketStatus() {
    if (state.ticketId == null) return;
    var resolving = $("#tk-toggle").textContent === "Mark resolved";
    try {
      var tk = await api("/admin/tickets/" + state.ticketId, {
        method: "PUT",
        body: { status: resolving ? "resolved" : "open" },
      });
      renderTicketThread(tk);
      renderTickets();
      toast(resolving ? "Ticket resolved" : "Ticket reopened");
    } catch (err) {
      toast(err.message, true);
    }
  }

  /* ---------------- diagnosis report modal ---------------- */

  var _resultPhotoUrl = null; // object URL of the current photo (revoked on close)

  function closeResultModal() {
    $("#result-modal").classList.add("hidden");
    if (_resultPhotoUrl) { URL.revokeObjectURL(_resultPhotoUrl); _resultPhotoUrl = null; }
  }

  /** The farmer's own "PredictionResult" view, mirrored for the panel: the
   * scanned photo, verdict + confidence, device time, GPS tag and the full
   * per-class probability breakdown for one diagnosis. */
  async function openResultModal(userId, itemId) {
    var modal = $("#result-modal");
    modal.classList.remove("hidden");
    $("#rm-title").textContent = "Diagnosis report";
    $("#rm-photo").innerHTML = '<span class="muted">Loading…</span>';
    $("#rm-verdict").innerHTML = "";
    $("#rm-meta").textContent = "";
    $("#rm-loc").innerHTML = "";
    $("#rm-probs").innerHTML = "";

    try {
      var h = await api("/admin/users/" + userId + "/history?limit=50");
      var it = null;
      (h.items || []).forEach(function (x) {
        if (String(x.id) === String(itemId)) it = x;
      });
      if (!it) throw new Error("This diagnosis no longer exists.");

      $("#rm-title").textContent = "Diagnosis · " + (it.class || "unknown");

      // Photo — fetched with the bearer token (a bare <img> cannot send it).
      if (it.has_photo) {
        try {
          _resultPhotoUrl = await imageUrl("/admin/users/" + userId + "/history/" +
            encodeURIComponent(itemId) + "/photo");
          $("#rm-photo").innerHTML =
            '<img src="' + _resultPhotoUrl + '" alt="Scanned leaf">';
        } catch (e) {
          $("#rm-photo").innerHTML = '<span class="muted">Photo unavailable</span>';
        }
      } else {
        $("#rm-photo").innerHTML =
          '<span class="muted">No photo stored for this scan</span>';
      }

      // Verdict + confidence + model.
      var conf = "";
      if (it.confidence != null) {
        var pct = it.confidence <= 1 ? it.confidence * 100 : it.confidence;
        conf = Math.round(pct) + "%";
      }
      $("#rm-verdict").innerHTML =
        '<div class="rm-class">' + esc(it.class || "Unknown") + "</div>" +
        '<span class="rm-conf">' + esc(conf || "—") + "</span>" +
        (it.is_unknown ? ' <span class="badge badge-draft">unrecognised</span>' : "") +
        (it.model ? ' <span class="badge badge-superadmin">' + esc(it.model) + "</span>" : "");

      // When: the device timestamp (when the photo was taken) + when saved.
      $("#rm-meta").textContent =
        "Taken " + (fmtDate(it.timestamp) || "—") +
        (it.created_at ? " · saved " + fmtDate(it.created_at) : "");

      // Where: GPS tag captured at diagnosis time, if any. The app writes
      // {lat, lon, accuracy, label}; a few legacy rows used
      // {latitude, longitude, accuracy_m, locality} — accept both.
      var loc = it.location || {};
      var lat = loc.lat != null ? loc.lat : loc.latitude;
      var lon = loc.lon != null ? loc.lon : loc.longitude;
      var acc = loc.accuracy != null ? loc.accuracy : loc.accuracy_m;
      var place = loc.label || loc.locality || "";
      $("#rm-loc").innerHTML = lat != null && lon != null
        ? '<span class="badge badge-open">📍 ' + Number(lat).toFixed(5) +
          ", " + Number(lon).toFixed(5) + "</span>" +
          (acc != null ? ' <span class="muted">±' + Math.round(acc) + " m</span>" : "") +
          (place ? ' <span class="muted">' + esc(place) + "</span>" : "")
        : '<span class="muted">No location tag</span>';

      // Full probability breakdown, sorted high → low.
      var probs = it.probabilities;
      if (probs && typeof probs === "object") {
        var entries = Object.keys(probs).map(function (k) { return [k, probs[k]]; });
        entries.sort(function (a, b) { return b[1] - a[1]; });
        $("#rm-probs").innerHTML = entries.map(function (e) {
          var v = e[1];
          var p = v <= 1 ? v * 100 : v;
          return '<div class="bar-row">' +
            '<span class="bar-label">' + esc(e[0]) + "</span>" +
            '<span class="bar-track"><span class="bar-fill" style="width:' +
            Math.max(2, Math.round(p)) + '%"></span></span>' +
            '<span class="bar-count">' + p.toFixed(1) + "%</span></div>";
        }).join("");
      } else {
        $("#rm-probs").innerHTML = '<p class="muted">No probability data stored.</p>';
      }
    } catch (err) {
      $("#rm-verdict").innerHTML = '<p class="muted">' + esc(err.message) + "</p>";
    }
  }

  /* ---------------- models ---------------- */

  async function renderModels() {
    var body = $("#models-body");
    try {
      var m = await api("/admin/models");
      var ready = m.items.filter(function (i) { return i.available; }).length;
      $("#models-callout").innerHTML =
        "<b>Default:</b> " + esc(m.default) +
        ' <span class="muted">· ' + ready + "/" + m.items.length +
        " weight sets present · " + esc(m.weights_dir) + "</span>";
      if (!m.items.length) {
        body.innerHTML = '<tr><td colspan="6" class="muted">No models configured.</td></tr>';
      } else {
        body.innerHTML = m.items.map(function (it) {
          return "<tr><td>" + esc(it.name) + "</td>" +
            "<td>" + (it.available
              ? '<span class="ok">✓ ready</span>'
              : '<span class="miss">✗ missing</span>') + "</td>" +
            "<td>" + esc(it.size_mb) + " MB</td>" +
            "<td>" + esc(fmtDate(it.modified_at) || "—") + "</td>" +
            "<td>" + (it.accuracy == null ? "—" : esc(it.accuracy)) + "</td>" +
            "<td>" + (it.f1_macro == null ? "—" : esc(it.f1_macro)) + "</td></tr>";
        }).join("");
      }
      var t = m.train_config || {};
      $("#models-train").textContent =
        "Trained " + (fmtDate(t.trained_at) || "—") +
        " · epochs " + t.epochs + " · batch " + t.batch +
        " · img " + t.img + " · seed " + t.seed;
      $("#models-classes").innerHTML = (m.classes || []).length
        ? m.classes.map(function (c) {
            return '<span class="cat cat-update">' + esc(c) + "</span>";
          }).join("")
        : '<p class="muted">No labels loaded.</p>';
      $("#models-extra").textContent = JSON.stringify(
        { ensemble: m.ensemble || {}, thresholds: m.thresholds || {} }, null, 2);
    } catch (err) {
      body.innerHTML = '<tr><td colspan="6" class="muted">' + esc(err.message) + "</td></tr>";
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
    $("#models-refresh").addEventListener("click", renderModels);
    $("#recent-users").addEventListener("click", function (e) {
      var btn = e.target.closest("[data-user]");
      if (btn && typeof openUserDetail === "function") openUserDetail(Number(btn.dataset.user));
    });
    $("#users-refresh").addEventListener("click", renderUsers);
    $("#notices-refresh").addEventListener("click", renderNotices);
    $("#notice-form").addEventListener("submit", saveNotice);
    $("#notice-form").addEventListener("click", function (e) {
      var rmImg = e.target.closest("[data-rmimg]");
      if (rmImg) { removeNoticeImage(Number(rmImg.dataset.rmimg)); }
    });
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

    $("#users-search").addEventListener("input", function (e) {
      state.search = e.target.value;
      renderUsers();
    });

    $("#users-body").addEventListener("click", function (e) {
      var farmer = e.target.closest("[data-user]");
      if (farmer) { openUserDetail(Number(farmer.dataset.user)); return; }
      var ban = e.target.closest("[data-ban-id]");
      if (ban) { changeStatus(Number(ban.dataset.banId), ban.dataset.banNext); return; }
      var btn = e.target.closest("[data-role-id]");
      if (btn) changeRole(Number(btn.dataset.roleId), btn.dataset.role);
    });

    $("#ud-history-body").addEventListener("click", function (e) {
      var row = e.target.closest("[data-hid]");
      if (row && state.userDetailId != null) {
        openResultModal(state.userDetailId, row.dataset.hid);
      }
    });

    $("#rm-close").addEventListener("click", closeResultModal);
    $("#result-modal").addEventListener("click", function (e) {
      if (e.target === $("#result-modal")) closeResultModal(); // backdrop
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !$("#result-modal").classList.contains("hidden")) {
        closeResultModal();
      }
    });

    $("#notices-list").addEventListener("click", function (e) {
      var edit = e.target.closest("[data-edit]");
      if (edit) { startEdit(Number(edit.dataset.edit)); return; }
      var del = e.target.closest("[data-delete]");
      if (del) { deleteNotice(Number(del.dataset.delete)); }
    });

    $("#tickets-refresh").addEventListener("click", renderTickets);
    $("#tk-reply-form").addEventListener("submit", sendTicketReply);
    $("#tk-toggle").addEventListener("click", toggleTicketStatus);
    $("#tk-back").addEventListener("click", function () {
      state.ticketId = null;
      $("#ticket-thread").classList.add("hidden");
      renderTickets();
    });
    $("#tickets-list").addEventListener("click", function (e) {
      var btn = e.target.closest("[data-ticket]");
      if (btn) openTicket(Number(btn.dataset.ticket));
    });
    $$(".chip[data-tstatus]").forEach(function (chip) {
      chip.addEventListener("click", function () {
        state.ticketStatus = chip.dataset.tstatus;
        $$(".chip[data-tstatus]").forEach(function (c) {
          c.classList.toggle("active", c === chip);
        });
        renderTickets();
      });
    });

    if (state.token && state.user) showApp();
    else showLogin();
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
