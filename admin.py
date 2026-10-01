"""Superadmin API: platform stats, farmer accounts and notice CRUD.

Every route requires the superadmin role (see auth.require_superadmin).
Served to the static web panel at /admin; no torch imports.

Beyond the CRUD the panel also needs read-only reporting:
  GET /admin/overview              dashboard totals, 7-day trends, class/model mix
  GET /admin/users/{id}            one farmer's profile + activity aggregates
  GET /admin/users/{id}/history    that farmer's diagnoses (payload parsed)
  GET /admin/models                model inventory + weights + metrics
"""
import json
import os
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from auth import require_superadmin
from db import NOTICE_CATEGORIES, NOTICE_STATUSES, ROLES, connect

router = APIRouter(prefix="/admin", tags=["admin"])


class RoleIn(BaseModel):
    role: str = ""


class NoticeIn(BaseModel):
    category: str = "announcement"
    title: str = ""
    body: str = ""
    status: str = "published"
    author_name: str = "Super Admin"


def _notice_row(row):
    return {
        "id": row["id"],
        "category": row["category"],
        "title": row["title"],
        "body": row["body"],
        "author_name": row["author_name"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "read_count": row["read_count"],
    }


def _validate_notice(body: NoticeIn):
    title = (body.title or "").strip()
    if not title:
        raise HTTPException(422, "Title is required.")
    if len(title) > 200:
        raise HTTPException(422, "Title must be 200 characters or fewer.")
    if body.category not in NOTICE_CATEGORIES:
        raise HTTPException(422, "Unknown category.")
    if body.status not in NOTICE_STATUSES:
        raise HTTPException(422, "Unknown status.")
    author = (body.author_name or "").strip() or "Super Admin"
    return title, (body.body or "").strip(), author


@router.get("/stats")
def stats(admin: dict = Depends(require_superadmin)):
    with connect() as conn:
        users = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        sessions = conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"]
        history_items = conn.execute("SELECT COUNT(*) AS n FROM history").fetchone()["n"]
        published = conn.execute(
            "SELECT COUNT(*) AS n FROM notices WHERE status = 'published'"
        ).fetchone()["n"]
        drafts = conn.execute(
            "SELECT COUNT(*) AS n FROM notices WHERE status = 'draft'"
        ).fetchone()["n"]
    return {
        "users": users,
        "sessions": sessions,
        "history_items": history_items,
        "notices_published": published,
        "notices_draft": drafts,
    }


@router.get("/users")
def list_users(admin: dict = Depends(require_superadmin)):
    with connect() as conn:
        rows = conn.execute(
            "SELECT u.id, u.contact, u.display_name, u.role, u.created_at, "
            "  (SELECT COUNT(*) FROM history h WHERE h.user_id = u.id) AS history_count, "
            "  (SELECT MAX(h.updated_at) FROM history h WHERE h.user_id = u.id) AS last_diagnosis_at, "
            "  (SELECT MAX(s.created_at) FROM sessions s WHERE s.user_id = u.id) AS last_session_at "
            "FROM users u ORDER BY u.id DESC"
        ).fetchall()
    return {
        "items": [
            {
                "id": row["id"],
                "contact": row["contact"],
                "name": row["display_name"],
                "role": row["role"],
                "created_at": row["created_at"],
                "history_count": row["history_count"],
                "last_diagnosis_at": row["last_diagnosis_at"],
                "last_session_at": row["last_session_at"],
            }
            for row in rows
        ]
    }


@router.put("/users/{user_id}/role")
def change_role(user_id: int, body: RoleIn, admin: dict = Depends(require_superadmin)):
    if body.role not in ROLES:
        raise HTTPException(422, "Unknown role.")
    if user_id == admin["id"] and body.role != "superadmin":
        raise HTTPException(400, "You cannot demote your own account.")
    with connect() as conn:
        row = conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "User not found.")
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (body.role, user_id))
    return {"ok": True, "id": user_id, "role": body.role}


@router.get("/notices")
def list_all_notices(status: str = "", admin: dict = Depends(require_superadmin)):
    """All notices including drafts, with per-notice read counts."""
    sql = (
        "SELECT n.*, (SELECT COUNT(*) FROM notice_reads r "
        "             WHERE r.notice_id = n.id) AS read_count "
        "FROM notices n"
    )
    args: list = []
    if status:
        if status not in NOTICE_STATUSES:
            raise HTTPException(422, "Unknown status.")
        sql += " WHERE n.status = ?"
        args.append(status)
    sql += " ORDER BY n.id DESC"
    with connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    return {"items": [_notice_row(row) for row in rows]}


@router.post("/notices", status_code=201)
def create_notice(body: NoticeIn, admin: dict = Depends(require_superadmin)):
    title, text, author = _validate_notice(body)
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO notices (category, title, body, author_name, status, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (body.category, title, text, author, body.status, admin["id"]),
        )
        notice_id = cur.lastrowid
        row = conn.execute(
            "SELECT n.*, 0 AS read_count FROM notices n WHERE n.id = ?",
            (notice_id,),
        ).fetchone()
    return _notice_row(row)


