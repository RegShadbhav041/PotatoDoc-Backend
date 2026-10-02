"""Support tickets — farmer chat routes plus the superadmin's half."""
import io
import itertools
import time
import unittest

from test_helpers import client

import auth
from db import connect

_seq = itertools.count()


def sign_up(contact=None):
    if contact is None:
        contact = f"tkt{next(_seq)}-{time.time_ns()}@example.com"
    res = client.post(
        "/auth/register",
        json={"contact": contact, "name": "Farmer", "password": "potato1234"},
    )
    return {"Authorization": f"Bearer {res.json()['token']}"}, res.json()["user"]["id"]


def make_superadmin():
    """Promote straight in the DB — the session lookup re-reads the role per request."""
    headers, user_id = sign_up()
    with connect() as conn:
        conn.execute("UPDATE users SET role = 'superadmin' WHERE id = ?", (user_id,))
    return headers, user_id


def open_ticket(headers, subject="Diagnose crashed",
                message="It crashed when I tapped Diagnose."):
    return client.post(
        "/tickets", json={"subject": subject, "message": message}, headers=headers
    )


def png_bytes(width=64, height=64):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (30, 140, 60)).save(buf, "PNG")
    return buf.getvalue()


class FarmerTicketTest(unittest.TestCase):
    def setUp(self):
        auth._attempt_log.clear()
        self.farmer, self.farmer_id = sign_up()

    def test_routes_require_authentication(self):
        self.assertEqual(client.get("/tickets").status_code, 401)
        self.assertEqual(client.post("/tickets", json={}).status_code, 401)
        self.assertEqual(client.get("/tickets/1").status_code, 401)
        self.assertEqual(
            client.post("/tickets/1/messages", json={"body": "x"}).status_code, 401
        )

    def test_opening_a_ticket_returns_the_thread(self):
        res = open_ticket(self.farmer)
        self.assertEqual(res.status_code, 201)
        body = res.json()
        self.assertEqual(body["subject"], "Diagnose crashed")
        self.assertEqual(body["status"], "open")
        self.assertEqual(body["message_count"], 1)
        self.assertEqual(body["last_body"], "It crashed when I tapped Diagnose.")
        msg = body["messages"][0]
        self.assertEqual(msg["from"], "farmer")
        self.assertEqual(msg["author"], "Farmer")
        self.assertIsNotNone(msg["created_at"])

    def test_validation_rejects_blank_and_oversized_input(self):
        self.assertEqual(open_ticket(self.farmer, subject="   ").status_code, 422)
        self.assertEqual(
            open_ticket(self.farmer, message="   ").status_code, 422
        )
        self.assertEqual(
            open_ticket(self.farmer, subject="x" * 201).status_code, 422
        )
        self.assertEqual(
            open_ticket(self.farmer, message="x" * 4001).status_code, 422
        )

    def test_list_shows_only_own_tickets_newest_activity_first(self):
        other, _ = sign_up()
        open_ticket(self.farmer, subject="First")
        open_ticket(self.farmer, subject="Second")
        open_ticket(other, subject="Not mine")

        mine = client.get("/tickets", headers=self.farmer).json()["items"]
        self.assertEqual(len(mine), 2)
        self.assertEqual([t["subject"] for t in mine], ["Second", "First"])
        for t in mine:
            self.assertEqual(t["message_count"], 1)

    def test_farmers_cannot_read_or_reply_to_each_others_tickets(self):
        other, _ = sign_up()
        ticket = open_ticket(self.farmer).json()
        self.assertEqual(
            client.get(f"/tickets/{ticket['id']}", headers=other).status_code, 404
        )
        self.assertEqual(
            client.post(
                f"/tickets/{ticket['id']}/messages",
                json={"body": "sneaky"},
                headers=other,
            ).status_code,
            404,
        )

    def test_detail_of_a_missing_ticket_is_404(self):
        self.assertEqual(client.get("/tickets/999999", headers=self.farmer).status_code, 404)

    def test_reply_appends_to_the_thread_and_bumps_the_summary(self):
        ticket = open_ticket(self.farmer).json()
        res = client.post(
            f"/tickets/{ticket['id']}/messages",
            json={"body": "It happens offline too."},
            headers=self.farmer,
        )
        self.assertEqual(res.status_code, 201)
        body = res.json()
        self.assertEqual(body["message_count"], 2)
        self.assertEqual(body["last_body"], "It happens offline too.")
        self.assertEqual([m["from"] for m in body["messages"]], ["farmer", "farmer"])
        self.assertEqual(
            client.post(
                f"/tickets/{ticket['id']}/messages",
                json={"body": "   "},
                headers=self.farmer,
            ).status_code,
            422,
        )


