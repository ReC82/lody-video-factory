"""Lexique de prononciation (#162) : la voix lit « èss-dé-i », l'écran garde « SDI ».

Non-régression sur SDI, HDMI, 3G-SDI, 12G-SDI, BNC et 4K. Ces tests vérifient la MÉCANIQUE (substitution,
portée, validation, blocage, payload) ; ils ne remplacent jamais l'écoute humaine : les lectures employées
ici sont des données de test, pas des prononciations retenues. Faux connecteurs uniquement, aucun appel payant.
"""

from __future__ import annotations

import copy
import sqlite3
from decimal import Decimal

import pytest

from lody import db, pronunciation
from lody.characters import CharacterRepository
from lody.generation.costing import PriceBook
from lody.generation.engine_facts import EngineFacts
from lody.generation.models import GenerationRequest, ProductionStatus
from lody.generation import mpt_connector as mpt
from lody.generation.service import LaunchError, ProductionService, build_request, make_snapshot
from lody.generation.store import ProductionRepository
from lody.locations import LocationRepository
from lody.projects import DEFAULTS, ProjectRepository
from lody.pronunciation import (
    LexiconEntry,
    PronunciationRepository,
    PronunciationValidationError,
    applicable,
    audit,
    render,
)
from lody.view_pronunciation import preview_voices
from test.lody.fakes import ScriptedConnector, SyncExecutor

PRICES = PriceBook("EUR", {("text", "openai"): Decimal("0.02"), ("visual", "openai_image"): Decimal("0.04"),
                           ("voice", "elevenlabs"): Decimal("0.3"), ("music", "elevenlabs"): Decimal("0.1")})


def _entry(written, spoken, **fields):
    return LexiconEntry(written=written, spoken=spoken, validated_at=fields.pop("validated_at", "2026-11-11"), **fields)


LEXICON = [
    _entry("SDI", "èss-dé-i"),
    _entry("3G-SDI", "trois-gé èss-dé-i"),
    _entry("12G-SDI", "douze-gé èss-dé-i"),
    _entry("HDMI", "hache-dé-èm-i"),
    _entry("BNC", "bé-èn-cé"),
    _entry("4K", "quatre-ka"),
]
TEXT = "Le SDI transporte l'image. Le 3G-SDI et le 12G-SDI vont plus vite. L'HDMI ? Pas en BNC. Une image 4K."


# -- substitution ------------------------------------------------------------------------------------------
def test_each_reference_expression_is_read_with_its_validated_reading_and_shown_exactly():
    result = render(TEXT, applicable(LEXICON, "fr-FR"))
    assert result.spoken == ("Le èss-dé-i transporte l'image. Le trois-gé èss-dé-i et le douze-gé èss-dé-i vont "
                             "plus vite. L'hache-dé-èm-i ? Pas en bé-èn-cé. Une image quatre-ka.")
    assert result.subtitle == TEXT  # les sigles restent typographiquement exacts à l'écran
    assert set(result.applied) == {"SDI", "3G-SDI", "12G-SDI", "HDMI", "BNC", "4K"}


@pytest.mark.parametrize("text", ["Le 3G-SDI", "Le 3G‑SDI", "Le 3G–SDI"])
def test_longest_expression_wins_and_typographic_dashes_are_recognised(text):
    result = render(text, applicable(LEXICON, "fr"))
    assert result.spoken == "Le trois-gé èss-dé-i"
    assert result.subtitle == text  # tiret d'origine conservé à l'écran


def test_word_boundaries_are_strict_and_case_sensitive():
    entries = applicable([_entry("SDI", "èss-dé-i")], "fr")
    for text in ("SDIs", "MSDI", "sdi", "6G-SDI", "SDI-X"):
        assert render(text, entries).spoken == text
    assert render("(SDI).", entries).spoken == "(èss-dé-i)."


def test_a_reading_is_never_substituted_again():
    entries = applicable([_entry("SDI", "SDI serial digital"), _entry("HD", "achedé")], "fr")
    assert render("SDI HD", entries).spoken == "SDI serial digital achedé"


def test_unvalidated_entries_are_never_applied():
    entries = applicable([_entry("SDI", "èss-dé-i", validated_at="")], "fr")
    assert entries == []
    assert render("Le SDI", entries).spoken == "Le SDI"


def test_explicit_subtitle_form_is_used_only_on_screen():
    entries = applicable([_entry("3G-SDI", "trois-gé èss-dé-i", subtitle="3G‑SDI")], "fr")
    result = render("Le 3G-SDI", entries)
    assert result.subtitle == "Le 3G‑SDI" and result.spoken == "Le trois-gé èss-dé-i"


