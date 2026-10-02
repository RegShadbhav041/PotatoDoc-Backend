"""Schema contract: profile photo column, notice gallery, new categories."""
import sqlite3
import unittest

from db import NOTICE_CATEGORIES, _migrate, connect


class PhotoColumnTest(unittest.TestCase):
    def test_fresh_database_has_the_photo_column(self):
        with connect() as conn:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
        self.assertIn("photo", cols)

    def test_a_legacy_users_table_gains_the_photo_column(self):
        # Simulates a database created before photos existed (the migration
        # path real deployments take — CREATE TABLE IF NOT EXISTS never alters).
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, contact TEXT, "
            "display_name TEXT, password_hash TEXT, "
            "created_at TEXT DEFAULT (datetime('now')))"
        )
        _migrate(conn)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
        self.assertIn("photo", cols)
        self.assertIn("role", cols)
        conn.close()


class NoticeImagesTest(unittest.TestCase):
    def test_table_exists_with_the_expected_columns(self):
        with connect() as conn:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(notice_images)")}
        self.assertTrue({"id", "notice_id", "data", "mime", "created_at"} <= cols)

    def test_rows_cascade_when_the_notice_is_deleted(self):
        with connect() as conn:
            notice = conn.execute(
                "INSERT INTO notices (title, body) VALUES ('t', 'b')"
            ).lastrowid
            conn.execute(
                "INSERT INTO notice_images (notice_id, data, mime) VALUES (?, ?, ?)",
                (notice, b"bytes", "image/jpeg"),
            )
            conn.execute("DELETE FROM notices WHERE id = ?", (notice,))
            left = conn.execute(
                "SELECT COUNT(*) AS n FROM notice_images WHERE notice_id = ?",
                (notice,),
            ).fetchone()["n"]
        self.assertEqual(left, 0)


class CategoriesTest(unittest.TestCase):
    def test_new_categories_are_offered(self):
        for category in ("new_product", "medicine"):
            self.assertIn(category, NOTICE_CATEGORIES)
        self.assertEqual(len(NOTICE_CATEGORIES), 5)


if __name__ == "__main__":
    unittest.main()
