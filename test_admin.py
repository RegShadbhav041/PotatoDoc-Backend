import itertools
import time
import unittest

from test_helpers import client

import auth
from db import connect

_seq = itertools.count()


def sign_up(contact=None):
    if contact is None:
        contact = f"adm{next(_seq)}-{time.time_ns()}@example.com"
    res = client.post(
        "/auth/register",
        json={"contact": contact, "name": "Farmer", "password": "potato1234"},
    )
    return {"Authorization": f"Bearer {res.json()['token']}"}, res.json()["user"]["id"]


def make_superadmin():
    """Register a fresh farmer, then promote them straight in the database.

    The session lookup re-reads the role on every request, so the token issued
    at registration picks up the promotion immediately.
    """
    headers, user_id = sign_up()
    with connect() as conn:
        conn.execute("UPDATE users SET role = 'superadmin' WHERE id = ?", (user_id,))
    return headers, user_id


def create_notice(admin, **overrides):
    body = {
        "category": "announcement",
        "title": f"Admin notice {next(_seq)}-{time.time_ns()}",
        "body": "Body.",
        "status": "published",
        "author_name": "Super Admin",
    }
    body.update(overrides)
    return client.post("/admin/notices", json=body, headers=admin)


class GuardTest(unittest.TestCase):
    def setUp(self):
        auth._attempt_log.clear()
        self.admin, self.admin_id = make_superadmin()
        self.farmer, _ = sign_up()

    def _admin_routes(self, admin_headers):
        return [
            client.get("/admin/stats", headers=admin_headers),
            client.get("/admin/users", headers=admin_headers),
            client.get("/admin/notices", headers=admin_headers),
            client.post(
                "/admin/notices",
                json={"title": "x", "category": "update", "status": "draft"},
                headers=admin_headers,
            ),
        ]

    def test_admin_routes_reject_anonymous_calls(self):
        for res in self._admin_routes({}):
            self.assertEqual(res.status_code, 401)

    def test_admin_routes_reject_ordinary_farmers(self):
        for res in self._admin_routes(self.farmer):
            self.assertEqual(res.status_code, 403)
            self.assertEqual(res.json()["detail"], "Superadmin access required")

    def test_superadmin_passes_the_guard(self):
        for res in self._admin_routes(self.admin):
            self.assertIn(res.status_code, (200, 201))


class StatsAndUsersTest(unittest.TestCase):
    def setUp(self):
        auth._attempt_log.clear()
        self.admin, self.admin_id = make_superadmin()

    def test_stats_reports_the_platform_totals(self):
        body = client.get("/admin/stats", headers=self.admin).json()
        for key in ("users", "sessions", "history_items",
                    "notices_published", "notices_draft"):
            self.assertIn(key, body)
            self.assertIsInstance(body[key], int)
        self.assertGreaterEqual(body["users"], 2)

    def test_users_list_includes_roles(self):
        items = client.get("/admin/users", headers=self.admin).json()["items"]
        roles = {u["role"] for u in items}
        self.assertIn("superadmin", roles)
        self.assertLessEqual(roles, {"user", "superadmin"})
        for u in items:
            self.assertIn("contact", u)
            self.assertIn("created_at", u)

    def test_role_change_promotes_and_demotes(self):
        headers, user_id = sign_up()
        res = client.put(
            f"/admin/users/{user_id}/role",
            json={"role": "superadmin"},
            headers=self.admin,
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["role"], "superadmin")
        # The promoted farmer's existing token now passes the admin guard.
        self.assertEqual(client.get("/admin/stats", headers=headers).status_code, 200)

        client.put(
            f"/admin/users/{user_id}/role", json={"role": "user"}, headers=self.admin
        )
        self.assertEqual(client.get("/admin/stats", headers=headers).status_code, 403)

    def test_self_demotion_is_blocked(self):
        res = client.put(
            f"/admin/users/{self.admin_id}/role",
            json={"role": "user"},
            headers=self.admin,
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["detail"], "You cannot demote your own account.")

    def test_unknown_user_and_bad_role_are_rejected(self):
        self.assertEqual(
            client.put("/admin/users/999999/role", json={"role": "user"},
                       headers=self.admin).status_code,
            404,
        )
        self.assertEqual(
            client.put("/admin/users/1/role", json={"role": "wizard"},
                       headers=self.admin).status_code,
            422,
        )