def test_language_and_character_scope():
    entries = [_entry("SDI", "général"), _entry("SDI", "voltix", character_id="c1"), _entry("SDI", "anglais", language="en")]
    assert render("SDI", applicable(entries, "fr-FR")).spoken == "général"
    assert render("SDI", applicable(entries, "fr-FR", "c1")).spoken == "voltix"
    assert render("SDI", applicable(entries, "en-US")).spoken == "anglais"
    assert pronunciation.language_matches("fr", "fr-FR")
    assert not pronunciation.language_matches("fr-BE", "fr-FR")


# -- contrôle éditorial ------------------------------------------------------------------------------------
def test_audit_blocks_on_pending_entries_and_warns_on_uncovered_and_overloaded_acronyms():
    entries = [_entry("SDI", "èss-dé-i"), _entry("HDMI", "x", validated_at="")]
    result = audit("Le SDI, l'HDMI, le BNC, la 4K et le 12G-SDI.", entries, "fr-FR")
    assert result.pending == ["HDMI"] and result.blocking
    assert result.uncovered == ["BNC", "4K", "12G-SDI"]
    assert result.overloaded
    messages = result.messages()
    assert messages[0][1] is True and "HDMI" in messages[0][0]
    assert all(not blocking for _, blocking in messages[1:])


def test_audit_of_a_plain_text_is_clean():
    result = audit("Un câble relie la caméra à la régie.", [], "fr-FR")
    assert result.messages() == [] and not result.blocking


def test_candidate_readings_are_suggestions_only():
    assert pronunciation.candidate_readings("SDI") == ["èss-dé-i", "èssdéi"]
    assert pronunciation.candidate_readings("3G-SDI")[0] == "3-gé èss-dé-i"


# -- dépôt ------------------------------------------------------------------------------------------------
@pytest.fixture
def repos(tmp_path):
    path = tmp_path / "lody.sqlite3"
    projects = ProjectRepository(path)
    project = projects.create(**{**copy.deepcopy(DEFAULTS), "name": "Voltix", "language": "fr-FR",
                                 "text_provider": "openai", "visual_provider": "openai_image",
                                 "voice_provider": "elevenlabs", "voice_name": "Voix projet", "music_provider": "none",
                                 "settings": {"brief": {"voice_id": "voix-projet", "voice_model": "eleven_multilingual_v2"}}})
    other = projects.create(**{**copy.deepcopy(DEFAULTS), "name": "Autre", "language": "fr-FR",
                               "text_provider": "openai", "visual_provider": "openai_image",
                               "voice_provider": "elevenlabs", "voice_name": "", "music_provider": "none"})
    return path, project, other, PronunciationRepository(path)


def test_migration_is_additive_and_idempotent(repos):
    path, *_ = repos
    connection = sqlite3.connect(path)
    db.migrate(connection)
    db.migrate(connection)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION >= 10
    assert connection.execute("SELECT COUNT(*) FROM pronunciation_lexicon").fetchone()[0] == 0


def test_crud_validation_and_reset_after_change(repos):
    _, project, other, repo = repos
    entry = repo.create(project.id, written="SDI", spoken="èss-dé-i")
    assert not entry.validated
    entry = repo.mark_validated(project.id, entry.id, "Voix projet (eleven_multilingual_v2)")
    assert entry.validated and entry.validated_with.startswith("Voix projet")
    assert repo.update(project.id, entry.id, notes="testé au casque").validated  # une note ne change pas l'écoute
    assert not repo.update(project.id, entry.id, spoken="ès-dé-i").validated     # nouvelle lecture : réécouter
    with pytest.raises(PronunciationValidationError):
        repo.create(project.id, written="SDI", spoken="autre")                   # doublon même portée
    repo.create(project.id, written="SDI", spoken="ess di", language="en")       # autre langue : permis
    with pytest.raises(pronunciation.EntryNotFound):
        repo.get(other.id, entry.id)                                             # isolation entre projets
    assert repo.list_for_project(other.id) == []
    repo.delete(project.id, entry.id)
    assert [item.language for item in repo.list_for_project(project.id)] == ["en"]


@pytest.mark.parametrize("fields, key", [
    ({"written": "", "spoken": "x"}, "written"),
    ({"written": "SDI", "spoken": ""}, "spoken"),
    ({"written": "SDI", "spoken": "[slow] èss"}, "spoken"),
    ({"written": "SDI", "spoken": "x", "language": "français"}, "language"),
])
def test_invalid_fields_are_refused_with_a_french_message(repos, fields, key):
    _, project, _, repo = repos
    with pytest.raises(PronunciationValidationError) as error:
        repo.create(project.id, **fields)
    assert key in error.value.errors


# -- production : instantané, blocage, payload ---------------------------------------------------------------
@pytest.fixture
def production_env(tmp_path, repos):
    path, project, _, repo = repos
    connector = ScriptedConnector(tmp_path / "storage")
    service = ProductionService(ProductionRepository(path), {"scripted": connector}, SyncExecutor(),
                                price_book=lambda: PRICES, character_repo=CharacterRepository(path),
                                location_repo=LocationRepository(path), pronunciation_repo=repo)
    return service, connector, project, repo


