# -*- coding: utf-8 -*-
"""Transfert d'une image de référence (#92) : même sécurité que audio_assets (#86), cycle de vie différent
— résoluble PLUSIEURS FOIS (jamais consommée), voir le docstring de app/services/image_assets.py."""

import io
import tempfile
import time
import unittest
from unittest.mock import patch

from PIL import Image

from app.services import image_assets as ia


def _png_bytes(width=128, height=128, color=(10, 20, 30)):
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


class ImageAssetsTestCase(unittest.TestCase):
    """Isole le registre en mémoire ET le dossier de stockage pour toute la durée du test — jamais le
    vrai storage/image_assets/ de l'hôte, jamais une fuite entre tests."""

    def setUp(self):
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
        ia._assets.clear()
        ia._assets.update(self._assets_snapshot)
        ia._last_cleanup_monotonic = self._cleanup_snapshot


class TestUploadAndResolve(ImageAssetsTestCase):
    SCOPE = "a" * 32

    def test_valid_upload_returns_an_opaque_id_and_resolves_to_the_stored_file(self):
        asset_id = ia.save_image_asset_upload("ref.png", io.BytesIO(_png_bytes()), self.SCOPE)
        self.assertRegex(asset_id, r"^[0-9a-f]{32}$")
        self.assertNotIn("ref", asset_id)  # jamais dérivé du nom fourni
        path = ia.resolve_image_asset(asset_id, self.SCOPE)
        with Image.open(path) as image:
            self.assertEqual(image.size, (128, 128))

    def test_resolving_the_same_asset_twice_both_succeed_unlike_audio_assets(self):
        """Différence délibérée avec audio_assets (#86) : une référence visuelle sert PLUSIEURS scènes
        de la même production, donc n'est jamais marquée consommée après un premier resolve."""
        asset_id = ia.save_image_asset_upload("ref.png", io.BytesIO(_png_bytes()), self.SCOPE)
        first = ia.resolve_image_asset(asset_id, self.SCOPE)
        second = ia.resolve_image_asset(asset_id, self.SCOPE)
        third = ia.resolve_image_asset(asset_id, self.SCOPE)
        self.assertEqual(first, second, third)

    def test_mismatched_scope_is_rejected_with_a_generic_message(self):
        asset_id = ia.save_image_asset_upload("ref.png", io.BytesIO(_png_bytes()), self.SCOPE)
        with self.assertRaises(ia.ImageAssetError) as ctx:
            ia.resolve_image_asset(asset_id, "b" * 32)
        self.assertEqual(str(ctx.exception), "unknown or expired image asset reference")

    def test_unknown_asset_id_is_rejected_with_the_same_generic_message(self):
        with self.assertRaises(ia.ImageAssetError) as ctx:
            ia.resolve_image_asset("0" * 32, self.SCOPE)
        self.assertEqual(str(ctx.exception), "unknown or expired image asset reference")

    def test_rejects_too_short_production_scope(self):
        with self.assertRaises(ia.ImageAssetError):
            ia.save_image_asset_upload("ref.png", io.BytesIO(_png_bytes()), "short")

    def test_rejects_unsupported_extension(self):
        with self.assertRaises(ia.ImageAssetError):
            ia.save_image_asset_upload("ref.gif", io.BytesIO(_png_bytes()), self.SCOPE)

    def test_rejects_disguised_extension_with_undecodable_content(self):
        """Extension .png autorisée mais contenu non décodable : rejeté, jamais seulement sur l'extension."""
        with self.assertRaises(ia.ImageAssetError):
            ia.save_image_asset_upload("ref.png", io.BytesIO(b"not a real image"), self.SCOPE)

    def test_rejects_content_whose_real_format_does_not_match_its_extension(self):
        jpeg_bytes = io.BytesIO()
        Image.new("RGB", (128, 128)).save(jpeg_bytes, format="JPEG")
        with self.assertRaises(ia.ImageAssetError):
            ia.save_image_asset_upload("ref.png", io.BytesIO(jpeg_bytes.getvalue()), self.SCOPE)

    def test_rejects_oversized_upload(self):
        oversized = io.BytesIO(b"\x00" * (ia.MAX_IMAGE_ASSET_UPLOAD_BYTES + 1))
        with self.assertRaises(ia.ImageAssetError):
            ia.save_image_asset_upload("ref.png", oversized, self.SCOPE)

    def test_rejects_image_too_small_to_be_a_usable_reference(self):
        with self.assertRaises(ia.ImageAssetError):
            ia.save_image_asset_upload("ref.png", io.BytesIO(_png_bytes(width=8, height=8)), self.SCOPE)


class TestCleanupAndExpiry(ImageAssetsTestCase):
    SCOPE = "c" * 32

    def test_expired_asset_is_removed_and_no_longer_resolvable(self):
        asset_id = ia.save_image_asset_upload("ref.png", io.BytesIO(_png_bytes()), self.SCOPE)
        future = time.time() + ia.UNRESOLVED_TTL_SECONDS + 1
        ia.cleanup(now=future, force=True)
        with self.assertRaises(ia.ImageAssetError):
            ia.resolve_image_asset(asset_id, self.SCOPE)

    def test_fresh_asset_survives_cleanup(self):
        asset_id = ia.save_image_asset_upload("ref.png", io.BytesIO(_png_bytes()), self.SCOPE)
        ia.cleanup(now=time.time(), force=True)
        ia.resolve_image_asset(asset_id, self.SCOPE)  # ne lève pas


if __name__ == "__main__":
    unittest.main()
