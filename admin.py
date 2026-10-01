"""Superadmin API: platform stats, farmer accounts and notice CRUD.

Every route requires the superadmin role (see auth.require_superadmin).
Served to the static web panel at /admin; no torch imports.
"""
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
            "SELECT id, contact, display_name, role, created_at "
            "FROM users ORDER BY id DESC"
        ).fetchall()
    return {
        "items": [
            {
                "id": row["id"],
                "contact": row["contact"],
                "name": row["display_name"],
                "role": row["role"],
                "created_at": row["created_at"],
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