@router.put("/notices/{notice_id}")
def update_notice(notice_id: int, body: NoticeIn, admin: dict = Depends(require_superadmin)):
    title, text, author = _validate_notice(body)
    with connect() as conn:
        row = conn.execute("SELECT id FROM notices WHERE id = ?", (notice_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Notice not found.")
        conn.execute(
            "UPDATE notices SET category = ?, title = ?, body = ?, author_name = ?, "
            "status = ?, updated_at = datetime('now') WHERE id = ?",
            (body.category, title, text, author, body.status, notice_id),
        )
        row = conn.execute(
            "SELECT n.*, (SELECT COUNT(*) FROM notice_reads r "
            "             WHERE r.notice_id = n.id) AS read_count "
            "FROM notices n WHERE n.id = ?",
            (notice_id,),
        ).fetchone()
    return _notice_row(row)


@router.delete("/notices/{notice_id}", status_code=204)
def delete_notice(notice_id: int, admin: dict = Depends(require_superadmin)):
    with connect() as conn:
        cur = conn.execute("DELETE FROM notices WHERE id = ?", (notice_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Notice not found.")
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Reporting: dashboard, farmer profiles, per-farmer history, model inventory
# ---------------------------------------------------------------------------

# Mirror of app.py's model table — admin.py deliberately does NOT import app.py,
# because that would pull torch into a pure-JSON endpoint.
MODEL_NAMES = {
    "ensemble": "Ensemble (All Models)",
    "small_cnn": "Small CNN (from scratch)",
    "mobilenetv2": "MobileNetV2 (transfer)",
    "efficientnetb0": "EfficientNet-B0 (transfer)",
    "convnext_plantvillage": "EfficientNet-B0 (transfer)",
}
MODEL_ORDER = ["ensemble", "small_cnn", "mobilenetv2", "efficientnetb0"]


def _base_dir():
    return Path(os.environ.get("POTATO_BASE_DIR", Path(__file__).resolve().parent))


def _weights_dir():
    return Path(os.environ.get("POTATO_WEIGHTS_DIR", str(_base_dir() / "outputs_combined")))


def _read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return None


def _payload_of(payload_json, key, default=None):
    """Read one field out of the stored history payload JSON (never raises)."""
    try:
        value = json.loads(payload_json)
    except Exception:
        return default
    if not isinstance(value, dict):
        return default
    return value.get(key, default)


def _tally(rows, key):
    """Count a payload field across history rows: {'Healthy': 4, ...}."""
    counts = {}
    for row in rows:
        value = _payload_of(row["payload"], key)
        if value in (None, ""):
            continue
        counts[str(value)] = counts.get(str(value), 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


@router.get("/overview")
def overview(admin: dict = Depends(require_superadmin)):
    """Dashboard feed: totals, 7-day trends, class/model mix, latest activity."""
    with connect() as conn:
        totals = {
            "users": conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"],
            "sessions": conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"],
            "history_items": conn.execute("SELECT COUNT(*) AS n FROM history").fetchone()["n"],
            "notices_published": conn.execute(
                "SELECT COUNT(*) AS n FROM notices WHERE status = 'published'"
            ).fetchone()["n"],
            "notices_draft": conn.execute(
                "SELECT COUNT(*) AS n FROM notices WHERE status = 'draft'"
            ).fetchone()["n"],
        }
        trends = {
            "new_users_7d": conn.execute(
                "SELECT COUNT(*) AS n FROM users WHERE created_at >= datetime('now','-7 days')"
            ).fetchone()["n"],
            "new_diagnoses_7d": conn.execute(
                "SELECT COUNT(*) AS n FROM history WHERE created_at >= datetime('now','-7 days')"
            ).fetchone()["n"],
            "sessions_24h": conn.execute(
                "SELECT COUNT(*) AS n FROM sessions WHERE created_at >= datetime('now','-1 day')"
            ).fetchone()["n"],
            "diagnoses_24h": conn.execute(
                "SELECT COUNT(*) AS n FROM history WHERE created_at >= datetime('now','-1 day')"
            ).fetchone()["n"],
        }
        recent_users = [
            {
                "id": r["id"],
                "contact": r["contact"],
                "name": r["display_name"],
                "role": r["role"],
                "created_at": r["created_at"],
            }
            for r in conn.execute(
                "SELECT id, contact, display_name, role, created_at FROM users "
                "ORDER BY id DESC LIMIT 5"
            ).fetchall()
        ]
        recent_rows = conn.execute(
            "SELECT h.payload, h.updated_at, u.display_name AS user_name "
            "FROM history h JOIN users u ON u.id = h.user_id "
            "ORDER BY h.updated_at DESC, CAST(h.id AS INTEGER) DESC LIMIT 10"
        ).fetchall()
        all_rows = conn.execute("SELECT payload FROM history").fetchall()

    recent_diagnoses = [
        {
            "user": r["user_name"],
            "class": _payload_of(r["payload"], "class"),
            "confidence": _payload_of(r["payload"], "confidence"),
            "model": _payload_of(r["payload"], "model"),
            "at": r["updated_at"],
        }
        for r in recent_rows
    ]

    return {
        "totals": totals,
        "trends": trends,
        "class_distribution": _tally(all_rows, "class"),
        "model_usage": _tally(all_rows, "model"),
        "recent_users": recent_users,
        "recent_diagnoses": recent_diagnoses,
    }


@router.get("/models")
def models(admin: dict = Depends(require_superadmin)):
    """Model inventory: weights on disk, training config, metrics, gates."""
    weights = _weights_dir()
    labels = _read_json(weights / "labels.json") or {}
    config = _read_json(weights / "config.json") or {}
    metrics = _read_json(weights / "metrics.json") or {}
    ensemble_config = _read_json(weights / "ensemble_config.json") or {}
    thresholds = _read_json(_base_dir() / "calibration" / "thresholds.json") or {}

    items = []
    for mid in MODEL_ORDER:
        if mid == "ensemble":
            members = ensemble_config.get("members") or [
                m for m in MODEL_ORDER if m != "ensemble"
            ]
            files = [weights / m / "best.pt" for m in members]
            available = all(f.exists() for f in files)
            size = sum(f.stat().st_size for f in files if f.exists())
            modified = max(
                (f.stat().st_mtime for f in files if f.exists()), default=None
            )
        else:
            f = weights / mid / "best.pt"
            available = f.exists()
            size = f.stat().st_size if available else 0
            modified = f.stat().st_mtime if available else None

        score = metrics.get(mid) or {}
        items.append({
            "id": mid,
            "name": MODEL_NAMES.get(mid, mid),
            "available": bool(available),
            "size_mb": round(size / 1e6, 1) if size else 0.0,
            "modified_at": (
                datetime.fromtimestamp(modified).isoformat(timespec="seconds")
                if modified else None
            ),
            "accuracy": round(score["accuracy"], 4) if isinstance(score.get("accuracy"), (int, float)) else None,
            "f1_macro": round(score["f1_macro"], 4) if isinstance(score.get("f1_macro"), (int, float)) else None,
        })

    return {
        "default": "ensemble",
        "weights_dir": str(weights),
        "classes": labels.get("api") or [],
        "train_config": {
            "epochs": config.get("epochs"),
            "batch": config.get("batch"),
            "img": config.get("img"),
            "seed": config.get("seed"),
            "trained_at": config.get("time"),
        },
        "ensemble": ensemble_config,
        "thresholds": thresholds,
        "items": items,
    }


@router.get("/users/{user_id}")
def user_detail(user_id: int, admin: dict = Depends(require_superadmin)):
    """One farmer's profile plus activity aggregates (dashboard drill-down)."""
    with connect() as conn:
        row = conn.execute(
            "SELECT id, contact, display_name, role, created_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "User not found.")

        history_count = conn.execute(
            "SELECT COUNT(*) AS n FROM history WHERE user_id = ?", (user_id,)
        ).fetchone()["n"]
        session_count = conn.execute(
            "SELECT COUNT(*) AS n FROM sessions WHERE user_id = ?", (user_id,)
        ).fetchone()["n"]
        last_session_at = conn.execute(
            "SELECT MAX(created_at) AS at FROM sessions WHERE user_id = ?", (user_id,)
        ).fetchone()["at"]
        last_diagnosis_at = conn.execute(
            "SELECT MAX(updated_at) AS at FROM history WHERE user_id = ?", (user_id,)
        ).fetchone()["at"]
        notices_read = conn.execute(
            "SELECT COUNT(*) AS n FROM notice_reads WHERE user_id = ?", (user_id,)
        ).fetchone()["n"]
        rows = conn.execute(
            "SELECT payload FROM history WHERE user_id = ?", (user_id,)
        ).fetchall()

    return {
        "id": row["id"],
        "contact": row["contact"],
        "name": row["display_name"],
        "role": row["role"],
        "created_at": row["created_at"],
        "history_count": history_count,
        "session_count": session_count,
        "notices_read": notices_read,
        "last_session_at": last_session_at,
        "last_diagnosis_at": last_diagnosis_at,
        "class_distribution": _tally(rows, "class"),
        "model_usage": _tally(rows, "model"),
    }


@router.get("/users/{user_id}/history")
def user_history(
    user_id: int,
    limit: int = 50,
    admin: dict = Depends(require_superadmin),
):
    """A farmer's diagnoses, newest first, with the payload parsed.

    Only display fields are returned — payloads can embed big probability maps.
    """
    limit = max(1, min(limit, 50))
    with connect() as conn:
        row = conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "User not found.")
        rows = conn.execute(
            "SELECT id, payload, created_at, updated_at FROM history "
            "WHERE user_id = ? ORDER BY updated_at DESC, id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()

    items = []
    for r in rows:
        try:
            payload = json.loads(r["payload"])
        except Exception:
            payload = {}
        items.append({
            "id": r["id"],
            "class": payload.get("class"),
            "confidence": payload.get("confidence"),
            "model": payload.get("model"),
            "is_unknown": payload.get("is_unknown"),
            "timestamp": payload.get("timestamp"),
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        })
    return {"items": items, "count": len(items)}
