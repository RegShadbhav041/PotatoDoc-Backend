import itertools
import time
import unittest

from test_helpers import client, app

import auth

_seq = itertools.count()


def register(contact=None, name="Ram", password="potato1234"):
    """Unique contact by default so tests never collide in the shared DB."""
    if contact is None:
        contact = f"farmer{next(_seq)}-{time.time_ns()}@example.com"
    return client.post(
        "/auth/register",
        json={"contact": contact, "name": name, "password": password},
    )


def login(contact, password):
    return client.post("/auth/login", json={"contact": contact, "password": password})


class RegisterLoginTest(unittest.TestCase):
    def setUp(self):
        auth._attempt_log.clear()

    def tearDown(self):
        auth._attempt_log.clear()

    def test_register_returns_token_and_user(self):
        res = register()
        self.assertEqual(res.status_code, 201)
        body = res.json()
        self.assertTrue(len(body["token"]) > 20)
        self.assertIn("@example.com", body["user"]["contact"])
        self.assertEqual(body["user"]["name"], "Ram")
        self.assertIn("id", body["user"])

    def test_register_normalizes_contact(self):
        register("  Farmer@Example.COM ")
        dup = register("farmer@example.com")
        self.assertEqual(dup.status_code, 409)
        self.assertEqual(dup.json()["detail"], "That contact is already registered.")

    def test_register_rejects_short_password(self):
        res = register(password="short")
        self.assertEqual(res.status_code, 422)
        self.assertEqual(res.json()["detail"], "Password must be at least 8 characters.")

    def test_register_rejects_blank_name(self):
        res = register(name="   ")
        self.assertEqual(res.status_code, 422)
        self.assertEqual(res.json()["detail"], "Name is required.")

    def test_register_rejects_blank_contact(self):
        res = register(contact="   ")
        self.assertEqual(res.status_code, 422)
        self.assertEqual(res.json()["detail"], "Email or phone is required.")

    def test_login_with_wrong_password_is_generic(self):
        register("a@example.com")
        res = login("a@example.com", "wrong-pass")
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.json()["detail"], "Invalid credentials")

    def test_unknown_contact_matches_wrong_password_detail(self):
        register("b@example.com")
        wrong = login("b@example.com", "wrong-pass")
        unknown = login("nobody@example.com", "wrong-pass")
        self.assertEqual(wrong.json()["detail"], unknown.json()["detail"])

    def test_login_is_case_insensitive(self):
        register("Case@Test.com")
        res = login("  case@test.COM ", "potato1234")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["user"]["contact"], "case@test.com")

    def test_login_rejects_blank_fields(self):
        res = login("", "")
        self.assertEqual(res.status_code, 422)


class SessionTest(unittest.TestCase):
    def setUp(self):
        auth._attempt_log.clear()

    def tearDown(self):
        auth._attempt_log.clear()

    def test_me_requires_a_bearer_token(self):
        self.assertEqual(client.get("/auth/me").status_code, 401)
        self.assertEqual(
            client.get("/auth/me", headers={"Authorization": "Basic abc"}).status_code, 401
        )
        self.assertEqual(
            client.get("/auth/me", headers={"Authorization": "Bearer made-up"}).status_code, 401
        )
        self.assertEqual(client.get("/auth/me").json()["detail"], "Not authenticated")

    def test_me_returns_the_signed_in_farmer(self):
        token = register("me@example.com").json()["token"]
        res = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["contact"], "me@example.com")
        self.assertEqual(body["name"], "Ram")
        self.assertIn("id", body)

    def test_logout_revokes_the_token(self):
        token = register("out@example.com").json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        self.assertEqual(client.get("/auth/me", headers=headers).status_code, 200)
        self.assertEqual(client.post("/auth/logout", headers=headers).status_code, 204)
        self.assertEqual(client.get("/auth/me", headers=headers).status_code, 401)

    def test_logout_is_idempotent(self):
        self.assertEqual(client.post("/auth/logout").status_code, 204)
        self.assertEqual(
            client.post("/auth/logout", headers={"Authorization": "Bearer gone"}).status_code, 204
        )

    def test_login_rate_limit_trips_after_ten_failures(self):
        register("throttle@example.com")
        for _ in range(10):
            login("throttle@example.com", "wrong-pass")
        res = login("throttle@example.com", "wrong-pass")
        self.assertEqual(res.status_code, 429)
        self.assertEqual(res.json()["detail"], "Too many attempts. Try again shortly.")

    def test_rate_limit_is_per_contact(self):
        register("one@example.com")
        register("two@example.com")
        for _ in range(10):
            login("one@example.com", "x")
        self.assertNotEqual(login("two@example.com", "x").status_code, 429)


