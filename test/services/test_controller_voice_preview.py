# -*- coding: utf-8 -*-
"""POST /api/v1/voice_preview : essai vocal comparatif pour une voix ElevenLabs déjà configurée (#92,
diagnostic du jeu vocal) — jamais de tâche vidéo, jamais persisté, jamais la voix réellement utilisée en
production tant qu'aucun choix explicite n'a été fait."""

import base64
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import asgi


def _fake_post(status_code=200, content=b"fake-mp3-bytes"):
    return type("R", (), {
        "status_code": status_code, "content": content,
        "json": lambda self: {}, "text": "rejected",
    })()


class TestVoicePreviewHTTP(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(asgi.app)

    def test_generates_and_returns_base64_without_creating_a_task(self):
        with (
            patch("app.services.voice.get_elevenlabs_api_key", return_value="fake-key"),
            patch("app.services.voice.requests.post", return_value=_fake_post()) as post,
            patch("app.services.voice.AudioFileClip") as clip_cls,
        ):
            clip_cls.return_value.duration = 2.0
            clip_cls.return_value.close = lambda: None
            response = self.client.post(
                "/api/v1/voice_preview",
                json={"text": "Attends, je l'ai déjà dit combien de fois, ça ?", "voice_id": "abc123",
                      "stability": 0.3, "style": 0.35, "speed": 1.1},
            )

        self.assertEqual(response.status_code, 200)
        sent = post.call_args.kwargs["json"]
        self.assertEqual(sent["voice_settings"], {
            "stability": 0.3, "similarity_boost": 0.75, "style": 0.35,
            "use_speaker_boost": True, "speed": 1.1,
        })
        decoded = base64.b64decode(response.json()["data"]["audio_base64"])
        self.assertEqual(decoded, b"fake-mp3-bytes")

    def test_omitted_settings_fall_back_to_elevenlabs_defaults(self):
        with (
            patch("app.services.voice.get_elevenlabs_api_key", return_value="fake-key"),
            patch("app.services.voice.requests.post", return_value=_fake_post()) as post,
            patch("app.services.voice.AudioFileClip") as clip_cls,
        ):
            clip_cls.return_value.duration = 2.0
            clip_cls.return_value.close = lambda: None
            response = self.client.post(
                "/api/v1/voice_preview", json={"text": "Bonjour.", "voice_id": "abc123"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(post.call_args.kwargs["json"]["voice_settings"], {
            "stability": 0.5, "similarity_boost": 0.75, "style": 0.0,
            "use_speaker_boost": True, "speed": 1.0,
        })

    def test_missing_elevenlabs_configuration_is_rejected_with_400(self):
        with patch("app.services.voice.get_elevenlabs_api_key", return_value=""):
            response = self.client.post(
                "/api/v1/voice_preview", json={"text": "Bonjour.", "voice_id": "abc123"},
            )
        self.assertEqual(response.status_code, 400)

    def test_empty_text_is_rejected(self):
        response = self.client.post("/api/v1/voice_preview", json={"text": "", "voice_id": "abc123"})
        self.assertEqual(response.status_code, 400)

    def test_out_of_range_speed_is_rejected(self):
        response = self.client.post(
            "/api/v1/voice_preview", json={"text": "Bonjour.", "voice_id": "abc123", "speed": 9.0},
        )
        self.assertEqual(response.status_code, 400)

    def test_provider_failure_is_reported_as_502_not_silently_empty(self):
        with (
            patch("app.services.voice.get_elevenlabs_api_key", return_value="fake-key"),
            patch("app.services.voice.requests.post", return_value=_fake_post(status_code=400)),
        ):
            response = self.client.post(
                "/api/v1/voice_preview", json={"text": "Bonjour.", "voice_id": "abc123"},
            )
        self.assertEqual(response.status_code, 502)


if __name__ == "__main__":
    unittest.main()
