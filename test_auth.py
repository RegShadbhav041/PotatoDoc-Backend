import itertools
import time
import unittest

from test_helpers import client

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


if __name__ == "__main__":
    unittest.main()