SCRIPT = "Le câble SDI transporte l'image de la caméra jusqu'à la régie. C'est simple et solide."


def test_validated_reading_goes_to_the_voice_and_the_script_stays_exact(production_env):
    service, connector, project, repo = production_env
    entry = repo.create(project.id, written="SDI", spoken="èss-dé-i")
    repo.mark_validated(project.id, entry.id, "Voix projet")
    production = service.prepare(project, "Le câble SDI", provider_id="scripted", script=SCRIPT)
    assert production.snapshot["pronunciation"]["entries"][0]["spoken"] == "èss-dé-i"
    repo.update(project.id, entry.id, spoken="changé après coup")  # jamais relu : l'instantané est figé
    service.confirm(production.id, accept_partial=True)
    sent = connector.submitted[-1]
    assert sent.spoken_script.startswith("Le câble èss-dé-i transporte")
    assert "SDI" in sent.script and sent.subtitle_script == ""
    payload = mpt.build_payload(sent, EngineFacts())
    assert payload["video_script"] == sent.script
    assert payload["video_script_spoken"] == sent.spoken_script
    done = service.repo.get(production.id)
    assert done.script == sent.script and "èss" not in done.script


def test_pending_expression_blocks_the_launch_before_any_call(production_env):
    service, connector, project, repo = production_env
    repo.create(project.id, written="SDI", spoken="èss-dé-i")  # jamais écoutée
    production = service.prepare(project, "Le câble SDI", provider_id="scripted", script=SCRIPT)
    with pytest.raises(LaunchError) as error:
        service.confirm(production.id, accept_partial=True)
    assert "SDI" in error.value.message and error.value.issues[0].code == "pronunciation_pending"
    assert connector.submitted == [] and "submit" not in connector.calls
    assert service.repo.get(production.id).status is ProductionStatus.EN_ATTENTE_CONFIRMATION


def test_generated_script_with_pending_expression_fails_before_submission(production_env):
    service, connector, project, repo = production_env
    repo.create(project.id, written="SDI", spoken="èss-dé-i")
    connector.script_text = SCRIPT  # le texte écrit par le moteur emploie le sigle non validé
    production = service.prepare(project, "Le câble SDI", provider_id="scripted")
    service.confirm(production.id, accept_partial=True)
    failed = service.repo.get(production.id)
    assert failed.status is ProductionStatus.ECHEC and "SDI" in failed.error_message
    assert connector.submitted == []


def test_without_lexicon_request_snapshot_and_payload_are_unchanged(production_env):
    service, connector, project, _ = production_env
    production = service.prepare(project, "Le câble SDI", provider_id="scripted", script=SCRIPT)
    assert "pronunciation" not in production.snapshot
    service.confirm(production.id, accept_partial=True)
    sent = connector.submitted[-1]
    assert sent.spoken_script == "" and "spoken_script" not in sent.to_dict()
    payload = mpt.build_payload(sent, EngineFacts())
    assert "video_script_spoken" not in payload and payload["video_script"] == sent.script


def test_snapshot_version_does_not_depend_on_the_lexicon(repos):
    _, project, _, _ = repos
    request = build_request(project, "Sujet", SCRIPT)
    plain = make_snapshot(project, request, "t")
    with_lexicon = make_snapshot(project, request, "t", pronunciation_lexicon={"entries": [], "pending": ["SDI"]})
    assert plain["version"] == with_lexicon["version"] and "pronunciation" not in plain


def test_request_round_trip_keeps_spoken_fields():
    request = GenerationRequest(subject="s", script="SDI", spoken_script="èss-dé-i")
    assert GenerationRequest.from_dict(request.to_dict()).spoken_script == "èss-dé-i"


# -- interface : voix d'écoute ----------------------------------------------------------------------------
def test_preview_voices_use_the_real_project_and_character_voices(tmp_path, repos):
    path, project, _, _ = repos
    characters = CharacterRepository(path)
    voltix = characters.create(project.id, name="Voltix", voice_provider="elevenlabs",
                               external_voice_id="21m00Tcm4TlvDq8ikWAM", voice_name="Voix Voltix")
    characters.set_voice_direction(project.id, voltix.id, {"model_id": "eleven_v3", "stability": 0.3,
                                                           "similarity_boost": 0.75, "style": 0.35,
                                                           "use_speaker_boost": True, "speed": 1.0})
    voices = preview_voices(project, characters.list_for_project(project.id))
    assert [voice.voice_id for voice in voices] == ["voix-projet", "21m00Tcm4TlvDq8ikWAM"]
    assert voices[0].settings["model_id"] == "eleven_multilingual_v2"
    assert voices[1].settings["model_id"] == "eleven_v3" and voices[1].settings["stability"] == 0.3
