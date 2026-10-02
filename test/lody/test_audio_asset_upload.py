"""Transfert d'un audio déjà synthétisé — côté connecteur Lody (#86, prérequis multi-locuteurs #39).

Complète le diagnostic côté moteur (``docs/`` + ``app/services/audio_assets.py`` + ses propres tests) par
le contrat exact que #87 pourra utiliser : ``upload_audio_asset()`` -> référence opaque -> ``submit()``
avec cette référence. Ne réalise AUCUN assemblage multi-segments ni génération multi-voix ici — seulement
la plomberie HTTP (connecteur) et le payload (``build_payload``), jamais l'orchestration elle-même.

Faux transport HTTP uniquement (comme ``test_generation_connector.py``) : aucun appel réel.
"""

from __future__ import annotations

import json

import pytest

from lody.generation import mpt_connector as mpt
from lody.generation.models import ErrorKind, ProviderError
from lody.generation.provider import VideoGenerationProvider
from lody.generation.safety import new_audio_asset_scope
from lody.generation.service import build_request
from lody.projects import ProjectRepository
from lody.seeds import SEED_PROJECTS


class _Recorder:
    """Faux transport : capture chaque requête (y compris le corps multipart brut) et renvoie une
    réponse préparée. Jamais d'appel réseau réel."""

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
        "http://engine:8080", "/tmp/lody-audio-asset-test-storage", transport=_Recorder(*responses),
    ), None


def _crypto_project():
    import tempfile
    from pathlib import Path

    repo = ProjectRepository(Path(tempfile.mktemp(suffix=".sqlite3")))
    repo.seed_defaults(SEED_PROJECTS)
    return next(p for p in repo.list_projects() if p.name == "LodyCrypto")


# ======================================================================================================================
# -- new_audio_asset_scope() : fonction pure --------------------------------------------------------------------------
# ======================================================================================================================
def test_scope_is_opaque_high_entropy_and_never_derived_from_a_production_id():
    scope_a = new_audio_asset_scope()
    scope_b = new_audio_asset_scope()
    assert scope_a != scope_b
    assert len(scope_a) == 64  # 256 bits, encodés en hexadécimal
    assert all(character in "0123456789abcdef" for character in scope_a)


# ======================================================================================================================
# -- VideoGenerationProvider (base) : repli explicite, jamais une simulation silencieuse --------------------------
# ======================================================================================================================
def test_base_provider_rejects_audio_asset_upload_by_default():
    class _Bare(VideoGenerationProvider):
        id = "bare"
        display_name = "bare"

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

    provider = _Bare()
    assert provider.supports_audio_assets is False
    with pytest.raises(ProviderError) as error:
        provider.upload_audio_asset(b"fake", "segment.mp3", "scope")
    assert error.value.kind is ErrorKind.REJECTED


# ======================================================================================================================
# -- MoneyPrinterTurboConnector.upload_audio_asset() : contrat HTTP réel (faux transport) ---------------------------
# ======================================================================================================================
def test_upload_audio_asset_sends_multipart_and_returns_the_opaque_reference():
    connector, _ = _connector((200, {"status": 200, "data": {"asset_id": "cb9bde8aad994c8ab60270d34a2006cc"}}))
    scope = new_audio_asset_scope()

    asset_id = connector.upload_audio_asset(b"fake-audio-bytes", "segment-1.mp3", scope)

    assert asset_id == "cb9bde8aad994c8ab60270d34a2006cc"
    method, url, body, headers = connector._transport.requests[0]
    assert method == "POST" and url.endswith("/api/v1/audio_assets")
    assert headers["Content-Type"].startswith("multipart/form-data; boundary=")
    assert b'name="production_scope"' in body and scope.encode() in body
    assert b'name="file"; filename="segment-1.mp3"' in body
    assert b"fake-audio-bytes" in body


def test_upload_audio_asset_sends_the_api_key_header_when_configured():
    connector = mpt.MoneyPrinterTurboConnector(
        "http://engine:8080", "/tmp/lody-audio-asset-test-storage", api_key="engine-secret",
        transport=_Recorder((200, {"status": 200, "data": {"asset_id": "a" * 32}})),
    )
    connector.upload_audio_asset(b"fake", "segment.mp3", new_audio_asset_scope())
    _, _, _, headers = connector._transport.requests[0]
    assert headers["x-api-key"] == "engine-secret"


def test_upload_audio_asset_raises_on_rejected_upload():
    connector, _ = _connector((400, {"status": 400, "message": "unsupported audio asset format"}))
    with pytest.raises(ProviderError) as error:
        connector.upload_audio_asset(b"fake", "segment.exe", new_audio_asset_scope())
    assert error.value.kind is ErrorKind.REJECTED


def test_upload_audio_asset_raises_if_no_reference_is_returned():
    connector, _ = _connector((200, {"status": 200, "data": {}}))
    with pytest.raises(ProviderError) as error:
        connector.upload_audio_asset(b"fake", "segment.mp3", new_audio_asset_scope())
    assert error.value.kind is ErrorKind.INVALID_RESPONSE


def test_upload_audio_asset_never_leaks_the_filename_or_scope_in_an_error(caplog):
    connector, _ = _connector((500, {"status": 500, "message": "internal failure"}))
    with pytest.raises(ProviderError):
        connector.upload_audio_asset(b"fake", "very-identifying-segment-name.mp3", new_audio_asset_scope())
    assert "very-identifying-segment-name" not in caplog.text


# ======================================================================================================================
# -- build_payload() : clés additives uniquement, compatibilité stricte --------------------------------------------
# ======================================================================================================================
def test_build_payload_omits_the_new_keys_by_default_byte_for_byte_compatible():
    """#86 : sans audio_asset_id (le cas de toutes les productions tant que #87 n'existe pas), le
    payload mono-voix historique est strictement inchangé."""
    from lody.generation.engine_facts import EngineFacts

    request = build_request(_crypto_project(), "Explique la blockchain aux débutants")
    payload = mpt.build_payload(request, EngineFacts())
    assert "custom_audio_asset_id" not in payload
    assert "audio_asset_scope" not in payload


def test_build_payload_includes_the_asset_reference_when_present():
    from lody.generation.engine_facts import EngineFacts

    request = build_request(_crypto_project(), "Explique la blockchain aux débutants")
    scope = new_audio_asset_scope()
    request = request.with_updates(audio_asset_id="cb9bde8aad994c8ab60270d34a2006cc", audio_asset_scope=scope)
    payload = mpt.build_payload(request, EngineFacts())
    assert payload["custom_audio_asset_id"] == "cb9bde8aad994c8ab60270d34a2006cc"
    assert payload["audio_asset_scope"] == scope


def test_generation_request_round_trips_the_new_fields_and_defaults_empty():
    from lody.generation.models import GenerationRequest

    historical = GenerationRequest.from_dict({"subject": "x"})  # ancien snapshot, sans les nouvelles clés
    assert historical.audio_asset_id == "" and historical.audio_asset_scope == ""

    with_asset = historical.with_updates(audio_asset_id="abc", audio_asset_scope="def")
    round_tripped = GenerationRequest.from_dict(with_asset.to_dict())
    assert round_tripped.audio_asset_id == "abc" and round_tripped.audio_asset_scope == "def"
