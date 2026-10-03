import time
import unittest

from test_helpers import client

import auth


def sign_up(contact):
    res = client.post(
        "/auth/register",
        json={"contact": contact, "name": "Farmer", "password": "potato1234"},
    )
    return {"Authorization": f"Bearer {res.json()['token']}"}


def entry(i, **extra):
    return {
        "id": str(1700000000000 + i),
        "class": "Healthy",
        "confidence": 0.9,
        "model": "Ensemble (All Models)",
        "timestamp": "10/1/2026, 3:00 PM",
        **extra,
    }


class HistoryTest(unittest.TestCase):
    def setUp(self):
        auth._attempt_log.clear()

    def test_history_requires_authentication(self):
        self.assertEqual(client.get("/history").status_code, 401)
        self.assertEqual(client.put("/history", json={"items": []}).status_code, 401)
        self.assertEqual(client.delete("/history").status_code, 401)

    def test_put_then_get_roundtrip(self):
        h = sign_up("h1@example.com")
        res = client.put("/history", json={"items": [entry(1), entry(2)]}, headers=h)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"upserted": 2})
        items = client.get("/history", headers=h).json()["items"]
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["class"], "Healthy")

    def test_put_is_idempotent_for_the_same_id(self):
        h = sign_up("h2@example.com")
        client.put("/history", json={"items": [entry(1)]}, headers=h)
        client.put("/history", json={"items": [entry(1, confidence=0.5)]}, headers=h)
        items = client.get("/history", headers=h).json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["confidence"], 0.5)

    def test_put_strips_the_device_local_image_uri(self):
        h = sign_up("h3@example.com")
        client.put(
            "/history",
            json={"items": [entry(1, imageUri="file:///var/mobile/leaf.jpg")]},
            headers=h,
        )
        item = client.get("/history", headers=h).json()["items"][0]
        self.assertNotIn("imageUri", item)
        self.assertEqual(item["class"], "Healthy")

    def test_empty_push_never_deletes_existing_items(self):
        h = sign_up("h4@example.com")
        client.put("/history", json={"items": [entry(1), entry(2)]}, headers=h)
        res = client.put("/history", json={"items": []}, headers=h)
        self.assertEqual(res.json(), {"upserted": 0})
        self.assertEqual(len(client.get("/history", headers=h).json()["items"]), 2)

    def test_delete_clears_the_copy(self):
        h = sign_up("h5@example.com")
        client.put("/history", json={"items": [entry(1)]}, headers=h)
        self.assertEqual(client.delete("/history", headers=h).status_code, 204)
        self.assertEqual(client.get("/history", headers=h).json()["items"], [])

    def test_items_come_back_newest_first(self):
        h = sign_up("h6@example.com")
        client.put("/history", json={"items": [entry(1), entry(3), entry(2)]}, headers=h)
        ids = [i["id"] for i in client.get("/history", headers=h).json()["items"]]
        self.assertEqual(
            ids,
            [str(1700000000003), str(1700000000002), str(1700000000001)],
        )

    def test_put_is_capped_at_fifty_items(self):
        h = sign_up("h7@example.com")
        res = client.put("/history", json={"items": [entry(i) for i in range(60)]}, headers=h)
        self.assertEqual(res.json(), {"upserted": 50})
        self.assertEqual(len(client.get("/history", headers=h).json()["items"]), 50)

    def test_farmers_cannot_read_each_others_history(self):
        a = sign_up("a1@example.com")
        b = sign_up("b1@example.com")
        client.put("/history", json={"items": [entry(1)]}, headers=a)
        self.assertEqual(client.get("/history", headers=b).json()["items"], [])


def png_bytes():
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (30, 140, 60)).save(buf, "PNG")
    return buf.getvalue()


class HistoryPhotoTest(unittest.TestCase):
    """Scan photos: uploaded once by the app, reviewed by the superadmin."""

    def setUp(self):
        auth._attempt_log.clear()
        self.farmer = sign_up(f"hp-{time.time_ns()}@example.com")
        client.put("/history", json={"items": [entry(1)]}, headers=self.farmer)
        self.item_id = str(1700000000001)
        self.file = {"file": ("leaf.png", png_bytes(), "image/png")}

    def test_upload_flags_the_item_and_serves_jpeg_bytes(self):
        res = client.post(
            f"/history/{self.item_id}/photo", files=self.file, headers=self.farmer
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["item_id"], self.item_id)

        items = client.get("/history", headers=self.farmer).json()["items"]
        self.assertTrue(items[0]["has_photo"])

        photo = client.get(f"/history/{self.item_id}/photo", headers=self.farmer)
        self.assertEqual(photo.status_code, 200)
        self.assertEqual(photo.headers["content-type"], "image/jpeg")
        self.assertTrue(photo.content.startswith(b"\xff\xd8"))

    def test_upload_is_idempotent_and_never_stored_unflagged(self):
        for _ in range(2):
            r = client.post(
                f"/history/{self.item_id}/photo", files=self.file, headers=self.farmer
            )
            self.assertEqual(r.status_code, 200)
        self.assertTrue(
            client.get("/history", headers=self.farmer).json()["items"][0]["has_photo"]
        )

    def test_has_photo_is_server_derived_and_never_persisted(self):
        # A client that pushes the flag back cannot fake it.
        item = dict(entry(1), has_photo=True)
        client.put("/history", json={"items": [item]}, headers=self.farmer)
        self.assertFalse(
            client.get("/history", headers=self.farmer).json()["items"][0]["has_photo"]
        )

    def test_upload_requires_an_existing_item(self):
        res = client.post(
            "/history/no-such-id/photo", files=self.file, headers=self.farmer
        )
        self.assertEqual(res.status_code, 404)

    def test_non_image_uploads_are_rejected(self):
        res = client.post(
            f"/history/{self.item_id}/photo",
            files={"file": ("x.txt", b"not an image", "text/plain")},
            headers=self.farmer,
        )
        self.assertEqual(res.status_code, 400)

    def test_farmers_cannot_read_each_others_photos(self):
        client.post(
            f"/history/{self.item_id}/photo", files=self.file, headers=self.farmer
        )
        other = sign_up(f"hp2-{time.time_ns()}@example.com")
        self.assertEqual(
            client.get(f"/history/{self.item_id}/photo", headers=other).status_code,
            404,
        )
        self.assertEqual(
            client.get(f"/history/{self.item_id}/photo").status_code, 401
        )

    def test_photo_requires_authentication(self):
        self.assertEqual(
            client.post(f"/history/{self.item_id}/photo", files=self.file).status_code,
            401,
        )

    def test_clearing_history_removes_the_photos_too(self):
        client.post(f"/history/{self.item_id}/photo", files=self.file, headers=self.farmer)
        self.assertEqual(client.delete("/history", headers=self.farmer).status_code, 204)
        self.assertEqual(
            client.get(f"/history/{self.item_id}/photo", headers=self.farmer).status_code,
            404,
        )


if __name__ == "__main__":
    unittest.main()
