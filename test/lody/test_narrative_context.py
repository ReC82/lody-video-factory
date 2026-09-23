"""Snapshot immuable des personnages et du lieu sélectionnés (epic #29, ticket #35). Aucun appel réel."""

from __future__ import annotations

import copy
from decimal import Decimal

import pytest

from lody.characters import CharacterRepository
from lody.generation.costing import PriceBook
from lody.generation.narrative_context import NarrativeContextError, resolve_narrative_context
from lody.generation.service import LaunchError, ProductionService, make_snapshot
from lody.generation.store import ProductionRepository
from lody.locations import LocationRepository
from lody.projects import DEFAULTS, ProjectRepository
from test.lody.fakes import ScriptedConnector, SyncExecutor

PRICES = PriceBook("EUR", {("text", "openai"): Decimal("0.02"), ("visual", "openai_image"): Decimal("0.04"),
                           ("voice", "elevenlabs"): Decimal("0.3"), ("music", "elevenlabs"): Decimal("0.1")})
SUBJECT = "Explique simplement comment fonctionne une pile solaire à quelqu'un qui débute."


class Env:
    """Un projet, ses personnages/lieux, et un service de production câblé (ou non) sur leurs dépôts."""

    def __init__(self, tmp_path, wire_repos: bool = True):
        self.path = tmp_path / "lody.sqlite3"
        self.projects = ProjectRepository(self.path)
        self.characters = CharacterRepository(self.path)
        self.locations = LocationRepository(self.path)
        fields = {**copy.deepcopy(DEFAULTS), "name": "Projet narratif", "language": "fr-FR",
                  "text_provider": "openai", "visual_provider": "openai_image",
                  "voice_provider": "elevenlabs", "voice_name": "Voix Test", "music_provider": "elevenlabs"}
        self.project = self.projects.create(**fields)
        self.connector = ScriptedConnector(tmp_path / "storage")
        self.service = ProductionService(
            ProductionRepository(self.path), {"scripted": self.connector}, SyncExecutor(), price_book=lambda: PRICES,
            character_repo=self.characters if wire_repos else None,
            location_repo=self.locations if wire_repos else None,
        )

    def add_character(self, **overrides):
        fields = {"name": "Léa", "role": "Guide", "personality": "Curieuse", **overrides}
        return self.characters.create(self.project.id, **fields)

    def add_location(self, **overrides):
        fields = {"name": "Atelier solaire", "location_type": "Intérieur", **overrides}
        return self.locations.create(self.project.id, **fields)


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


# -- resolve_narrative_context (fonction pure) --------------------------------------------------------------------
def test_no_selection_resolves_to_an_empty_block(env):
    assert resolve_narrative_context(env.project.id, (), None, env.characters, env.locations) == {}


def test_selected_characters_and_location_are_copied_by_value(env):
    lea = env.add_character(name="Léa", visual_description="Manteau bleu", continuity_notes="Toujours souriante")
    theo = env.add_character(name="Théo", role="Sceptique")
    atelier = env.add_location(name="Atelier solaire", description="Plein de panneaux")

    block = resolve_narrative_context(env.project.id, (lea.id, theo.id), atelier.id, env.characters, env.locations)

    assert block["version"] == 2  # #37 : ajoute reference_prompt (absent de la version 1, voir son docstring)
    assert [c["id"] for c in block["characters"]] == [lea.id, theo.id]
    assert block["characters"][0] == {
        "id": lea.id, "name": "Léa", "role": "Guide", "personality": "Curieuse",
        "visual_description": "Manteau bleu", "reference_prompt": "", "speech_style": "", "permanent_elements": "",
        "continuity_notes": "Toujours souriante", "is_primary": False,
    }
    assert block["location"] == {
        "id": atelier.id, "name": "Atelier solaire", "location_type": "Intérieur",
        "description": "Plein de panneaux", "reference_prompt": "", "continuity_notes": "", "is_primary": False,
    }


def test_zero_location_is_none_not_an_empty_object(env):
    lea = env.add_character()
    block = resolve_narrative_context(env.project.id, (lea.id,), None, env.characters, env.locations)
    assert block["location"] is None


def test_repeated_character_id_is_deduplicated(env):
    lea = env.add_character()
    block = resolve_narrative_context(env.project.id, (lea.id, lea.id, lea.id), None, env.characters, env.locations)
    assert len(block["characters"]) == 1


def test_unknown_character_id_is_refused(env):
    with pytest.raises(NarrativeContextError):
        resolve_narrative_context(env.project.id, ("chr_does_not_exist",), None, env.characters, env.locations)


def test_unknown_location_id_is_refused(env):
    with pytest.raises(NarrativeContextError):
        resolve_narrative_context(env.project.id, (), "loc_does_not_exist", env.characters, env.locations)


def test_character_belonging_to_another_project_is_refused(env, tmp_path):
    other_project = env.projects.create(**{**copy.deepcopy(DEFAULTS), "name": "Autre projet", "language": "fr-FR",
                                            "text_provider": "openai", "visual_provider": "openai_image",
                                            "voice_provider": "elevenlabs", "voice_name": "V", "music_provider": "elevenlabs"})
    foreign = env.characters.create(other_project.id, name="Intrus")
    with pytest.raises(NarrativeContextError):
        resolve_narrative_context(env.project.id, (foreign.id,), None, env.characters, env.locations)


