"""Support tickets: a two-way chat between one farmer and the superadmin.

A farmer opens a ticket with a subject and a first message; both sides then
append messages until the superadmin resolves it. A farmer reply on a
resolved ticket automatically reopens it. The superadmin's list/reply/status
routes live in admin.py (they ride the shared /admin router and the
require_superadmin guard) and reuse the helpers below.

No torch imports — same rationale as auth.py / history.py / notices.py.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import require_user
from db import connect

router = APIRouter(prefix="/tickets", tags=["tickets"])

MAX_SUBJECT = 200
MAX_BODY = 4000


class TicketIn(BaseModel):
    subject: str = ""
    message: str = ""


class MessageIn(BaseModel):
    body: str = ""


def validate_subject(raw) -> str:
    subject = (raw or "").strip()
    if not subject:
        raise HTTPException(422, "Subject is required.")
    if len(subject) > MAX_SUBJECT:
        raise HTTPException(422, f"Subject must be {MAX_SUBJECT} characters or fewer.")
    return subject


def validate_body(raw, label: str = "Message") -> str:
    text = (raw or "").strip()
    if not text:
        raise HTTPException(422, f"{label} is required.")
    if len(text) > MAX_BODY:
        raise HTTPException(422, f"{label} must be {MAX_BODY} characters or fewer.")
    return text


def ticket_fields(row) -> dict:
    """The shared ticket shape for list and detail responses.

    `message_count` / `last_body` come from the list SQL when present; the
    detail helpers overwrite them with exact values from the loaded thread.
    """
    d = dict(row)
    return {
        "id": d["id"],
        "subject": d["subject"],
        "status": d["status"],
        "created_at": d["created_at"],
        "updated_at": d["updated_at"],
        "message_count": d.get("message_count"),
        "last_body": d.get("last_body"),
    }


def load_messages(conn, ticket_id: int, owner_id: int) -> list:
    """The whole thread, oldest first.

    `from` is derived from the ticket owner: only the owner and superadmins
    may post, so anyone else in the thread is the admin side.
    """
    rows = conn.execute(
        "SELECT m.id, m.user_id, m.body, m.created_at, u.display_name "
        "FROM ticket_messages m JOIN users u ON u.id = m.user_id "
        "WHERE m.ticket_id = ? ORDER BY m.id",
        (ticket_id,),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "body": r["body"],
            "created_at": r["created_at"],
            "author": r["display_name"],
            "from": "farmer" if r["user_id"] == owner_id else "admin",
        }
        for r in rows
    ]


# List SQL shared by the farmer and admin listings (no farmer identity here —
# the admin side joins users itself).
TICKET_LIST_SQL = (
    "SELECT t.*, "
    " (SELECT COUNT(*) FROM ticket_messages m WHERE m.ticket_id = t.id) AS message_count, "
    " (SELECT m.body FROM ticket_messages m WHERE m.ticket_id = t.id "
    "  ORDER BY m.id DESC LIMIT 1) AS last_body "
    "FROM tickets t"
)


def ticket_detail(ticket_id: int, owner_id: int) -> dict:
    """Owner-scoped detail; 404 for other farmers' tickets (no existence leak)."""
    with connect() as conn:
        row = conn.execute(
            "SELECT t.* FROM tickets t WHERE t.id = ? AND t.user_id = ?",
            (ticket_id, owner_id),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "Ticket not found.")
        messages = load_messages(conn, ticket_id, row["user_id"])
    fields = ticket_fields(row)
    fields["message_count"] = len(messages)
    fields["last_body"] = messages[-1]["body"] if messages else None
    return {**fields, "messages": messages}


@router.get("")
def list_tickets(user: dict = Depends(require_user)):
    """The signed-in farmer's tickets, newest activity first."""
    with connect() as conn:
        rows = conn.execute(
            TICKET_LIST_SQL + " WHERE t.user_id = ? ORDER BY t.updated_at DESC, t.id DESC",
            (user["id"],),
        ).fetchall()
    return {"items": [ticket_fields(r) for r in rows]}


@router.post("", status_code=201)
def create_ticket(body: TicketIn, user: dict = Depends(require_user)):
    """Open a ticket: the subject + first message land atomically."""
    subject = validate_subject(body.subject)
    text = validate_body(body.message)
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO tickets (user_id, subject) VALUES (?, ?)",
            (user["id"], subject),
        )
        ticket_id = cur.lastrowid
        conn.execute(
            "INSERT INTO ticket_messages (ticket_id, user_id, body) VALUES (?, ?, ?)",
            (ticket_id, user["id"], text),
        )
    return ticket_detail(ticket_id, user["id"])


@router.get("/{ticket_id}")
def get_ticket(ticket_id: int, user: dict = Depends(require_user)):
    return ticket_detail(ticket_id, user["id"])


@router.post("/{ticket_id}/messages", status_code=201)
def add_message(ticket_id: int, body: MessageIn, user: dict = Depends(require_user)):
    """Reply to own ticket. Reopens it if the superadmin had resolved it."""
    text = validate_body(body.body)
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM tickets WHERE id = ? AND user_id = ?",
            (ticket_id, user["id"]),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "Ticket not found.")
        conn.execute(
            "INSERT INTO ticket_messages (ticket_id, user_id, body) VALUES (?, ?, ?)",
            (ticket_id, user["id"], text),
        )
        conn.execute(
            "UPDATE tickets SET status = 'open', updated_at = datetime('now') WHERE id = ?",
            (ticket_id,),
        )
    return ticket_detail(ticket_id, user["id"])
