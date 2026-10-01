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

from fastapi import APIRouter, Depends, HTTPException, Header, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from db import connect

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


def _issue_session(user_id, contact, name):
    token = secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(days=TOKEN_TTL_DAYS)).isoformat()
    with connect() as conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
            (token, user_id, expires_at),
        )
    return token, {"id": user_id, "contact": contact, "name": name}


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

    token, user = _issue_session(user_id, contact, name)
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
            "SELECT id, contact, display_name, password_hash FROM users WHERE contact = ?",
            (contact,),
        ).fetchone()

    # Identical 401 for unknown contact and wrong password: no user enumeration.
    if row is None or not verify_password(body.password, row["password_hash"]):
        _record_failure(contact)
        raise HTTPException(401, "Invalid credentials")

    _clear_failures(contact)
    token, user = _issue_session(row["id"], row["contact"], row["display_name"])
    return {"token": token, "user": user}


def require_user(authorization: str = Header(default="")):
    """FastAPI dependency: resolves a valid bearer token to the farmer."""
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(401, "Not authenticated")
    token = token.strip()

    with connect() as conn:
        row = conn.execute(
            "SELECT u.id, u.contact, u.display_name, s.expires_at "
            "FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token = ?",
            (token,),
        ).fetchone()

    if row is None:
        raise HTTPException(401, "Not authenticated")
    if row["expires_at"] <= datetime.now(timezone.utc).isoformat():
        with connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        raise HTTPException(401, "Not authenticated")

    return {"id": row["id"], "contact": row["contact"], "name": row["display_name"]}


@router.get("/me")
def me(user: dict = Depends(require_user)):
    return user


@router.post("/logout", status_code=204)
def logout(authorization: str = Header(default="")):
    """Idempotent by design: signing out must never fail on a dead token."""
    _, _, token = (authorization or "").partition(" ")
    token = token.strip()
    if token:
        with connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
    return Response(status_code=204)