def test_location_belonging_to_another_project_is_refused(env):
    other_project = env.projects.create(**{**copy.deepcopy(DEFAULTS), "name": "Autre projet 2", "language": "fr-FR",
                                            "text_provider": "openai", "visual_provider": "openai_image",
                                            "voice_provider": "elevenlabs", "voice_name": "V", "music_provider": "elevenlabs"})
    foreign = env.locations.create(other_project.id, name="Ailleurs")
    with pytest.raises(NarrativeContextError):
        resolve_narrative_context(env.project.id, (), foreign.id, env.characters, env.locations)


def test_inactive_character_cannot_be_selected(env):
    lea = env.add_character()
    env.characters.deactivate(env.project.id, lea.id)
    with pytest.raises(NarrativeContextError):
        resolve_narrative_context(env.project.id, (lea.id,), None, env.characters, env.locations)


def test_inactive_location_cannot_be_selected(env):
    atelier = env.add_location()
    env.locations.deactivate(env.project.id, atelier.id)
    with pytest.raises(NarrativeContextError):
        resolve_narrative_context(env.project.id, (), atelier.id, env.characters, env.locations)


# -- make_snapshot -------------------------------------------------------------------------------------------------
def test_make_snapshot_without_narrative_context_matches_pre_35_shape_plus_an_empty_block(env):
    from lody.generation.service import build_request

    request = build_request(env.project, SUBJECT)
    snap = make_snapshot(env.project, request, "2026-09-22T00:00:00+00:00")
    assert snap["narrative_context"] == {}
    # tout le reste du snapshot garde exactement la forme d'avant #35
    assert set(snap) == {"version", "captured_at", "inherited_from", "project", "request", "origins", "narrative_context"}


# -- intégration via ProductionService.prepare() -------------------------------------------------------------------
def test_prepare_with_zero_selection_is_functionally_identical_to_before_35(env):
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted")
    assert draft.snapshot["narrative_context"] == {}


def test_prepare_with_a_selection_stores_it_in_the_snapshot_and_never_in_what_is_sent(env):
    import json

    lea = env.add_character(name="Léa")
    atelier = env.add_location(name="Atelier solaire")
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted",
                                character_ids=[lea.id], location_id=atelier.id)
    assert [c["name"] for c in draft.snapshot["narrative_context"]["characters"]] == ["Léa"]
    assert draft.snapshot["narrative_context"]["location"]["name"] == "Atelier solaire"
    # jamais injecté dans ce qui est réellement envoyé au moteur à ce stade (script, voix, prompts d'images)
    assert "narrative_context" not in json.dumps(draft.params)
    assert "Léa" not in json.dumps(draft.params) and "Atelier solaire" not in json.dumps(draft.params)


def test_prepare_refuses_an_inactive_or_foreign_selection(env):
    lea = env.add_character()
    env.characters.deactivate(env.project.id, lea.id)
    with pytest.raises(LaunchError):
        env.service.prepare(env.project, SUBJECT, provider_id="scripted", character_ids=[lea.id])


def test_prepare_without_wired_repositories_refuses_any_selection(tmp_path):
    """Un service construit sans character_repo/location_repo (l'app actuelle avant #35 câblée ailleurs)
    refuse une sélection plutôt que de l'ignorer silencieusement."""
    env = Env(tmp_path, wire_repos=False)
    lea = env.add_character()
    with pytest.raises(LaunchError):
        env.service.prepare(env.project, SUBJECT, provider_id="scripted", character_ids=[lea.id])
    # mais zéro sélection continue de fonctionner exactement comme avant #35
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted")
    assert draft.snapshot["narrative_context"] == {}


def test_editing_or_deactivating_a_character_after_launch_never_changes_the_production(env):
    lea = env.add_character(name="Léa", personality="Curieuse")
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", character_ids=[lea.id])
    launched = env.service.confirm(draft.id, accept_partial=True)
    before = copy.deepcopy(launched.snapshot["narrative_context"])

    env.characters.update(env.project.id, lea.id, personality="Complètement différente")
    env.characters.deactivate(env.project.id, lea.id)

    stored = ProductionRepository(env.path).get(launched.id)
    assert stored.snapshot["narrative_context"] == before
    assert stored.snapshot["narrative_context"]["characters"][0]["personality"] == "Curieuse"


def test_v2_inherits_the_v1_narrative_context_even_after_the_character_changes(env):
    lea = env.add_character(name="Léa")
    v1 = env.service.confirm(
        env.service.prepare(env.project, SUBJECT, provider_id="scripted", character_ids=[lea.id]).id,
        accept_partial=True,
    )
    env.connector.queue(env.connector.done("task-0001"))
    v1 = env.service.refresh(v1.id)
    env.characters.update(env.project.id, lea.id, name="Léa (renommée)")

    v2 = env.service.create_v2(v1.id)
    assert v2.snapshot["narrative_context"] == v1.snapshot["narrative_context"]
    assert v2.snapshot["narrative_context"]["characters"][0]["name"] == "Léa"


def test_legacy_production_snapshot_without_narrative_context_key_is_accepted(env):
    """Productions historiques (#35 pas encore déployé) : la clé est absente, pas vide — doit rester accepté."""
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted")
    legacy_snapshot = copy.deepcopy(draft.snapshot)
    del legacy_snapshot["narrative_context"]
    assert env.service.repo.transition(draft.id, [draft.status], snapshot=legacy_snapshot)

    launched = env.service.confirm(draft.id, accept_partial=True)  # _check_isolation ne regarde pas cette clé
    assert launched.status.value == "EN_FILE" and launched.script  # script généré : create_v2 l'acceptera

    v2 = env.service.create_v2(launched.id)
    assert v2.snapshot.get("narrative_context", {}) == {}
