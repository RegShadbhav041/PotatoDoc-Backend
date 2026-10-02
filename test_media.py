"""media.py — upload validation, resizing and data-URI encoding."""
import io
import unittest

from fastapi import HTTPException
from PIL import Image

from media import MAX_UPLOAD_BYTES, normalise, to_data_uri


def png_bytes(width=100, height=60):
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (12, 200, 40)).save(buf, "PNG")
    return buf.getvalue()


class NormaliseTest(unittest.TestCase):
    def test_resizes_to_the_longest_edge_and_reencodes_to_jpeg(self):
        data, mime = normalise(png_bytes(1000, 500), "image/png", max_edge=512, quality=82)
        self.assertEqual(mime, "image/jpeg")
        img = Image.open(io.BytesIO(data))
        self.assertEqual(img.format, "JPEG")
        self.assertEqual(img.size, (512, 256))

    def test_small_images_keep_their_dimensions(self):
        data, _ = normalise(png_bytes(64, 64), "image/png", max_edge=512, quality=82)
        self.assertEqual(Image.open(io.BytesIO(data)).size, (64, 64))

    def test_an_empty_payload_is_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            normalise(b"", "image/png", max_edge=512, quality=82)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_oversized_payload_is_rejected_before_decoding(self):
        with self.assertRaises(HTTPException) as ctx:
            normalise(b"x" * (MAX_UPLOAD_BYTES + 1), "image/jpeg", max_edge=512, quality=82)
        self.assertEqual(ctx.exception.status_code, 422)

    def test_an_unsupported_content_type_is_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            normalise(b"hello", "text/html", max_edge=512, quality=82)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_a_non_image_payload_is_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            normalise(b"not an image at all", "image/png", max_edge=512, quality=82)
        self.assertEqual(ctx.exception.status_code, 400)


class DataUriTest(unittest.TestCase):
    def test_to_data_uri_encodes_base64(self):
        self.assertEqual(to_data_uri(b"abc"), "data:image/jpeg;base64,YWJj")


if __name__ == "__main__":
    unittest.main()