class AdminTicketTest(unittest.TestCase):
    def setUp(self):
        auth._attempt_log.clear()
        self.admin, self.admin_id = make_superadmin()
        self.farmer, self.farmer_id = sign_up()
        self.ticket = open_ticket(self.farmer).json()
        self.tid = self.ticket["id"]

    def test_admin_routes_reject_anonymous_calls(self):
        self.assertEqual(client.get("/admin/tickets").status_code, 401)
        self.assertEqual(client.get(f"/admin/tickets/{self.tid}").status_code, 401)
        self.assertEqual(
            client.post(f"/admin/tickets/{self.tid}/messages", json={"body": "x"}).status_code,
            401,
        )
        self.assertEqual(
            client.put(f"/admin/tickets/{self.tid}", json={"status": "open"}).status_code,
            401,
        )

    def test_admin_routes_reject_ordinary_farmers(self):
        for res in (
            client.get("/admin/tickets", headers=self.farmer),
            client.get(f"/admin/tickets/{self.tid}", headers=self.farmer),
            client.post(
                f"/admin/tickets/{self.tid}/messages",
                json={"body": "x"},
                headers=self.farmer,
            ),
            client.put(
                f"/admin/tickets/{self.tid}", json={"status": "open"}, headers=self.farmer
            ),
        ):
            self.assertEqual(res.status_code, 403)
            self.assertEqual(res.json()["detail"], "Superadmin access required")

    def test_admin_list_carries_the_farmer_identity_and_thread_counts(self):
        items = client.get("/admin/tickets", headers=self.admin).json()["items"]
        mine = next(t for t in items if t["id"] == self.tid)
        self.assertEqual(mine["subject"], "Diagnose crashed")
        self.assertEqual(mine["status"], "open")
        self.assertEqual(mine["message_count"], 1)
        self.assertEqual(mine["last_body"], "It crashed when I tapped Diagnose.")
        self.assertEqual(mine["farmer"]["id"], self.farmer_id)
        self.assertIn("@example.com", mine["farmer"]["contact"])

        open_only = client.get(
            "/admin/tickets", params={"status": "open"}, headers=self.admin
        ).json()["items"]
        self.assertTrue(any(t["id"] == self.tid for t in open_only))
        resolved = client.get(
            "/admin/tickets", params={"status": "resolved"}, headers=self.admin
        ).json()["items"]
        self.assertFalse(any(t["id"] == self.tid for t in resolved))
        self.assertEqual(
            client.get(
                "/admin/tickets", params={"status": "bogus"}, headers=self.admin
            ).status_code,
            422,
        )

    def test_admin_reply_reaches_the_farmer_marked_as_admin(self):
        res = client.post(
            f"/admin/tickets/{self.tid}/messages",
            json={"body": "We shipped a fix — please retry."},
            headers=self.admin,
        )
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.json()["message_count"], 2)

        thread = client.get(f"/tickets/{self.tid}", headers=self.farmer).json()
        self.assertEqual(thread["messages"][-1]["from"], "admin")
        self.assertEqual(
            thread["messages"][-1]["body"], "We shipped a fix — please retry."
        )
        self.assertEqual(
            client.post(
                f"/admin/tickets/{self.tid}/messages",
                json={"body": "   "},
                headers=self.admin,
            ).status_code,
            422,
        )

    def test_resolve_and_farmer_reply_reopens(self):
        done = client.put(
            f"/admin/tickets/{self.tid}", json={"status": "resolved"}, headers=self.admin
        )
        self.assertEqual(done.status_code, 200)
        self.assertEqual(done.json()["status"], "resolved")

        # Farmed side sees the resolved state…
        self.assertEqual(
            client.get(f"/tickets/{self.tid}", headers=self.farmer).json()["status"],
            "resolved",
        )
        # …and a farmer reply automatically reopens the ticket.
        reopened = client.post(
            f"/tickets/{self.tid}/messages",
            json={"body": "Still broken for me."},
            headers=self.farmer,
        )
        self.assertEqual(reopened.status_code, 201)
        self.assertEqual(reopened.json()["status"], "open")

    def test_status_validation_and_missing_tickets(self):
        self.assertEqual(
            client.put(
                f"/admin/tickets/{self.tid}", json={"status": "bogus"}, headers=self.admin
            ).status_code,
            422,
        )
        self.assertEqual(
            client.get("/admin/tickets/999999", headers=self.admin).status_code, 404
        )
        self.assertEqual(
            client.put(
                "/admin/tickets/999999", json={"status": "open"}, headers=self.admin
            ).status_code,
            404,
        )
        self.assertEqual(
            client.post(
                "/admin/tickets/999999/messages",
                json={"body": "x"},
                headers=self.admin,
            ).status_code,
            404,
        )

    def test_admin_detail_carries_the_farmers_profile_photo(self):
        self.assertIsNone(
            client.get(f"/admin/tickets/{self.tid}", headers=self.admin).json()["farmer"]["photo"]
        )
        up = client.post(
            "/auth/me/photo",
            files={"file": ("me.png", png_bytes(), "image/png")},
            headers=self.farmer,
        )
        self.assertEqual(up.status_code, 200)
        detail = client.get(f"/admin/tickets/{self.tid}", headers=self.admin).json()
        self.assertTrue(detail["farmer"]["photo"].startswith("data:image/jpeg;base64,"))
        self.assertEqual(detail["farmer"]["id"], self.farmer_id)


if __name__ == "__main__":
    unittest.main()
