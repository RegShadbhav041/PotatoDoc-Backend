"""Public notice feed: news, announcements and crop alerts.

Read/unread state is per-farmer (notice_reads). The list itself is public —
anonymous callers just don't get read flags. Admin CRUD lives in admin.py.
No torch imports, same rationale as auth.py / history.py.
"""
from fastapi import APIRouter, Depends, HTTPException

from auth import optional_user, require_user
from db import NOTICE_CATEGORIES, connect

router = APIRouter(prefix="/notices", tags=["notices"])

MAX_LIMIT = 200


def _unread_count(conn, user_id):
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM notices n "
        "WHERE n.status = 'published' "
        "AND NOT EXISTS (SELECT 1 FROM notice_reads r "
        "                WHERE r.notice_id = n.id AND r.user_id = ?)",
        (user_id,),
    ).fetchone()
    return row["n"]


@router.get("")
def list_notices(
    category: str = "",
    limit: int = 100,
    user: dict | None = Depends(optional_user),
):
    """Published notices, newest first. Attaches read state when signed in."""
    limit = max(1, min(limit, MAX_LIMIT))
    sql = (
        "SELECT n.id, n.category, n.title, n.body, n.author_name, "
        "n.created_at, n.updated_at "
        "FROM notices n WHERE n.status = 'published'"
    )
    args: list = []
    if category:
        if category not in NOTICE_CATEGORIES:
            raise HTTPException(422, "Unknown category.")
        sql += " AND n.category = ?"
        args.append(category)
    sql += " ORDER BY n.id DESC LIMIT ?"
    args.append(limit)

    with connect() as conn:
        rows = conn.execute(sql, args).fetchall()
        read_ids = set()
        unread = 0
        if user is not None:
            unread = _unread_count(conn, user["id"])
            if rows:
                marks = conn.execute(
                    "SELECT notice_id FROM notice_reads WHERE user_id = ?",
                    (user["id"],),
                ).fetchall()
                read_ids = {m["notice_id"] for m in marks}

    items = []
    for row in rows:
        item = {
            "id": row["id"],
            "category": row["category"],
            "title": row["title"],
            "body": row["body"],
            "author_name": row["author_name"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        if user is not None:
            item["read"] = row["id"] in read_ids
        items.append(item)

    return {"items": items, "unread": unread if user is not None else 0}


@router.get("/unread-count")
def unread_count(user: dict = Depends(require_user)):
    with connect() as conn:
        return {"unread": _unread_count(conn, user["id"])}


@router.post("/{notice_id}/read")
def mark_read(notice_id: int, user: dict = Depends(require_user)):
    """Idempotent — re-reading a notice is a no-op. 404 when the notice does
    not exist or is still a draft.
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM notices WHERE id = ? AND status = 'published'",
            (notice_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "Notice not found.")
        conn.execute(
            "INSERT OR IGNORE INTO notice_reads (user_id, notice_id) VALUES (?, ?)",
            (user["id"], notice_id),
        )
        unread = _unread_count(conn, user["id"])
    return {"ok": True, "unread": unread}


@router.post("/read-all")
def mark_all_read(user: dict = Depends(require_user)):
    with connect() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO notice_reads (user_id, notice_id) "
            "SELECT ?, id FROM notices WHERE status = 'published'",
            (user["id"],),
        )
        marked = cur.rowcount
        unread = _unread_count(conn, user["id"])
    return {"marked": marked, "unread": unread}
