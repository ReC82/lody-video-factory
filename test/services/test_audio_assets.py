"""Transfert d'un audio déjà synthétisé (#86, prérequis multi-locuteurs #39).

Couvre le cycle de vie complet (créé -> réservé -> consommé -> nettoyé), la sécurité (traversée de
chemin, extension/MIME trompeurs, décodage réel, taille, jeton d'autorisation opaque) et l'absence de
tout repli silencieux. Aucun appel réel : ``bgm.validate_audio_file`` (décodage FFmpeg) est simulé dans
tous les tests sauf ceux qui testent explicitement son échec — le contenu audio lui-même n'a jamais
besoin d'être un fichier décodable pour exercer cette logique.
"""

import io
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.services import audio_assets as aa
from app.services import bgm as bgm_service


class _UnseekableUpload(io.BytesIO):
    def seek(self, *args, **kwargs):
        raise OSError("stream is not seekable")


class _TextUpload(io.BytesIO):
    def read(self, *args, **kwargs):
        return "not binary"


class AudioAssetsTestCase(unittest.TestCase):
    """Base commune : registre et horloge de nettoyage réinitialisés avant/après chaque test, pour que
    l'ordre d'exécution des tests ne puisse jamais s'influencer (rate-limit du nettoyage, assets
    résiduels)."""

    def setUp(self):
        self._assets_snapshot = dict(aa._assets)
        self._cleanup_snapshot = aa._last_cleanup_monotonic
        aa._assets.clear()
        aa._last_cleanup_monotonic = None

    def tearDown(self):
        aa._assets.clear()
        aa._assets.update(self._assets_snapshot)
        aa._last_cleanup_monotonic = self._cleanup_snapshot


# ======================================================================================================================
# -- sanitize_audio_asset_filename() : fonction pure, sécurité des chemins -----------------------------------------
# ======================================================================================================================
class TestFilenameSanitization(AudioAssetsTestCase):
    def test_accepts_every_supported_extension_case_insensitively(self):
        for extension in aa.SUPPORTED_AUDIO_ASSET_EXTENSIONS:
            filename = f"segment{extension.upper()}"
            with self.subTest(filename=filename):
                self.assertEqual(aa.sanitize_audio_asset_filename(filename), filename)

    def test_strips_posix_and_windows_path_components(self):
        """Traversée de chemin : seul le dernier segment est conservé, jamais utilisé comme chemin tel
        quel (le nom final sur disque est de toute façon <uuid4 hex><extension>, voir
        save_audio_asset_upload — ce test couvre uniquement la fonction de sanitation elle-même)."""
        self.assertEqual(aa.sanitize_audio_asset_filename("../../etc/passwd.mp3"), "passwd.mp3")
        self.assertEqual(aa.sanitize_audio_asset_filename(r"C:\windows\system32\evil.wav"), "evil.wav")

    def test_rejects_invalid_names_and_untrusted_extensions(self):
        for filename in (
            "", "   ", ".", "..", "segment.mp4", "segment.mp3\x00", "CON.mp3", "lpt1.wav",
            "bad:name.mp3", "bad?.flac", ".audio-asset-upload-user.m4a", "segment.exe", "segment",
        ):
            with self.subTest(filename=filename):
                with self.assertRaises(aa.AudioAssetError):
                    aa.sanitize_audio_asset_filename(filename)