class RoleTest(unittest.TestCase):
    def setUp(self):
        auth._attempt_log.clear()

    def _with_superadmin_env(self, contact, password, fn):
        import os

        os.environ["POTATO_SUPERADMIN_CONTACT"] = contact
        os.environ["POTATO_SUPERADMIN_PASSWORD"] = password
        try:
            fn()
        finally:
            os.environ.pop("POTATO_SUPERADMIN_CONTACT", None)
            os.environ.pop("POTATO_SUPERADMIN_PASSWORD", None)

    def test_register_and_login_surface_the_default_role(self):
        contact = f"role{next(_seq)}-{time.time_ns()}@example.com"
        body = register(contact).json()
        self.assertEqual(body["user"]["role"], "user")
        self.assertEqual(login(contact, "potato1234").json()["user"]["role"], "user")

    def test_me_includes_the_role(self):
        token = register("role-me@example.com").json()["token"]
        res = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(res.json()["role"], "user")

    def test_env_seed_creates_a_superadmin_and_it_can_log_in(self):
        import db

        contact = f"seed{time.time_ns()}@example.com"
        self._with_superadmin_env(
            contact, "secretpass123", lambda: db._seed_superadmin()
        )
        res = login(contact, "secretpass123")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["user"]["role"], "superadmin")

    def test_env_seed_promotes_an_existing_account(self):
        import db

        contact = f"promote{time.time_ns()}@example.com"
        register(contact, name="Existing")
        self._with_superadmin_env(
            contact, "secretpass123", lambda: db._seed_superadmin()
        )
        # Env pair is authoritative: the promoted account now uses the env
        # password, but its display name is untouched.
        res = login(contact, "secretpass123")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["user"]["role"], "superadmin")
        self.assertEqual(res.json()["user"]["name"], "Existing")
        self.assertEqual(login(contact, "potato1234").status_code, 401)


