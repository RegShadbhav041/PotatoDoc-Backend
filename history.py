"""Per-farmer prediction history, pushed up from the device.

Photos: the device keeps its local file path (`imageUri`, never synced) and
uploads the actual bytes once to POST /history/{id}/photo. The server stores
a downscaled JPEG so the superadmin can review exactly what was scanned.
GET /history flags every item that already has a server copy (has_photo) so
the app's sync can self-heal missed uploads; the flag itself is server-derived
and is stripped from pushes and merges, exactly like `imageUri`.
"""
import json

from fastapi import APIRouter, Depends, HTTPException, Response, File, UploadFile
from pydantic import BaseModel

from auth import require_user
from db import connect
from media import normalise

router = APIRouter(prefix="/history", tags=["history"])

MAX_ITEMS = 50
LOCAL_ONLY_KEYS = ("imageUri",)  # device file paths are meaningless elsewhere
SERVER_ONLY_KEYS = ("has_photo",)  # derived by GET /history, never stored


class HistoryPut(BaseModel):
    items: list = []


@router.get("")
def get_history(user: dict = Depends(require_user)):
    with connect() as conn:
        rows = conn.execute(
            "SELECT payload FROM history WHERE user_id = ? "
            "ORDER BY CAST(id AS INTEGER) DESC",
            (user["id"],),
        ).fetchall()
        photo_ids = {
            r["item_id"]
            for r in conn.execute(
                "SELECT item_id FROM history_photos WHERE user_id = ?",
                (user["id"],),
            ).fetchall()
        }
    items = []
    for r in rows:
        try:
            item = json.loads(r["payload"])
        except Exception:
            item = {}
        if isinstance(item, dict):
            item["has_photo"] = str(item.get("id", "")) in photo_ids
        items.append(item)
    return {"items": items}


@router.put("")
def put_history(body: HistoryPut, user: dict = Depends(require_user)):
    items = [
        i
        for i in body.items
        if isinstance(i, dict) and i.get("id") not in (None, "")
    ][:MAX_ITEMS]
    # An empty push is never intentional (Clear uses DELETE). Refuse it so a
    # client bug can never wipe a farmer's account.
    if not items:
        return {"upserted": 0}

    with connect() as conn:
        for item in items:
            payload = {
                k: v
                for k, v in item.items()
                if k not in LOCAL_ONLY_KEYS and k not in SERVER_ONLY_KEYS
            }
            conn.execute(
                "INSERT INTO history (id, user_id, payload, updated_at) "
                "VALUES (?, ?, ?, datetime('now')) "
                "ON CONFLICT(user_id, id) DO UPDATE SET "
                "payload = excluded.payload, updated_at = datetime('now')",
                (str(item["id"]), user["id"], json.dumps(payload, ensure_ascii=False)),
            )
    return {"upserted": len(items)}


@router.post("/{item_id}/photo")
def upload_history_photo(
    item_id: str,
    file: UploadFile = File(...),
    user: dict = Depends(require_user),
):
    """Attach the scanned leaf photo to one history item (idempotent upsert).

    Re-encoded server-side (EXIF stripped, longest edge 768px, JPEG) — a
    phone camera dump must never bloat the database. Requires the history row
    to exist, so the app uploads right after the item has synced.
    """
    data, mime = normalise(file.file.read(), file.content_type, max_edge=768, quality=80)
    with connect() as conn:
        exists = conn.execute(
            "SELECT id FROM history WHERE user_id = ? AND id = ?",
            (user["id"], str(item_id)),
        ).fetchone()
        if exists is None:
            raise HTTPException(404, "History item not found.")
        conn.execute(
            "INSERT INTO history_photos (user_id, item_id, data, mime) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id, item_id) DO UPDATE SET "
            "data = excluded.data, mime = excluded.mime, "
            "created_at = datetime('now')",
            (user["id"], str(item_id), data, mime),
        )
    return {"ok": True, "item_id": str(item_id)}


@router.get("/{item_id}/photo")
def get_history_photo(item_id: str, user: dict = Depends(require_user)):
    """The stored JPEG for one of the signed-in farmer's own items."""
    with connect() as conn:
        row = conn.execute(
            "SELECT data, mime FROM history_photos WHERE user_id = ? AND item_id = ?",
            (user["id"], str(item_id)),
        ).fetchone()
    if row is None:
        raise HTTPException(404, "Photo not found.")
    return Response(
        content=row["data"],
        media_type=row["mime"],
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.delete("", status_code=204)
def clear_history(user: dict = Depends(require_user)):
    with connect() as conn:
        conn.execute("DELETE FROM history WHERE user_id = ?", (user["id"],))
        conn.execute("DELETE FROM history_photos WHERE user_id = ?", (user["id"],))
    return Response(status_code=204)
