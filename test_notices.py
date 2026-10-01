import itertools
import time
import unittest

from test_helpers import client

import auth
from db import connect

_seq = itertools.count()


def sign_up(contact=None):
    if contact is None:
        contact = f"news{next(_seq)}-{time.time_ns()}@example.com"
    res = client.post(
        "/auth/register",
        json={"contact": contact, "name": "Farmer", "password": "potato1234"},
    )
    return {"Authorization": f"Bearer {res.json()['token']}"}


def seed_notice(status="published", category="announcement", title=None):
    """Insert a notice directly — feed tests don't depend on the admin API."""
    title = title or f"notice-{next(_seq)}-{time.time_ns()}"
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO notices (category, title, body, author_name, status) "
            "VALUES (?, ?, 'Body text.', 'Super Admin', ?)",
            (category, title, status),
        )
        return cur.lastrowid, title


class PublicFeedTest(unittest.TestCase):
    def setUp(self):
        auth._attempt_log.clear()

    def test_list_is_public_and_returns_published_newest_first(self):
        older, older_title = seed_notice()
        newer, newer_title = seed_notice()
        res = client.get("/notices")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        titles = [i["title"] for i in body["items"]]
        self.assertIn(newer_title, titles)
        self.assertIn(older_title, titles)
        self.assertLess(titles.index(newer_title), titles.index(older_title))

    def test_drafts_never_appear_in_the_public_list(self):
        _, pub_title = seed_notice(status="published")
        _, draft_title = seed_notice(status="draft")
        titles = [i["title"] for i in client.get("/notices").json()["items"]]
        self.assertIn(pub_title, titles)
        self.assertNotIn(draft_title, titles)

    def test_anonymous_list_carries_no_read_state(self):
        seed_notice()
        body = client.get("/notices").json()
        self.assertEqual(body["unread"], 0)
        for item in body["items"]:
            self.assertNotIn("read", item)

    def test_category_filter(self):
        _, a_title = seed_notice(category="announcement")
        _, c_title = seed_notice(category="crop_alert")
        items = client.get("/notices", params={"category": "crop_alert"}).json()["items"]
        titles = [i["title"] for i in items]
        self.assertIn(c_title, titles)
        self.assertNotIn(a_title, titles)

    def test_unknown_category_is_rejected(self):
        self.assertEqual(client.get("/notices", params={"category": "nope"}).status_code, 422)

    def test_empty_feed_returns_empty_items(self):
        with connect() as conn:
            conn.execute("DELETE FROM notices")
        body = client.get("/notices").json()
        self.assertEqual(body["items"], [])
        self.assertEqual(body["unread"], 0)


class ReadStateTest(unittest.TestCase):
    """Assertions are relative (deltas) — the test database is shared across
    modules, so absolute unread counts are not stable."""

    def setUp(self):
        auth._attempt_log.clear()

    def _unread(self, headers):
        return client.get("/notices", headers=headers).json()["unread"]

    def test_signed_in_list_includes_read_flags_and_unread_count(self):
        h = sign_up()
        before = self._unread(h)
        nid, _ = seed_notice()
        body = client.get("/notices", headers=h).json()
        self.assertEqual(body["unread"], before + 1)
        mine = [i for i in body["items"] if i["id"] == nid]
        self.assertEqual(mine[0]["read"], False)

    def test_mark_read_decrements_unread_and_is_idempotent(self):
        h = sign_up()
        seed_notice()
        before = self._unread(h)
        nid, _ = seed_notice()
        self.assertEqual(self._unread(h), before + 1)

        first = client.post(f"/notices/{nid}/read", headers=h)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["unread"], before)

        again = client.post(f"/notices/{nid}/read", headers=h)
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json()["unread"], before)

        item = [
            i for i in client.get("/notices", headers=h).json()["items"] if i["id"] == nid
        ][0]
        self.assertTrue(item["read"])

    def test_read_state_is_per_farmer(self):
        a, b = sign_up(), sign_up()
        nid, _ = seed_notice()
        a_before, b_before = self._unread(a), self._unread(b)
        client.post(f"/notices/{nid}/read", headers=a)
        self.assertEqual(self._unread(a), a_before - 1)
        self.assertEqual(self._unread(b), b_before)

    def test_mark_read_on_missing_or_draft_notice_is_404(self):
        h = sign_up()
        self.assertEqual(client.post("/notices/999999/read", headers=h).status_code, 404)
        draft_id, _ = seed_notice(status="draft")
        self.assertEqual(client.post(f"/notices/{draft_id}/read", headers=h).status_code, 404)

    def test_mark_read_requires_authentication(self):
        nid, _ = seed_notice()
        self.assertEqual(client.post(f"/notices/{nid}/read").status_code, 401)
        self.assertEqual(client.post("/notices/read-all").status_code, 401)
        self.assertEqual(client.get("/notices/unread-count").status_code, 401)

    def test_read_all_clears_everything_and_reports_new_arrivals(self):
        h = sign_up()
        seed_notice()
        seed_notice()
        res = client.post("/notices/read-all", headers=h)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["unread"], 0)
        self.assertEqual(self._unread(h), 0)

        again = client.post("/notices/read-all", headers=h)
        self.assertEqual(again.json()["marked"], 0)

        seed_notice()
        self.assertEqual(self._unread(h), 1)

    def test_unread_count_endpoint_matches_the_list(self):
        h = sign_up()
        seed_notice()
        listed = client.get("/notices", headers=h).json()["unread"]
        counted = client.get("/notices/unread-count", headers=h).json()["unread"]
        self.assertEqual(listed, counted)
        self.assertGreaterEqual(counted, 1)


if __name__ == "__main__":
    unittest.main()
