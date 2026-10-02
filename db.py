"""SQLite persistence for farmer accounts, sessions and synced history.

Stdlib only. auth.py and history.py import this and nothing torch-related,
so both routers stay testable without loading the ML weights.
"""
import os
import sqlite3
from pathlib import Path

DB_PATH = Path(
    os.environ.get("POTATO_DB")
    or str(Path(__file__).resolve().parent / "potatodoc.db")
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  contact       TEXT NOT NULL UNIQUE,
  display_name  TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  role          TEXT NOT NULL DEFAULT 'user',
  photo         BLOB,
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS sessions (
  token      TEXT PRIMARY KEY,
  user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS history (
  id         TEXT NOT NULL,
  user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  payload    TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (user_id, id)
);
CREATE TABLE IF NOT EXISTS notices (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  category    TEXT NOT NULL DEFAULT 'announcement',
  title       TEXT NOT NULL,
  body        TEXT NOT NULL DEFAULT '',
  author_name TEXT NOT NULL DEFAULT 'Super Admin',
  status      TEXT NOT NULL DEFAULT 'published',
  created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
  created_at  TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS notice_reads (
  user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  notice_id INTEGER NOT NULL REFERENCES notices(id) ON DELETE CASCADE,
  read_at   TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (user_id, notice_id)
);
CREATE TABLE IF NOT EXISTS notice_images (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  notice_id  INTEGER NOT NULL REFERENCES notices(id) ON DELETE CASCADE,
  data       BLOB NOT NULL,
  mime       TEXT NOT NULL DEFAULT 'image/jpeg',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS tickets (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  subject    TEXT NOT NULL,
  status     TEXT NOT NULL DEFAULT 'open',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS ticket_messages (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  ticket_id  INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
  user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  body       TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS history_photos (
  user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  item_id    TEXT NOT NULL,
  data       BLOB NOT NULL,
  mime       TEXT NOT NULL DEFAULT 'image/jpeg',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (user_id, item_id)
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_history_user ON history(user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_notices_status ON notices(status, id DESC);
CREATE INDEX IF NOT EXISTS idx_notice_images_notice ON notice_images(notice_id, id);
CREATE INDEX IF NOT EXISTS idx_tickets_user ON tickets(user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_ticket_messages_ticket ON ticket_messages(ticket_id, id);
"""

# Keep the schema string pure SQL (executescript ignores nothing) — the
# python constants live here instead.
NOTICE_CATEGORIES = ("update", "announcement", "crop_alert", "new_product", "medicine")
NOTICE_STATUSES = ("draft", "published")
ROLES = ("user", "superadmin")
TICKET_STATUSES = ("open", "resolved")
ACCOUNT_STATUSES = ("active", "banned")


def connect():
    """Fresh connection per call — FastAPI sync endpoints run on a threadpool."""
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    with connect() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
    _seed_superadmin()


def _migrate(conn):
    """Hand-rolled migrations — there is no schema-version mechanism.

    CREATE TABLE IF NOT EXISTS never alters an existing table, so the live
    database (created before roles existed) needs an explicit ADD COLUMN.
    """
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
    if "role" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
    if "photo" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN photo BLOB")
    if "status" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")


def _seed_superadmin():
    """Create/promote the superadmin account from POTATO_SUPERADMIN_* env vars.

    The contact/password pair is authoritative for that contact: the password is
    (re)set whenever it no longer verifies, so a wiped Cloud Run database comes
    back with a working superadmin on the next boot. Existing accounts are never
    touched otherwise, and no other row is ever modified.
    """
    contact_raw = (os.environ.get("POTATO_SUPERADMIN_CONTACT") or "").strip()
    password = os.environ.get("POTATO_SUPERADMIN_PASSWORD") or ""
    if not contact_raw or not password:
        if contact_raw or password:
            print("[db] superadmin seed skipped: both POTATO_SUPERADMIN_CONTACT and "
                  "POTATO_SUPERADMIN_PASSWORD must be set")
        return

    # Lazy import: auth.py imports db at module level.
    from auth import hash_password, normalize_contact, verify_password

    contact = normalize_contact(contact_raw)
    with connect() as conn:
        row = conn.execute(
            "SELECT id, role, password_hash FROM users WHERE contact = ?",
            (contact,),
        ).fetchone()
        if row is None:
            name = (os.environ.get("POTATO_SUPERADMIN_NAME") or "Super Admin").strip()
            conn.execute(
                "INSERT INTO users (contact, display_name, password_hash, role) "
                "VALUES (?, ?, ?, 'superadmin')",
                (contact, name, hash_password(password)),
            )
            print(f"[db] created superadmin {contact}")
        else:
            if row["role"] != "superadmin":
                conn.execute("UPDATE users SET role = 'superadmin' WHERE id = ?", (row["id"],))
                print(f"[db] promoted {contact} to superadmin")
            if not verify_password(password, row["password_hash"]):
                conn.execute(
                    "UPDATE users SET password_hash = ? WHERE id = ?",
                    (hash_password(password), row["id"]),
                )
                print(f"[db] superadmin password resynced from env for {contact}")
