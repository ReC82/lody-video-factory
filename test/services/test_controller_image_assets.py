"""POST /api/v1/image_assets : couverture HTTP (#92, continuité visuelle).

Complète les tests service (``test_image_assets.py``) par le protocole HTTP réel (``TestClient`` sur
l'application ASGI, aucun serveur ni appel réseau réel)."""

import io
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from app import asgi
from app.config import config
from app.services import image_assets as ia


def _png_bytes(width=128, height=128):
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (5, 6, 7)).save(buffer, format="PNG")
    return buffer.getvalue()


class TestImageAssetUploadHTTP(unittest.TestCase):
    VALID_SCOPE = "y" * 32

    def setUp(self):
        self.original_app_config = dict(config.app)
        config.app["api_key"] = ""
        self.client = TestClient(asgi.app)
        self._assets_snapshot = dict(ia._assets)
        self._cleanup_snapshot = ia._last_cleanup_monotonic
        ia._assets.clear()
        ia._last_cleanup_monotonic = None
        self._temp_dir_cm = tempfile.TemporaryDirectory()
        self.temp_dir = self._temp_dir_cm.__enter__()
        self._dir_patch = patch.object(ia, "uploaded_image_asset_dir", return_value=self.temp_dir)
        self._dir_patch.start()

    def tearDown(self):
        self._dir_patch.stop()
        self._temp_dir_cm.__exit__(None, None, None)
        config.app.clear()
        config.app.update(self.original_app_config)
        ia._assets.clear()
        ia._assets.update(self._assets_snapshot)
        ia._last_cleanup_monotonic = self._cleanup_snapshot

    def _upload(self, filename="ref.png", content=None, scope=None, content_type="image/png"):
        return self.client.post(
            "/api/v1/image_assets",
            files={"file": (filename, content if content is not None else _png_bytes(), content_type)},
            data={"production_scope": scope if scope is not None else self.VALID_SCOPE},
        )

    def test_valid_upload_returns_an_opaque_asset_id(self):
        response = self._upload()
        self.assertEqual(response.status_code, 200)
        asset_id = response.json()["data"]["asset_id"]
        self.assertRegex(asset_id, r"^[0-9a-f]{32}$")

    def test_uploaded_asset_resolves_with_the_matching_scope(self):
        asset_id = self._upload().json()["data"]["asset_id"]
        path = ia.resolve_image_asset(asset_id, self.VALID_SCOPE)
        with Image.open(path) as image:
            self.assertEqual(image.size, (128, 128))

    def test_undecodable_content_is_rejected_with_400(self):
        response = self._upload(content=b"not a real image")
        self.assertEqual(response.status_code, 400)

    def test_missing_production_scope_is_rejected(self):
        response = self.client.post(
            "/api/v1/image_assets", files={"file": ("ref.png", _png_bytes(), "image/png")}, data={}
        )
        self.assertEqual(response.status_code, 400)  # 422 FastAPI converti en 400 par ce dépôt


if __name__ == "__main__":
    unittest.main()
