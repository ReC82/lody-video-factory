"""Essai vocal isolé pour une voix ElevenLabs déjà configurée — côté connecteur Lody (#92, diagnostic du
jeu vocal). Même structure que ``test_image_reference_upload.py`` : faux transport HTTP uniquement, aucun
appel réel."""

from __future__ import annotations

import base64
import json

import pytest

from lody.generation import mpt_connector as mpt
from lody.generation.models import ErrorKind, ProviderError
from lody.generation.provider import VideoGenerationProvider


class _Recorder:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[tuple[str, str, bytes | None, dict]] = []

    def __call__(self, method, url, body, headers, timeout):
        self.requests.append((method, url, body, dict(headers)))
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        status, payload = item
        return status, payload if isinstance(payload, bytes) else json.dumps(payload).encode()


def _connector(*responses):
    return mpt.MoneyPrinterTurboConnector(
        "http://engine:8080", "/tmp/lody-voice-preview-test-storage", transport=_Recorder(*responses),
    )


class _Bare(VideoGenerationProvider):
    id = "bare"

    def preflight(self, request):
        raise NotImplementedError

    def write_script(self, request, narrative_block="", dialogue_character=""):
        raise NotImplementedError

    def submit(self, request, idempotency_key):
        raise NotImplementedError

    def poll(self, task):
        raise NotImplementedError

    def resolve_asset(self, task, ref, suffixes=()):
        raise NotImplementedError

    def recover(self, task):
        return None


def test_base_provider_rejects_voice_preview_generation_by_default():
    provider = _Bare()
    assert provider.supports_voice_preview is False
    with pytest.raises(ProviderError) as error:
        provider.generate_voice_preview("Bonjour.", "voice-id", {})
    assert error.value.kind is ErrorKind.REJECTED


# ======================================================================================================================
# -- MoneyPrinterTurboConnector.generate_voice_preview() : contrat HTTP réel (faux transport) -----------------------
# ======================================================================================================================
def test_generate_voice_preview_decodes_the_returned_audio_and_sends_settings_as_is():
    original = b"ID3fake-but-plausible-mp3-bytes"
    connector = _connector((200, {"status": 200, "data": {"audio_base64": base64.b64encode(original).decode("ascii")}}))
    settings = {"stability": 0.3, "similarity_boost": 0.75, "style": 0.35, "use_speaker_boost": True, "speed": 1.1}

    result = connector.generate_voice_preview("Attends, je l'ai déjà dit combien de fois, ça ?", "abc123", settings)

    assert result == original
    method, url, body, headers = connector._transport.requests[0]
    assert method == "POST" and url.endswith("/api/v1/voice_preview")
    sent = json.loads(body)
    assert sent["text"] == "Attends, je l'ai déjà dit combien de fois, ça ?"
    assert sent["voice_id"] == "abc123"
    for key, value in settings.items():
        assert sent[key] == value


def test_generate_voice_preview_raises_if_no_audio_is_returned():
    connector = _connector((200, {"status": 200, "data": {}}))
    with pytest.raises(ProviderError) as error:
        connector.generate_voice_preview("Bonjour.", "abc123", {})
    assert error.value.kind is ErrorKind.INVALID_RESPONSE


def test_generate_voice_preview_propagates_rejection():
    connector = _connector((400, {"status": 400, "message": "ElevenLabs is not configured"}))
    with pytest.raises(ProviderError) as error:
        connector.generate_voice_preview("Bonjour.", "abc123", {})
    assert error.value.kind is ErrorKind.REJECTED


def test_supports_voice_preview_is_true_for_the_real_connector():
    assert _connector().supports_voice_preview is True
