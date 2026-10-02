"""POST /api/v1/audio_assets : couverture HTTP (#86, prérequis multi-locuteurs #39).

Authentification, type/extension trompeurs, traversée de chemin, taille, décodage réel — complète les
tests service (``test_audio_assets.py``) et de câblage pipeline (``test_task.py``) par le protocole HTTP
réel (``TestClient`` sur l'application ASGI, aucun serveur ni appel réseau réel)."""

import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import asgi
from app.config import config
from app.services import audio_assets as aa
from app.services import bgm as bgm_service


class TestAudioAssetUploadHTTP(unittest.TestCase):
    VALID_SCOPE = "x" * 32

    def setUp(self):
        self.original_app_config = dict(config.app)
        config.app["api_key"] = ""  # cette classe ne teste pas l'authentification elle-même
        self.client = TestClient(asgi.app)
        self._assets_snapshot = dict(aa._assets)
        self._cleanup_snapshot = aa._last_cleanup_monotonic
        aa._assets.clear()
        aa._last_cleanup_monotonic = None
        # Jamais écrire dans le vrai storage/audio_assets/ de l'hôte : dossier temporaire dédié, patché
        # pour toute la durée du test (l'endpoint appelle lui-même cleanup() en interne).
        self._temp_dir_cm = tempfile.TemporaryDirectory()
        self.temp_dir = self._temp_dir_cm.__enter__()
        self._dir_patch = patch.object(aa, "uploaded_audio_asset_dir", return_value=self.temp_dir)
        self._dir_patch.start()

    def tearDown(self):
        self._dir_patch.stop()
        self._temp_dir_cm.__exit__(None, None, None)
        config.app.clear()
        config.app.update(self.original_app_config)
        aa._assets.clear()
        aa._assets.update(self._assets_snapshot)
        aa._last_cleanup_monotonic = self._cleanup_snapshot

    def _upload(self, filename="segment.mp3", content=b"fake-audio-bytes", scope=None, content_type="audio/mpeg"):
        return self.client.post(
            "/api/v1/audio_assets",
            files={"file": (filename, content, content_type)},
            data={"production_scope": scope if scope is not None else self.VALID_SCOPE},
        )

    def test_authenticated_valid_upload_returns_an_opaque_asset_id(self):
        with patch.object(bgm_service, "validate_audio_file", return_value=None):
            response = self._upload()

        self.assertEqual(response.status_code, 200)
        asset_id = response.json()["data"]["asset_id"]
        self.assertRegex(asset_id, r"^[0-9a-f]{32}$")
        # Jamais le nom de fichier ni un chemin serveur dans la réponse.
        self.assertNotIn("segment.mp3", response.text)
        self.assertNotIn("storage", response.text)
        # Stocké dans le dossier de test isolé, jamais dans le vrai storage/audio_assets/ de l'hôte.
        import os

        self.assertIn(f"{asset_id}.mp3", os.listdir(self.temp_dir))

    def test_unauthenticated_request_is_rejected_when_a_key_is_configured(self):
        config.app["api_key"] = "engine-secret"
        with patch.object(bgm_service, "validate_audio_file", return_value=None):
            missing = self._upload()
            wrong = self.client.post(
                "/api/v1/audio_assets",
                files={"file": ("segment.mp3", b"fake-audio-bytes", "audio/mpeg")},
                data={"production_scope": self.VALID_SCOPE},
                headers={"x-api-key": "wrong"},
            )
            accepted = self.client.post(
                "/api/v1/audio_assets",
                files={"file": ("segment.mp3", b"fake-audio-bytes", "audio/mpeg")},
                data={"production_scope": self.VALID_SCOPE},
                headers={"x-api-key": "engine-secret"},
            )

        self.assertEqual(missing.status_code, 401)
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(accepted.status_code, 200)

    def test_path_traversal_filename_is_sanitized_not_rejected_as_a_path(self):
        """Le nom fourni par le client n'est jamais utilisé comme chemin : une traversée de chemin dans
        le nom ne provoque ni erreur serveur ni écriture hors du dossier dédié — seul le dernier segment
        est retenu pour l'extension, et le fichier final s'appelle de toute façon <uuid4 hex>.<ext>."""
        with patch.object(bgm_service, "validate_audio_file", return_value=None):
            response = self._upload(filename="../../../etc/passwd.mp3")

        self.assertEqual(response.status_code, 200)

    def test_disguised_extension_is_rejected(self):
        response = self._upload(filename="segment.exe")
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("segment.exe", response.text)

    def test_file_that_fails_real_decoding_is_rejected(self):
        with patch.object(
            bgm_service, "validate_audio_file",
            side_effect=bgm_service.BgmUploadError("uploaded file must contain a decodable audio stream"),
        ):
            response = self._upload(content=b"not-really-audio-data")

        self.assertEqual(response.status_code, 400)

    def test_empty_file_is_rejected(self):
        response = self._upload(content=b"")
        self.assertEqual(response.status_code, 400)

    def test_oversized_file_is_rejected(self):
        with patch.object(aa, "MAX_AUDIO_ASSET_UPLOAD_BYTES", 4):
            response = self._upload(content=b"this-is-too-large-for-the-limit")
        self.assertEqual(response.status_code, 400)

    def test_missing_production_scope_is_rejected(self):
        response = self.client.post(
            "/api/v1/audio_assets",
            files={"file": ("segment.mp3", b"fake-audio-bytes", "audio/mpeg")},
        )
        # Ce dépôt convertit déjà les erreurs de validation FastAPI (422 par défaut) en 400 (voir
        # app/asgi.py:validation_exception_handler, documenté dans docs/lody-engine-contract.md) : même
        # convention que toutes les autres routes, pas une exception pour ce nouvel endpoint.
        self.assertEqual(response.status_code, 400)

    def test_too_short_production_scope_is_rejected(self):
        response = self._upload(scope="short")
        self.assertEqual(response.status_code, 400)

    def test_service_failure_returns_a_generic_500_without_leaking_detail(self):
        with patch.object(
            aa, "save_audio_asset_upload",
            side_effect=aa.AudioAssetServiceError("ffmpeg binary not found at /srv/secret/path"),
        ):
            response = self._upload()
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("/srv/secret/path", response.text)

    def test_openapi_documents_api_key_header_for_the_new_route(self):
        schema = self.client.get("/openapi.json").json()
        parameters = schema["paths"]["/api/v1/audio_assets"]["post"]["parameters"]
        self.assertTrue(
            any(parameter["in"] == "header" and parameter["name"] == "x-api-key" for parameter in parameters)
        )


if __name__ == "__main__":
    unittest.main()
