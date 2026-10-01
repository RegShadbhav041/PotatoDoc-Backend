"""Seed the three notices from the designer's News & Notification mockup.

Usage:
  python3 scripts/seed_notices.py           # only if the table is empty
  python3 scripts/seed_notices.py --force   # insert even if notices exist
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db import connect

SEED = [
    {
        "category": "update",
        "title": "A note for the growing season",
        "body": "Check the underside of leaves regularly as the weather turns "
                "wetter. Early detection makes a difference.",
    },
    {
        "category": "crop_alert",
        "title": "Blight risk rising after rainfall",
        "body": "Avoid watering leaves this evening and avoid overhead watering.",
    },
    {
        "category": "announcement",
        "title": "New Ensemble model is now available",
        "body": "Improved recognition for early and late blight is live in the "
                "Diagnose tab.",
    },
]


def main():
    ap = argparse.ArgumentParser(description="Seed demo notices.")
    ap.add_argument("--force", action="store_true",
                    help="insert even when notices already exist")
    args = ap.parse_args()

    with connect() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM notices").fetchone()["n"]
        if count and not args.force:
            print(f"notices table already has {count} rows — use --force to add anyway")
            return
        for item in SEED:
            conn.execute(
                "INSERT INTO notices (category, title, body, author_name, status) "
                "VALUES (?, ?, ?, 'Super Admin', 'published')",
                (item["category"], item["title"], item["body"]),
            )
    print(f"seeded {len(SEED)} notices")


if __name__ == "__main__":
    main()
