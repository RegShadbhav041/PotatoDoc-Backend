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


def _png(width=64, height=64):
    import io as _io
    from PIL import Image
    buf = _io.BytesIO()
    Image.new("RGB", (width, height), (30, 140, 60)).save(buf, "PNG")
    return buf.getvalue()


class GuardTest(unittest.TestCase):
    def setUp(self):
        auth._attempt_log.clear()
        self.admin, self.admin_id = make_superadmin()
        self.farmer, self.farmer_id = sign_up()

    def _admin_routes(self, admin_headers):
        return [
            client.get("/admin/stats", headers=admin_headers),
            client.get("/admin/overview", headers=admin_headers),
            client.get("/admin/models", headers=admin_headers),
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

    def test_user_detail_and_history_are_guarded(self):
        detail = f"/admin/users/{self.farmer_id}"
        for res in (client.get(detail), client.get(f"{detail}/history")):
            self.assertEqual(res.status_code, 401)
        for res in (
            client.get(detail, headers=self.farmer),
            client.get(f"{detail}/history", headers=self.farmer),
        ):
            self.assertEqual(res.status_code, 403)


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


class WeightsDirDefaultTest(unittest.TestCase):
    def test_default_weights_dir_is_outputs_combined(self):
        import os

        from admin import _weights_dir

        old = os.environ.pop("POTATO_WEIGHTS_DIR", None)
        try:
            self.assertEqual(_weights_dir().name, "outputs_combined")
        finally:
            if old is not None:
                os.environ["POTATO_WEIGHTS_DIR"] = old


class ReportingTest(unittest.TestCase):
    """Dashboard, farmer profiles, per-farmer history and model inventory."""

    def setUp(self):
        auth._attempt_log.clear()
        self.admin, self.admin_id = make_superadmin()
        self.farmer_headers, self.farmer_id = sign_up()

    def _push(self, items):
        return client.put(
            "/history", json={"items": items}, headers=self.farmer_headers
        )

    def test_overview_reports_totals_trends_and_mix(self):
        self._push([
            {"id": "1", "class": "Healthy", "confidence": 0.91,
             "model": "Ensemble (All Models)"},
            {"id": "2", "class": "Early Blight", "confidence": 0.72,
             "model": "MobileNetV2 (transfer)"},
        ])
        body = client.get("/admin/overview", headers=self.admin).json()
        for key in ("totals", "trends", "class_distribution", "model_usage",
                    "recent_users", "recent_diagnoses"):
            self.assertIn(key, body)
        self.assertGreaterEqual(body["totals"]["users"], 2)
        self.assertGreaterEqual(body["totals"]["history_items"], 2)
        self.assertEqual(body["class_distribution"]["Healthy"], 1)
        self.assertEqual(body["class_distribution"]["Early Blight"], 1)
        self.assertIn("Ensemble (All Models)", body["model_usage"])
        for key in ("new_users_7d", "new_diagnoses_7d", "sessions_24h",
                    "diagnoses_24h"):
            self.assertIsInstance(body["trends"][key], int)
        self.assertTrue(body["recent_users"])
        self.assertEqual(body["recent_diagnoses"][0]["user"], "Farmer")
        self.assertEqual(body["recent_diagnoses"][0]["class"], "Early Blight")

    def test_user_detail_is_a_full_profile(self):
        self._push([{"id": "10", "class": "Healthy", "confidence": 0.8,
                     "model": "Ensemble (All Models)"}])
        body = client.get(f"/admin/users/{self.farmer_id}", headers=self.admin).json()
        self.assertEqual(body["id"], self.farmer_id)
        self.assertEqual(body["role"], "user")
        self.assertIn("@example.com", body["contact"])
        self.assertEqual(body["history_count"], 1)
        self.assertGreaterEqual(body["session_count"], 1)
        self.assertIsInstance(body["notices_read"], int)
        self.assertIsNotNone(body["last_diagnosis_at"])
        self.assertEqual(body["class_distribution"], {"Healthy": 1})

        missing = client.get("/admin/users/999999", headers=self.admin)
        self.assertEqual(missing.status_code, 404)

    def test_user_history_returns_parsed_display_fields(self):
        self._push([
            {"id": "11", "class": "Late Blight", "confidence": 0.63,
             "model": "Ensemble (All Models)", "timestamp": "10/1/2026, 1:04 PM",
             "probabilities": {"Late Blight": 0.63, "Healthy": 0.37}},
        ])
        body = client.get(
            f"/admin/users/{self.farmer_id}/history", headers=self.admin
        ).json()
        self.assertEqual(body["count"], 1)
        item = body["items"][0]
        self.assertEqual(item["class"], "Late Blight")
        self.assertEqual(item["confidence"], 0.63)
        self.assertEqual(item["model"], "Ensemble (All Models)")
        self.assertEqual(item["timestamp"], "10/1/2026, 1:04 PM")
        self.assertIsNotNone(item["updated_at"])
        # Bulk payload fields are not shipped to the panel.
        self.assertNotIn("probabilities", item)

        self.assertEqual(
            client.get(
                f"/admin/users/{self.farmer_id}/history", params={"limit": 0},
                headers=self.admin,
            ).json()["count"],
            1,
        )
        self.assertEqual(
            client.get("/admin/users/999999/history", headers=self.admin).status_code,
            404,
        )

    def test_models_inventory_describes_the_deployed_weights(self):
        body = client.get("/admin/models", headers=self.admin).json()
        self.assertEqual(body["default"], "ensemble")
        self.assertTrue(body["classes"])
        self.assertIn("Healthy", body["classes"])
        self.assertIn("epochs", body["train_config"])
        self.assertIn("members", body["ensemble"])
        self.assertIn("entropy_max", body["thresholds"])

        ids = [m["id"] for m in body["items"]]
        self.assertEqual(ids, ["ensemble", "small_cnn", "mobilenetv2", "efficientnetb0"])
        for model in body["items"]:
            self.assertIn("name", model)
            self.assertIn("available", model)
            self.assertIsInstance(model["size_mb"], float)
            self.assertIn("accuracy", model)
        # Ensemble is virtual: available only when every member weight exists.
        members = [m for m in body["items"] if m["id"] != "ensemble"]
        ensemble = next(m for m in body["items"] if m["id"] == "ensemble")
        self.assertEqual(
            ensemble["available"], all(m["available"] for m in members)
        )


class WeightsDirDefaultTest(unittest.TestCase):
    def test_default_weights_dir_is_outputs_combined(self):
        import os
        from admin import _weights_dir

        old = os.environ.pop("POTATO_WEIGHTS_DIR", None)
        try:
            self.assertEqual(_weights_dir().name, "outputs_combined")
        finally:
            if old is not None:
                os.environ["POTATO_WEIGHTS_DIR"] = old


class NoticeImageTest(unittest.TestCase):
    def setUp(self):
        auth._attempt_log.clear()
        self.admin, self.admin_id = make_superadmin()
        self.notice = create_notice(self.admin, title="With pictures").json()

    def upload(self, notice_id=None, payload=None):
        return client.post(
            f"/admin/notices/{notice_id or self.notice['id']}/images",
            files={"file": ("pic.png", payload or _png(), "image/png")},
            headers=self.admin,
        )

    def test_upload_counts_the_image_in_admin_and_public_lists(self):
        res = self.upload()
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.json()["image_count"], 1)
        admin_items = client.get("/admin/notices", headers=self.admin).json()["items"]
        self.assertEqual(admin_items[0]["image_count"], 1)
        public_items = client.get("/notices").json()["items"]
        self.assertEqual(public_items[0]["image_count"], 1)

    def test_uploaded_image_is_served_publicly_as_a_resized_jpeg(self):
        self.upload()
        res = client.get(f"/notices/{self.notice['id']}/images/0")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers["content-type"], "image/jpeg")
        import io as _io
        from PIL import Image
        self.assertEqual(Image.open(_io.BytesIO(res.content)).size, (64, 64))

    def test_six_images_are_allowed_and_the_seventh_is_rejected(self):
        for _ in range(6):
            self.assertEqual(self.upload().status_code, 201)
        res = self.upload()
        self.assertEqual(res.status_code, 422)
        self.assertIn("at most 6", res.json()["detail"])

    def test_unknown_notice_is_404(self):
        self.assertEqual(self.upload(notice_id=999999).status_code, 404)

    def test_admin_route_serves_draft_images_to_the_superadmin(self):
        draft = create_notice(self.admin, status="draft", title="Unreleased").json()
        self.upload(notice_id=draft["id"])
        # the public route hides drafts ...
        self.assertEqual(client.get(f"/notices/{draft['id']}/images/0").status_code, 404)
        # ... but the panel (same-origin <img> with the token) can still preview them
        res = client.get(f"/admin/notices/{draft['id']}/images/0", headers=self.admin)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers["content-type"], "image/jpeg")
        self.assertEqual(
            client.get(f"/admin/notices/{draft['id']}/images/0").status_code, 401
        )

    def test_anonymous_and_ordinary_farmers_are_rejected(self):
        files = {"file": ("pic.png", _png(), "image/png")}
        self.assertEqual(
            client.post(f"/admin/notices/{self.notice['id']}/images", files=files).status_code,
            401,
        )
        farmer, _ = sign_up()
        self.assertEqual(
            client.post(
                f"/admin/notices/{self.notice['id']}/images", files=files, headers=farmer
            ).status_code,
            403,
        )

    def test_delete_by_index_removes_the_right_image(self):
        for _ in range(3):
            self.upload()
        first = client.get(f"/notices/{self.notice['id']}/images/0").content
        res = client.delete(
            f"/admin/notices/{self.notice['id']}/images/1", headers=self.admin
        )
        self.assertEqual(res.status_code, 204)
        self.assertEqual(client.get(f"/notices/{self.notice['id']}/images/0").content, first)
        self.assertEqual(client.get(f"/notices/{self.notice['id']}/images/2").status_code, 404)
        self.assertEqual(
            client.delete(
                f"/admin/notices/{self.notice['id']}/images/5", headers=self.admin
            ).status_code,
            404,
        )

    def test_deleting_the_notice_removes_its_images(self):
        self.upload()
        notice_id = self.notice["id"]
        self.assertEqual(
            client.delete(f"/admin/notices/{notice_id}", headers=self.admin).status_code, 204
        )
        self.assertEqual(client.get(f"/notices/{notice_id}/images/0").status_code, 404)

    def test_new_categories_are_accepted_by_the_crud(self):
        for category in ("new_product", "medicine"):
            res = create_notice(self.admin, category=category, title=f"Cat {category}")
            self.assertEqual(res.status_code, 201, res.text)
            self.assertEqual(res.json()["category"], category)


if __name__ == "__main__":
    unittest.main()
