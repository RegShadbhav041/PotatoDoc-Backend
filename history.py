"""Per-farmer prediction history, pushed up from the device."""
import json

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from auth import require_user
from db import connect

router = APIRouter(prefix="/history", tags=["history"])

MAX_ITEMS = 50
LOCAL_ONLY_KEYS = ("imageUri",)  # device file paths are meaningless elsewhere


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
    return {"items": [json.loads(r["payload"]) for r in rows]}


@router.put("")
def put_history(body: HistoryPut, user: dict = Depends(require_user)):
    items = [
        i for i in body.items
        if isinstance(i, dict) and i.get("id") not in (None, "")
    ][:MAX_ITEMS]
    # An empty push is never intentional (Clear uses DELETE). Refuse it so a
    # client bug can never wipe a farmer's account.
    if not items:
        return {"upserted": 0}

    with connect() as conn:
        for item in items:
            payload = {k: v for k, v in item.items() if k not in LOCAL_ONLY_KEYS}
            conn.execute(
                "INSERT INTO history (id, user_id, payload, updated_at) "
                "VALUES (?, ?, ?, datetime('now')) "
                "ON CONFLICT(user_id, id) DO UPDATE SET "
                "payload = excluded.payload, updated_at = datetime('now')",
                (str(item["id"]), user["id"], json.dumps(payload, ensure_ascii=False)),
            )
    return {"upserted": len(items)}


@router.delete("", status_code=204)
def clear_history(user: dict = Depends(require_user)):
    with connect() as conn:
        conn.execute("DELETE FROM history WHERE user_id = ?", (user["id"],))
    return Response(status_code=204)