# ======================================================================================================================
# -- save_audio_asset_upload() : upload, sécurité, stockage ---------------------------------------------------------
# ======================================================================================================================
class TestUpload(AudioAssetsTestCase):
    VALID_SCOPE = "a" * 32

    def test_valid_upload_returns_an_opaque_unpredictable_asset_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(aa, "uploaded_audio_asset_dir", return_value=temp_dir),
                patch.object(bgm_service, "validate_audio_file", return_value=None),
            ):
                first = aa.save_audio_asset_upload("segment.mp3", io.BytesIO(b"segment-one"), self.VALID_SCOPE)
                second = aa.save_audio_asset_upload("segment.mp3", io.BytesIO(b"segment-two"), self.VALID_SCOPE)

            # Opaque : 32 caractères hexadécimaux (uuid4().hex), jamais dérivé du nom de fichier.
            self.assertRegex(first, r"^[0-9a-f]{32}$")
            self.assertRegex(second, r"^[0-9a-f]{32}$")
            # Imprévisible et indépendant du contenu/nom : deux uploads identiques -> deux références
            # différentes (pas de déduplication par contenu qui permettrait de deviner une référence).
            self.assertNotEqual(first, second)
            self.assertEqual(
                Path(temp_dir, f"{first}.mp3").read_bytes(), b"segment-one"
            )
            self.assertEqual(
                Path(temp_dir, f"{second}.mp3").read_bytes(), b"segment-two"
            )
            # Aucun fichier temporaire laissé derrière.
            self.assertFalse(any(name.startswith(aa._INTERNAL_UPLOAD_PREFIX) for name in os.listdir(temp_dir)))

    def test_decodable_audio_validation_runs_before_storage_never_header_only(self):
        """Le contenu est réellement décodé (FFmpeg), jamais seulement l'extension ou un en-tête — un
        fichier qui échoue au décodage est refusé même avec une extension valide."""
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(aa, "uploaded_audio_asset_dir", return_value=temp_dir),
                patch.object(
                    bgm_service, "validate_audio_file",
                    side_effect=bgm_service.BgmUploadError("uploaded file must contain a decodable audio stream"),
                ) as validate,
            ):
                with self.assertRaises(aa.AudioAssetError):
                    aa.save_audio_asset_upload("fake.mp3", io.BytesIO(b"not-really-audio"), self.VALID_SCOPE)
            validate.assert_called_once()
            self.assertEqual(os.listdir(temp_dir), [])  # aucun fichier (temporaire ou final) laissé derrière

    def test_disguised_extension_with_undecodable_content_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(aa, "uploaded_audio_asset_dir", return_value=temp_dir),
                patch.object(
                    bgm_service, "validate_audio_file",
                    side_effect=bgm_service.BgmUploadError("uploaded file must contain a decodable audio stream"),
                ),
            ):
                with self.assertRaises(aa.AudioAssetError):
                    aa.save_audio_asset_upload("video.mp3", io.BytesIO(b"\x00\x01\x02binary-garbage"), self.VALID_SCOPE)

    def test_rejects_empty_unseekable_and_non_binary_uploads(self):
        invalid_sources = (
            (io.BytesIO(b""), "audio asset file is empty"),
            (_UnseekableUpload(b"audio"), "not seekable"),
            (_TextUpload(b"audio"), "must be binary"),
        )
        for source, expected_error in invalid_sources:
            with self.subTest(expected_error=expected_error):
                with tempfile.TemporaryDirectory() as temp_dir:
                    with patch.object(aa, "uploaded_audio_asset_dir", return_value=temp_dir):
                        with self.assertRaisesRegex(aa.AudioAssetError, expected_error):
                            aa.save_audio_asset_upload("invalid.mp3", source, self.VALID_SCOPE)
                    self.assertEqual(os.listdir(temp_dir), [])

    def test_rejects_oversized_upload_and_cleans_temp_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(aa, "uploaded_audio_asset_dir", return_value=temp_dir),
                patch.object(aa, "MAX_AUDIO_ASSET_UPLOAD_BYTES", 4),
            ):
                with self.assertRaises(aa.AudioAssetError):
                    aa.save_audio_asset_upload("large.mp3", io.BytesIO(b"12345"), self.VALID_SCOPE)
            self.assertEqual(os.listdir(temp_dir), [])

    def test_rejects_too_short_production_scope(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(aa, "uploaded_audio_asset_dir", return_value=temp_dir):
                with self.assertRaises(aa.AudioAssetError):
                    aa.save_audio_asset_upload("segment.mp3", io.BytesIO(b"data"), "short")
            self.assertEqual(os.listdir(temp_dir), [])  # rejeté avant même de toucher le disque

    def test_rejects_non_string_production_scope(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(aa, "uploaded_audio_asset_dir", return_value=temp_dir):
                with self.assertRaises(aa.AudioAssetError):
                    aa.save_audio_asset_upload("segment.mp3", io.BytesIO(b"data"), None)

    def test_double_upload_of_identical_content_is_handled_deterministically(self):
        """Un ré-essai légitime (après coupure réseau côté client, par exemple) obtient une NOUVELLE
        référence indépendante — jamais une collision, jamais un état partagé entre les deux."""
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(aa, "uploaded_audio_asset_dir", return_value=temp_dir),
                patch.object(bgm_service, "validate_audio_file", return_value=None),
            ):
                retry_1 = aa.save_audio_asset_upload("segment.mp3", io.BytesIO(b"identical"), self.VALID_SCOPE)
                retry_2 = aa.save_audio_asset_upload("segment.mp3", io.BytesIO(b"identical"), self.VALID_SCOPE)

            self.assertNotEqual(retry_1, retry_2)
            path = aa.resolve_audio_asset(retry_1, self.VALID_SCOPE)
            self.assertTrue(os.path.isfile(path))
            # La première consommation ne doit jamais affecter la seconde référence, indépendante.
            path_2 = aa.resolve_audio_asset(retry_2, self.VALID_SCOPE)
            self.assertNotEqual(path, path_2)
            self.assertTrue(os.path.isfile(path_2))


# ======================================================================================================================
# -- resolve_audio_asset() : autorisation, consommation unique, erreurs génériques ----------------------------------
# ======================================================================================================================
class TestResolution(AudioAssetsTestCase):
    VALID_SCOPE = "b" * 32

    def _upload(self, temp_dir, scope=None):
        with (
            patch.object(aa, "uploaded_audio_asset_dir", return_value=temp_dir),
            patch.object(bgm_service, "validate_audio_file", return_value=None),
        ):
            return aa.save_audio_asset_upload("segment.mp3", io.BytesIO(b"segment"), scope or self.VALID_SCOPE)

    def test_matching_scope_resolves_and_consumes_the_asset(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_id = self._upload(temp_dir)
            path = aa.resolve_audio_asset(asset_id, self.VALID_SCOPE)
            self.assertTrue(os.path.isfile(path))
            self.assertTrue(path.endswith(".mp3"))

    def test_cross_project_or_cross_production_scope_mismatch_is_rejected(self):
        """Isolation stricte : un jeton différent — qu'il appartienne à un autre projet ou une autre
        production — ne peut jamais consommer l'asset d'un autre."""
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_id = self._upload(temp_dir)
            with self.assertRaises(aa.AudioAssetError):
                aa.resolve_audio_asset(asset_id, "c" * 32)
            # L'échec d'autorisation ne consomme PAS l'asset : le bon scope peut toujours le résoudre.
            path = aa.resolve_audio_asset(asset_id, self.VALID_SCOPE)
            self.assertTrue(os.path.isfile(path))

    def test_unknown_reference_is_rejected_with_a_generic_message(self):
        with self.assertRaises(aa.AudioAssetError) as raised:
            aa.resolve_audio_asset("0" * 32, self.VALID_SCOPE)
        self.assertEqual(str(raised.exception), "unknown or expired audio asset reference")

    def test_already_consumed_reference_cannot_be_reused_by_another_production(self):
        """Un asset consommé ne doit jamais pouvoir être détourné par une autre production — même avec
        le scope EXACT qui a servi la première fois."""
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_id = self._upload(temp_dir)
            aa.resolve_audio_asset(asset_id, self.VALID_SCOPE)
            with self.assertRaises(aa.AudioAssetError) as raised:
                aa.resolve_audio_asset(asset_id, self.VALID_SCOPE)
            self.assertEqual(str(raised.exception), "unknown or expired audio asset reference")

    def test_unknown_mismatch_and_already_consumed_share_the_exact_same_message(self):
        """Message générique IDENTIQUE dans les trois cas : jamais de distinction qui laisserait
        deviner si une référence est inconnue, mal autorisée, ou déjà consommée."""
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_id = self._upload(temp_dir)
            aa.resolve_audio_asset(asset_id, self.VALID_SCOPE)  # consomme
            messages = set()
            for probe_id, probe_scope in (
                ("0" * 32, self.VALID_SCOPE),         # inconnu
                (asset_id, "d" * 32),                 # scope erroné (mais déjà consommé de toute façon)
                (asset_id, self.VALID_SCOPE),         # déjà consommé
            ):
                with self.assertRaises(aa.AudioAssetError) as raised:
                    aa.resolve_audio_asset(probe_id, probe_scope)
                messages.add(str(raised.exception))
            self.assertEqual(messages, {"unknown or expired audio asset reference"})

    def test_expired_unconsumed_reference_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_id = self._upload(temp_dir)
            future = time.time() + aa.UNRESOLVED_TTL_SECONDS + 10
            aa.cleanup(now=future, force=True)
            with self.assertRaises(aa.AudioAssetError):
                aa.resolve_audio_asset(asset_id, self.VALID_SCOPE)


# ======================================================================================================================
# -- cleanup() : cycle de vie, nettoyage déterministe ---------------------------------------------------------------
# ======================================================================================================================
class TestCleanup(AudioAssetsTestCase):
    VALID_SCOPE = "e" * 32

    def test_removes_unconsumed_assets_past_ttl_and_keeps_fresh_ones(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(aa, "uploaded_audio_asset_dir", return_value=temp_dir),
                patch.object(bgm_service, "validate_audio_file", return_value=None),
            ):
                stale = aa.save_audio_asset_upload("old.mp3", io.BytesIO(b"old"), self.VALID_SCOPE)
                fresh = aa.save_audio_asset_upload("new.mp3", io.BytesIO(b"new"), self.VALID_SCOPE)

            future = time.time() + aa.UNRESOLVED_TTL_SECONDS + 10
            removed = aa.cleanup(now=future, force=True)
            self.assertEqual(removed, 2)  # les deux sont "unresolved" et au-delà du même TTL
            self.assertNotIn(stale, aa._assets)
            self.assertNotIn(fresh, aa._assets)

    def test_removes_consumed_assets_only_after_the_grace_period(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(aa, "uploaded_audio_asset_dir", return_value=temp_dir),
                patch.object(bgm_service, "validate_audio_file", return_value=None),
            ):
                asset_id = aa.save_audio_asset_upload("seg.mp3", io.BytesIO(b"seg"), self.VALID_SCOPE)
            aa.resolve_audio_asset(asset_id, self.VALID_SCOPE)

            just_consumed = aa.cleanup(now=time.time(), force=True)
            self.assertEqual(just_consumed, 0)  # encore dans la période de grâce
            self.assertIn(asset_id, aa._assets)

            after_grace = time.time() + aa.CONSUMED_GRACE_SECONDS + 10
            removed = aa.cleanup(now=after_grace, force=True)
            self.assertEqual(removed, 1)
            self.assertNotIn(asset_id, aa._assets)

    def test_cleanup_after_failure_never_leaves_a_registered_entry(self):
        """Nettoyage après échec : un upload qui échoue à la validation ne crée jamais d'entrée dans le
        registre (voir aussi TestUpload.test_decodable_audio_validation_runs_before_storage_never_header_only)."""
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(aa, "uploaded_audio_asset_dir", return_value=temp_dir),
                patch.object(
                    bgm_service, "validate_audio_file",
                    side_effect=bgm_service.BgmUploadError("invalid"),
                ),
            ):
                with self.assertRaises(aa.AudioAssetError):
                    aa.save_audio_asset_upload("bad.mp3", io.BytesIO(b"bad"), self.VALID_SCOPE)
            self.assertEqual(aa._assets, {})

    def test_cleanup_never_touches_files_with_an_unrelated_name(self):
        """Le nettoyage par motif de nom (orphelins après redémarrage) ne doit jamais toucher un
        fichier qui n'est pas au format <uuid4 hex><extension> des assets audio."""
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(aa, "uploaded_audio_asset_dir", return_value=temp_dir):
                unrelated = Path(temp_dir, "readme.txt")
                unrelated.write_text("do not touch")
                old = time.time() - aa.UNRESOLVED_TTL_SECONDS - 100
                os.utime(unrelated, (old, old))

                removed = aa.cleanup(force=True)
                self.assertEqual(removed, 0)
                self.assertTrue(unrelated.is_file())

    def test_cleanup_sweeps_orphan_files_left_by_a_lost_registry(self):
        """Après un redémarrage (registre en mémoire perdu), un fichier orphelin au bon format est
        toujours nettoyé — seconde ligne de défense indépendante du registre."""
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(aa, "uploaded_audio_asset_dir", return_value=temp_dir):
                orphan = Path(temp_dir, ("f" * 32) + ".mp3")
                orphan.write_bytes(b"orphan")
                old = time.time() - aa.UNRESOLVED_TTL_SECONDS - 100
                os.utime(orphan, (old, old))

                removed = aa.cleanup(force=True)
                self.assertEqual(removed, 1)
                self.assertFalse(orphan.exists())

    def test_cleanup_is_rate_limited_unless_forced(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(aa, "uploaded_audio_asset_dir", return_value=temp_dir):
                aa.cleanup(force=True)  # arme le rate-limit
                immediate = aa.cleanup()  # pas assez de temps écoulé, jamais forcé
                self.assertEqual(immediate, 0)


if __name__ == "__main__":
    unittest.main()
