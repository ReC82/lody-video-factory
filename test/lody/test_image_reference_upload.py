"""Transfert d'une image de référence déjà validée — côté connecteur Lody (#92, continuité visuelle).

Même structure que ``test_audio_asset_upload.py`` (#86) : ``upload_reference_image()`` -> référence
opaque -> ``submit()`` avec cette référence ; ``generate_reference_proposal()`` -> une image isolée pour
prévisualisation. Faux transport HTTP uniquement, aucun appel réel.
"""

from __future__ import annotations

import base64
import json

import pytest

from lody.generation import mpt_connector as mpt
from lody.generation.models import ErrorKind, ProviderError
from lody.generation.provider import VideoGenerationProvider
from lody.generation.safety import new_image_asset_scope
from lody.generation.service import build_request
from lody.projects import ProjectRepository
from lody.seeds import SEED_PROJECTS


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
        "http://engine:8080", "/tmp/lody-image-asset-test-storage", transport=_Recorder(*responses),
    ), None


def _crypto_project():
    import tempfile
    from pathlib import Path

    repo = ProjectRepository(Path(tempfile.mktemp(suffix=".sqlite3")))
    repo.seed_defaults(SEED_PROJECTS)
    return next(p for p in repo.list_projects() if p.name == "LodyCrypto")


# ======================================================================================================================
# -- new_image_asset_scope() : fonction pure --------------------------------------------------------------------------
# ======================================================================================================================
def test_scope_is_opaque_high_entropy_and_distinct_from_the_audio_scope_generator():
    from lody.generation.safety import new_audio_asset_scope

    scope_a = new_image_asset_scope()
    scope_b = new_image_asset_scope()
    assert scope_a != scope_b
    assert len(scope_a) == 64
    assert all(character in "0123456789abcdef" for character in scope_a)
    assert scope_a != new_audio_asset_scope()  # deux registres indépendants, jamais le même générateur


# ======================================================================================================================
# -- VideoGenerationProvider (base) : repli explicite, jamais une simulation silencieuse --------------------------
# ======================================================================================================================
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


def test_base_provider_rejects_reference_image_upload_by_default():
    provider = _Bare()
    assert provider.supports_reference_images is False
    with pytest.raises(ProviderError) as error:
        provider.upload_reference_image(b"fake-png", "scope")
    assert error.value.kind is ErrorKind.REJECTED


def test_base_provider_rejects_reference_proposal_generation_by_default():
    with pytest.raises(ProviderError) as error:
        _Bare().generate_reference_proposal("a brave voxel hero")
    assert error.value.kind is ErrorKind.REJECTED


# ======================================================================================================================
# -- MoneyPrinterTurboConnector.upload_reference_image() : contrat HTTP réel (faux transport) -----------------------
# ======================================================================================================================
def test_upload_reference_image_sends_multipart_and_returns_the_opaque_reference():
    connector, _ = _connector((200, {"status": 200, "data": {"asset_id": "ab9fae41c8f3774961cfdc3d2e5b7a10"}}))
    scope = new_image_asset_scope()

    asset_id = connector.upload_reference_image(b"fake-png-bytes", scope)

    assert asset_id == "ab9fae41c8f3774961cfdc3d2e5b7a10"
    method, url, body, headers = connector._transport.requests[0]
    assert method == "POST" and url.endswith("/api/v1/image_assets")
    assert headers["Content-Type"].startswith("multipart/form-data; boundary=")
    assert b'name="production_scope"' in body and scope.encode() in body
    assert b'name="file"; filename="reference.png"' in body
    assert b"fake-png-bytes" in body


def test_upload_reference_image_raises_on_rejected_upload():
    connector, _ = _connector((400, {"status": 400, "message": "unsupported image asset format"}))
    with pytest.raises(ProviderError) as error:
        connector.upload_reference_image(b"fake", new_image_asset_scope())
    assert error.value.kind is ErrorKind.REJECTED


def test_upload_reference_image_raises_if_no_reference_is_returned():
    connector, _ = _connector((200, {"status": 200, "data": {}}))
    with pytest.raises(ProviderError) as error:
        connector.upload_reference_image(b"fake", new_image_asset_scope())
    assert error.value.kind is ErrorKind.INVALID_RESPONSE


# ======================================================================================================================
# -- MoneyPrinterTurboConnector.generate_reference_proposal() -------------------------------------------------------
# ======================================================================================================================
def test_generate_reference_proposal_decodes_the_returned_image():
    original = b"\x89PNG\r\n\x1a\nfake-but-plausible-bytes"
    connector, _ = _connector(
        (200, {"status": 200, "data": {"image_base64": base64.b64encode(original).decode("ascii")}})
    )
    result = connector.generate_reference_proposal("Eli, young voxel hero, blue eyes")
    assert result == original
    method, url, body, headers = connector._transport.requests[0]
    assert method == "POST" and url.endswith("/api/v1/image_preview")
    assert json.loads(body)["prompt"] == "Eli, young voxel hero, blue eyes"


def test_generate_reference_proposal_raises_if_no_image_is_returned():
    connector, _ = _connector((200, {"status": 200, "data": {}}))
    with pytest.raises(ProviderError) as error:
        connector.generate_reference_proposal("Eli")
    assert error.value.kind is ErrorKind.INVALID_RESPONSE


def test_generate_reference_proposal_propagates_rejection():
    connector, _ = _connector((400, {"status": 400, "message": "the image provider is not configured"}))
    with pytest.raises(ProviderError) as error:
        connector.generate_reference_proposal("Eli")
    assert error.value.kind is ErrorKind.REJECTED


# ======================================================================================================================
# -- build_payload() : clés additives uniquement, compatibilité stricte --------------------------------------------
# ======================================================================================================================
def test_build_payload_omits_the_new_keys_by_default_byte_for_byte_compatible():
    """#92 : sans character_reference_asset_id (le cas de toute production sans référence configurée), le
    payload texte-seul historique est strictement inchangé."""
    from lody.generation.engine_facts import EngineFacts

    request = build_request(_crypto_project(), "Explique la blockchain aux débutants")
    payload = mpt.build_payload(request, EngineFacts())
    assert "character_reference_asset_id" not in payload
    assert "image_asset_scope" not in payload


def test_build_payload_includes_the_asset_reference_when_present():
    from lody.generation.engine_facts import EngineFacts

    request = build_request(_crypto_project(), "Explique la blockchain aux débutants")
    scope = new_image_asset_scope()
    request = request.with_updates(character_reference_asset_id="ab9fae41c8f3774961cfdc3d2e5b7a10",
                                   image_asset_scope=scope)
    payload = mpt.build_payload(request, EngineFacts())
    assert payload["character_reference_asset_id"] == "ab9fae41c8f3774961cfdc3d2e5b7a10"
    assert payload["image_asset_scope"] == scope


def test_generation_request_round_trips_the_new_fields_and_defaults_empty():
    from lody.generation.models import GenerationRequest

    historical = GenerationRequest.from_dict({"subject": "x"})  # ancien snapshot, sans les nouvelles clés
    assert historical.character_reference_asset_id == "" and historical.image_asset_scope == ""