class NoticeCrudTest(unittest.TestCase):
    def setUp(self):
        auth._attempt_log.clear()
        self.admin, self.admin_id = make_superadmin()

    def test_create_returns_the_notice_and_it_goes_public(self):
        res = create_notice(self.admin, title="Model v2 shipped",
                            body="Live now.", category="announcement")
        self.assertEqual(res.status_code, 201)
        created = res.json()
        self.assertEqual(created["title"], "Model v2 shipped")
        self.assertEqual(created["status"], "published")
        self.assertEqual(created["read_count"], 0)

        titles = [i["title"] for i in client.get("/notices").json()["items"]]
        self.assertIn("Model v2 shipped", titles)

    def test_draft_stays_out_of_the_public_list_until_published(self):
        created = create_notice(self.admin, status="draft", title="Secret plan").json()
        public_titles = [i["title"] for i in client.get("/notices").json()["items"]]
        self.assertNotIn("Secret plan", public_titles)

        published = client.put(
            f"/admin/notices/{created['id']}",
            json={"title": "Secret plan", "status": "published",
                  "category": "announcement", "body": ""},
            headers=self.admin,
        )
        self.assertEqual(published.json()["status"], "published")
        public_titles = [i["title"] for i in client.get("/notices").json()["items"]]
        self.assertIn("Secret plan", public_titles)

    def test_admin_list_shows_drafts_and_filters_by_status(self):
        draft_title = f"draft-{time.time_ns()}"
        create_notice(self.admin, status="draft", title=draft_title)
        drafts = client.get(
            "/admin/notices", params={"status": "draft"}, headers=self.admin
        ).json()["items"]
        self.assertIn(draft_title, [i["title"] for i in drafts])
        published = client.get(
            "/admin/notices", params={"status": "published"}, headers=self.admin
        ).json()["items"]
        self.assertNotIn(draft_title, [i["title"] for i in published])

    def test_update_edits_fields_and_bumps_updated_at(self):
        created = create_notice(self.admin, title="Before").json()
        with connect() as conn:
            conn.execute(
                "UPDATE notices SET updated_at = '2020-01-01 00:00:00' WHERE id = ?",
                (created["id"],),
            )
        res = client.put(
            f"/admin/notices/{created['id']}",
            json={"title": "After", "body": "Edited.", "status": "published",
                  "category": "crop_alert", "author_name": "Super Admin"},
            headers=self.admin,
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["title"], "After")
        self.assertEqual(body["category"], "crop_alert")
        self.assertNotEqual(body["updated_at"], "2020-01-01 00:00:00")

    def test_delete_removes_it_everywhere(self):
        created = create_notice(self.admin, title="Doomed").json()
        self.assertEqual(
            client.delete(f"/admin/notices/{created['id']}", headers=self.admin).status_code,
            204,
        )
        self.assertEqual(
            client.delete(f"/admin/notices/{created['id']}", headers=self.admin).status_code,
            404,
        )
        titles = [i["title"] for i in client.get("/notices").json()["items"]]
        self.assertNotIn("Doomed", titles)

    def test_validation_rejects_bad_input(self):
        blank = create_notice(self.admin, title="   ")
        self.assertEqual(blank.status_code, 422)
        bad_category = create_notice(self.admin, category="rumour")
        self.assertEqual(bad_category.status_code, 422)
        bad_status = create_notice(self.admin, status="live")
        self.assertEqual(bad_status.status_code, 422)
        missing = client.post("/admin/notices", json={}, headers=self.admin)
        self.assertEqual(missing.status_code, 422)

    def test_update_and_delete_of_unknown_notice_are_404(self):
        res = client.put(
            "/admin/notices/999999",
            json={"title": "x", "status": "published", "category": "update"},
            headers=self.admin,
        )
        self.assertEqual(res.status_code, 404)
        self.assertEqual(
            client.delete("/admin/notices/999999", headers=self.admin).status_code, 404
        )


if __name__ == "__main__":
    unittest.main()