class UpdateProfileTest(unittest.TestCase):
    def test_update_me_changes_name_and_contact(self):
        body = register().json()
        token = body["token"]
        new_contact = f"renamed{time.time_ns()}@example.com"
        res = client.put(
            "/auth/me",
            json={"name": "Salina Kunwar", "contact": new_contact},
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["name"], "Salina Kunwar")
        self.assertEqual(res.json()["contact"], new_contact)

        # The new contact signs in, the old one no longer does.
        self.assertEqual(login(new_contact, "potato1234").status_code, 200)
        self.assertEqual(login(body["user"]["contact"], "potato1234").status_code, 401)

    def test_update_me_requires_auth(self):
        res = client.put("/auth/me", json={"name": "X", "contact": "x@example.com"})
        self.assertEqual(res.status_code, 401)

    def test_update_me_rejects_a_taken_contact(self):
        taken = register().json()["user"]["contact"]
        token = register().json()["token"]
        res = client.put(
            "/auth/me",
            json={"name": "Ram", "contact": taken},
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(res.status_code, 409)

    def test_update_me_rejects_empty_fields(self):
        token = register().json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        self.assertEqual(
            client.put("/auth/me", json={"name": "", "contact": "a@example.com"}, headers=headers).status_code,
            422,
        )
        self.assertEqual(
            client.put("/auth/me", json={"name": "Ram", "contact": "  "}, headers=headers).status_code,
            422,
        )


def tiny_png(width=300, height=200):
    import io as _io
    from PIL import Image
    buf = _io.BytesIO()
    Image.new("RGB", (width, height), (10, 120, 30)).save(buf, "PNG")
    return buf.getvalue()


class PhotoTest(unittest.TestCase):
    def auth_headers(self, res=None):
        body = (res or register()).json()
        return {"Authorization": f"Bearer {body['token']}"}, body["user"]["contact"]

    def upload(self, headers, payload=None, name="photo.png", ctype="image/png"):
        return client.post(
            "/auth/me/photo",
            files={"file": (name, payload if payload is not None else tiny_png(), ctype)},
            headers=headers,
        )

    def test_fresh_register_has_no_photo(self):
        self.assertIsNone(register().json()["user"]["photo"])

    def test_upload_returns_a_data_uri_and_me_serves_it_back(self):
        headers, _ = self.auth_headers()
        res = self.upload(headers)
        self.assertEqual(res.status_code, 200)
        photo = res.json()["photo"]
        self.assertTrue(photo.startswith("data:image/jpeg;base64,"))
        self.assertEqual(client.get("/auth/me", headers=headers).json()["photo"], photo)

    def test_a_fresh_login_includes_the_photo(self):
        headers, contact = self.auth_headers()
        self.upload(headers)
        login_body = login(contact, "potato1234").json()
        self.assertTrue(login_body["user"]["photo"].startswith("data:image/jpeg;base64,"))

    def test_update_me_keeps_the_photo_in_its_response(self):
        headers, _ = self.auth_headers()
        self.upload(headers)
        res = client.put(
            "/auth/me",
            json={"name": "Renamed Farmer", "contact": f"p{time.time_ns()}@example.com"},
            headers=headers,
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["photo"].startswith("data:image/jpeg;base64,"))

    def test_upload_replaces_the_previous_picture(self):
        import base64
        import io as _io
        from PIL import Image
        headers, _ = self.auth_headers()
        self.upload(headers, payload=tiny_png(300, 200))
        self.upload(headers, payload=tiny_png(64, 64))
        photo = client.get("/auth/me", headers=headers).json()["photo"]
        raw = base64.b64decode(photo.split(",", 1)[1])
        self.assertEqual(Image.open(_io.BytesIO(raw)).size, (64, 64))

    def test_delete_clears_the_photo(self):
        headers, _ = self.auth_headers()
        self.upload(headers)
        res = client.delete("/auth/me/photo", headers=headers)
        self.assertEqual(res.status_code, 200)
        self.assertIsNone(res.json()["photo"])
        self.assertIsNone(client.get("/auth/me", headers=headers).json()["photo"])

    def test_upload_requires_auth(self):
        res = self.upload({"Authorization": ""})
        self.assertEqual(res.status_code, 401)

    def test_delete_requires_auth(self):
        self.assertEqual(client.delete("/auth/me/photo").status_code, 401)

    def test_non_image_payloads_are_rejected(self):
        headers, _ = self.auth_headers()
        res = self.upload(headers, payload=b"hello", name="x.txt", ctype="text/plain")
        self.assertEqual(res.status_code, 400)
        self.assertIn("detail", res.json())

    def test_payloads_over_five_megabytes_are_rejected(self):
        headers, _ = self.auth_headers()
        res = self.upload(
            headers,
            payload=b"x" * (5 * 1024 * 1024 + 1),
            name="big.jpg",
            ctype="image/jpeg",
        )
        self.assertEqual(res.status_code, 422)


class OpenApiSecurityTest(unittest.TestCase):
    """The OpenAPI document must declare the bearer scheme so Swagger UI
    shows the Authorize button on protected routes (and only those)."""

    def test_openapi_declares_bearer_scheme(self):
        schema = app.openapi()
        schemes = schema.get("components", {}).get("securitySchemes", {})
        self.assertIn("Bearer", schemes)
        self.assertEqual(schemes["Bearer"]["type"], "http")
        self.assertEqual(schemes["Bearer"]["scheme"], "bearer")

    def test_protected_routes_are_locked_and_public_ones_are_not(self):
        schema = app.openapi()
        me = schema["paths"]["/auth/me"]["get"]
        self.assertEqual(me.get("security"), [{"Bearer": []}])
        history = schema["paths"]["/history"]["get"]
        self.assertEqual(history.get("security"), [{"Bearer": []}])
        login = schema["paths"]["/auth/login"]["post"]
        self.assertNotIn("security", login)
        analyze = schema["paths"]["/location/analyze"]["get"]
        self.assertNotIn("security", analyze)


if __name__ == "__main__":
    unittest.main()
