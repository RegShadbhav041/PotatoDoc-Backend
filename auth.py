"""Farmer accounts: register, sign in, sign out, token validation.

No torch imports — deliberately separable from app.py's ML endpoints.
"""
import base64
import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Header, Response, File, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from db import connect
from media import normalise, to_data_uri

router = APIRouter(prefix="/auth", tags=["auth"])

TOKEN_TTL_DAYS = int(os.environ.get("POTATO_TOKEN_TTL_DAYS", "30"))
LOGIN_WINDOW_S = 300
LOGIN_MAX_ATTEMPTS = 10
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1
SCRYPT_MAXMEM = 64 * 1024 * 1024

# contact -> [monotonic timestamps of recent failed logins]
_attempt_log = {}


def normalize_contact(raw):
    return (raw or "").strip().lower()


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, maxmem=SCRYPT_MAXMEM,
    )
    return "$".join([
        "scrypt", str(SCRYPT_N), str(SCRYPT_R), str(SCRYPT_P),
        base64.b64encode(salt).decode(),
        base64.b64encode(digest).decode(),
    ])


def verify_password(password, stored):
    try:
        _tag, n, r, p, salt_b64, digest_b64 = stored.split("$")
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=base64.b64decode(salt_b64),
            n=int(n), r=int(r), p=int(p), maxmem=SCRYPT_MAXMEM,
        )
        return hmac.compare_digest(digest, base64.b64decode(digest_b64))
    except Exception:
        return False


class RegisterIn(BaseModel):
    contact: str = ""
    name: str = ""
    password: str = ""


class LoginIn(BaseModel):
    contact: str = ""
    password: str = ""


def _rate_limited(contact):
    now = time.monotonic()
    hits = [t for t in _attempt_log.get(contact, []) if now - t < LOGIN_WINDOW_S]
    _attempt_log[contact] = hits
    return len(hits) >= LOGIN_MAX_ATTEMPTS


def _record_failure(contact):
    if len(_attempt_log) > 10000:
        _attempt_log.clear()
    _attempt_log.setdefault(contact, []).append(time.monotonic())


def _clear_failures(contact):
    _attempt_log.pop(contact, None)


def _photo_data_uri(user_id):
    """The farmer's profile picture as a data-URI, or None.

    Loaded only where a response actually needs it — the per-request session
    lookup stays blob-free.
    """
    with connect() as conn:
        row = conn.execute("SELECT photo FROM users WHERE id = ?", (user_id,)).fetchone()
    return to_data_uri(row["photo"]) if row is not None and row["photo"] else None


def _issue_session(user_id, contact, name, role="user"):
    token = secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(days=TOKEN_TTL_DAYS)).isoformat()
    with connect() as conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
            (token, user_id, expires_at),
        )
    photo = _photo_data_uri(user_id)
    return token, {"id": user_id, "contact": contact, "name": name, "role": role, "photo": photo}


@router.post("/register", status_code=201)
def register(body: RegisterIn):
    contact = normalize_contact(body.contact)
    name = (body.name or "").strip()
    if not contact:
        raise HTTPException(422, "Email or phone is required.")
    if not name:
        raise HTTPException(422, "Name is required.")
    if len(body.password or "") < 8:
        raise HTTPException(422, "Password must be at least 8 characters.")

    with connect() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO users (contact, display_name, password_hash) VALUES (?, ?, ?)",
                (contact, name, hash_password(body.password)),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(409, "That contact is already registered.")
        user_id = cur.lastrowid

    token, user = _issue_session(user_id, contact, name, "user")
    return JSONResponse({"token": token, "user": user}, status_code=201)


@router.post("/login")
def login(body: LoginIn):
    contact = normalize_contact(body.contact)
    if not contact or not body.password:
        raise HTTPException(422, "Email or phone and password are required.")
    if _rate_limited(contact):
        raise HTTPException(429, "Too many attempts. Try again shortly.")

    with connect() as conn:
        row = conn.execute(
            "SELECT id, contact, display_name, role, password_hash FROM users WHERE contact = ?",
            (contact,),
        ).fetchone()

    # Identical 401 for unknown contact and wrong password: no user enumeration.
    if row is None or not verify_password(body.password, row["password_hash"]):
        _record_failure(contact)
        raise HTTPException(401, "Invalid credentials")

    _clear_failures(contact)
    token, user = _issue_session(row["id"], row["contact"], row["display_name"], row["role"])
    return {"token": token, "user": user}


