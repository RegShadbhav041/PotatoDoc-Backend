"""Promote (or create) a superadmin account directly against the database.

Usage — run from anywhere, against the same DB the server uses:
  python3 scripts/make_superadmin.py <contact>
  python3 scripts/make_superadmin.py <contact> --name "Shadbhav Regmi"
  python3 scripts/make_superadmin.py <contact> --password 'secret'   # non-interactive

- Existing contact -> role set to 'superadmin' (password untouched unless given).
- Unknown contact  -> created (password prompted if --password omitted).
- Idempotent: safe to run repeatedly.

Prefer the POTATO_SUPERADMIN_CONTACT/PASSWORD env vars for deployments (seeded
automatically at boot); this script is for local/manual management.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import getpass

from auth import hash_password, normalize_contact, verify_password
from db import connect


def main():
    ap = argparse.ArgumentParser(description="Promote a contact to superadmin.")
    ap.add_argument("contact", help="email or phone the farmer registered with")
    ap.add_argument("--name", default="Super Admin", help="display name when creating")
    ap.add_argument("--password", default=None, help="password for a new account")
    ap.add_argument("--set-password", dest="set_password", default=None,
                    help="also reset the password of an existing account")
    args = ap.parse_args()

    contact = normalize_contact(args.contact)
    if not contact:
        sys.exit("error: contact is required")

    with connect() as conn:
        row = conn.execute(
            "SELECT id, role, password_hash FROM users WHERE contact = ?",
            (contact,),
        ).fetchone()

        if row is None:
            password = args.password
            if not password:
                password = getpass.getpass("Password for the new account (min 8 chars): ")
                confirm = getpass.getpass("Repeat password: ")
                if password != confirm:
                    sys.exit("error: passwords do not match")
            if len(password) < 8:
                sys.exit("error: password must be at least 8 characters")
            conn.execute(
                "INSERT INTO users (contact, display_name, password_hash, role) "
                "VALUES (?, ?, ?, 'superadmin')",
                (contact, args.name.strip() or "Super Admin", hash_password(password)),
            )
            print(f"created superadmin: {contact}")
            return

        if row["role"] != "superadmin":
            conn.execute("UPDATE users SET role = 'superadmin' WHERE id = ?", (row["id"],))
            print(f"promoted to superadmin: {contact}")
        else:
            print(f"already a superadmin: {contact}")

        if args.set_password:
            if len(args.set_password) < 8:
                sys.exit("error: password must be at least 8 characters")
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (hash_password(args.set_password), row["id"]),
            )
            print("password updated")
        elif args.password and not verify_password(args.password, row["password_hash"]):
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (hash_password(args.password), row["id"]),
            )
            print("password updated")


if __name__ == "__main__":
    main()