def _resolve_session(authorization):
    """Shared token lookup. Returns the user dict, or None when the header is
    missing/malformed/the token is unknown or expired (expired rows are purged).
    """
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    token = token.strip()

    with connect() as conn:
        row = conn.execute(
            "SELECT u.id, u.contact, u.display_name, u.role, s.expires_at "
            "FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token = ?",
            (token,),
        ).fetchone()

    if row is None:
        return None
    if row["expires_at"] <= datetime.now(timezone.utc).isoformat():
        with connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        return None

    return {
        "id": row["id"],
        "contact": row["contact"],
        "name": row["display_name"],
        "role": row["role"],
    }


def require_user(authorization: str = Header(default="")):
    """FastAPI dependency: resolves a valid bearer token to the farmer."""
    user = _resolve_session(authorization)
    if user is None:
        raise HTTPException(401, "Not authenticated")
    return user


def optional_user(authorization: str = Header(default="")):
    """Like require_user, but anonymous requests resolve to None instead of 401.

    Used by public endpoints (the notices list) that attach per-user read state
    only when the caller happens to be signed in.
    """
    return _resolve_session(authorization)


def require_superadmin(user: dict = Depends(require_user)):
    """FastAPI dependency: 403 unless the signed-in user is a superadmin."""
    if user.get("role") != "superadmin":
        raise HTTPException(403, "Superadmin access required")
    return user


@router.get("/me")
def me(user: dict = Depends(require_user)):
    return {**user, "photo": _photo_data_uri(user["id"])}


class ProfileIn(BaseModel):
    name: str = ""
    contact: str = ""


@router.put("/me")
def update_me(body: ProfileIn, user: dict = Depends(require_user)):
    """Edit the signed-in farmer's profile (Profile tab -> "Your details").

    contact is the login identity, so it is normalised and checked for
    collisions before the row is touched; the current token keeps working.
    """
    name = (body.name or "").strip()
    contact = normalize_contact(body.contact)
    if not name:
        raise HTTPException(422, "Name is required.")
    if len(name) > 80:
        raise HTTPException(422, "Name must be 80 characters or fewer.")
    if not contact:
        raise HTTPException(422, "Email or phone is required.")

    with connect() as conn:
        clash = conn.execute(
            "SELECT id FROM users WHERE contact = ? AND id != ?",
            (contact, user["id"]),
        ).fetchone()
        if clash is not None:
            raise HTTPException(409, "That contact is already registered.")
        conn.execute(
            "UPDATE users SET display_name = ?, contact = ? WHERE id = ?",
            (name, contact, user["id"]),
        )
    return {
        "id": user["id"],
        "contact": contact,
        "name": name,
        "role": user.get("role", "user"),
        "photo": _photo_data_uri(user["id"]),
    }


@router.post("/me/photo")
def upload_photo(file: UploadFile = File(...), user: dict = Depends(require_user)):
    """Set the signed-in farmer's profile picture (Profile tab -> details sheet).

    Re-encoded server-side (EXIF stripped, longest edge 512px, JPEG) so a
    phone camera dump can never bloat the database.
    """
    data, mime = normalise(file.file.read(), file.content_type, max_edge=512, quality=82)
    with connect() as conn:
        conn.execute("UPDATE users SET photo = ? WHERE id = ?", (data, user["id"]))
    return {**user, "photo": to_data_uri(data, mime)}


@router.delete("/me/photo")
def delete_photo(user: dict = Depends(require_user)):
    with connect() as conn:
        conn.execute("UPDATE users SET photo = NULL WHERE id = ?", (user["id"],))
    return {**user, "photo": None}


@router.post("/logout", status_code=204)
def logout(authorization: str = Header(default="")):
    """Idempotent by design: signing out must never fail on a dead token."""
    _, _, token = (authorization or "").partition(" ")
    token = token.strip()
    if token:
        with connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
    return Response(status_code=204)
