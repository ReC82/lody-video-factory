"""Paramètres de production et brief : fonctions pures, sans Streamlit ni réseau."""

import pytest

from lody import brief
from lody.projects import ProjectRepository, ProjectValidationError
from lody.seeds import SEED_PROJECTS


@pytest.fixture
def repo(tmp_path):
    repository = ProjectRepository(tmp_path / "lody.sqlite3")
    repository.seed_defaults(SEED_PROJECTS)
    return repository


def _project(repo, name):
    return next(p for p in repo.list_projects() if p.name == name)


def test_lodycrypto_seed_matches_the_requested_configuration(repo):
    crypto = _project(repo, "LodyCrypto")
    settings = brief.brief_settings(crypto.settings)
    assert crypto.language == "fr-FR" and crypto.format == "9:16"
    assert (settings["duration_min"], settings["duration_max"]) == (45, 60)
    assert settings["audience"] == "Débutants complets en crypto"
    assert settings["orientation"] == "Progression rapide vers le Web3 gaming"
    assert crypto.tone == "Simple, honnête, dynamique, sans posture d’expert"
    assert crypto.voice_name == "Kev - Young, Dynamic and Bright" and crypto.voice_provider == "elevenlabs"
    assert settings["narration_pace"] == "rapide"
    assert (settings["scenes_per_minute_min"], settings["scenes_per_minute_max"]) == (8, 10)
    assert "sans texte, sans logo, sans marque" in crypto.visual_style
    assert "cyan et bleu électrique" in crypto.visual_style
    assert settings["structure"] == ["Accroche", "Explication simple", "Exemple concret",
                                     "Erreur ou piège à éviter", "Idée à retenir", "Annonce de l’épisode suivant"]


def test_estimates_are_derived_from_pace_and_scene_rate(repo):
    settings = brief.brief_settings(_project(repo, "LodyCrypto").settings)
    assert brief.estimates(settings) == {"words": (131, 175), "scenes": (6, 10)}
    slow = {**settings, "narration_pace": "calme"}
    assert brief.estimates(slow)["words"] == (98, 130)


def test_build_brief_merges_request_and_project_settings(repo):
    crypto = _project(repo, "LodyCrypto")
    built = brief.build_brief(crypto, "  Explique le minage.  ")
    assert built["demande"] == "Explique le minage."
    assert built["duree_cible"] == "45 à 60 secondes"
    assert built["visuels"]["rythme"] == "8 à 10 scènes par minute"
    assert built["narration"] == "Rapide et énergique"
    assert built["reperes"] == {"mots": "131 à 175 mots", "scenes": "6 à 10 scènes"}
    assert built["voix"]["identifiant"] == "jGpnMdbhtKgQbVrYezOx"
    text = brief.brief_to_text(built)
    assert "BRIEF DE PRODUCTION — LodyCrypto" in text and "1. Accroche" in text and "6. Annonce" in text
    assert "aucune promesse de gain" in text


def test_brief_for_a_project_without_request_is_a_placeholder(repo):
    assert "(à saisir)" in brief.brief_to_text(brief.build_brief(_project(repo, "LodyCrypto")))


def test_audiovisuel_without_brief_block_falls_back_to_legacy_fields(repo):
    audio = _project(repo, "Audiovisuel")
    assert "brief" not in audio.settings
    settings = brief.brief_settings(audio.settings)
    assert (settings["duration_min"], settings["duration_max"]) == (45, 65)
    assert settings["voice_id"] == "jGpnMdbhtKgQbVrYezOx"


@pytest.mark.parametrize(
    ("raw", "field"),
    [
        ({"duration_min": 5}, "brief.duration_min"),
        ({"duration_min": 60, "duration_max": 45}, "brief.duration_min"),
        ({"duration_max": 1000}, "brief.duration_min"),
        ({"scenes_per_minute_min": 0}, "brief.scenes_per_minute_min"),
        ({"narration_pace": "turbo"}, "brief.narration_pace"),
        ({"voice_id": "pas valide !"}, "brief.voice_id"),
        ({"voice_model": "?!"}, "brief.voice_model"),
        ({"audience": "x" * 201}, "brief.audience"),
        ({"structure": [f"étape {i}" for i in range(13)]}, "brief.structure"),
        ({"structure": ["x" * 121]}, "brief.structure"),
        ({"standing_instructions": "x" * 2001}, "brief.standing_instructions"),
    ],
)
def test_invalid_production_settings_are_reported_per_field(raw, field):
    _, errors = brief.validate_brief(raw)
    assert field in errors


def test_repository_refuses_invalid_brief_and_secrets_in_it(repo):
    crypto = _project(repo, "LodyCrypto")
    with pytest.raises(ProjectValidationError) as error:
        repo.update(crypto.id, settings={**crypto.settings, "brief": {"duration_min": 90, "duration_max": 45}})
    assert "brief.duration_min" in error.value.errors
    with pytest.raises(ProjectValidationError) as error:
        repo.update(crypto.id, settings={**crypto.settings, "brief": {
            **crypto.settings["brief"], "standing_instructions": "clé sk-abcdefghijklmnopqrstuvwxyz123456"}})
    assert "clé secrète" in " ".join(error.value.errors.values())
    assert repo.get(crypto.id).settings["brief"]["duration_max"] == 60


def test_settings_are_stored_separately_per_project(repo):
    crypto, audio = _project(repo, "LodyCrypto"), _project(repo, "Audiovisuel")
    repo.update(crypto.id, settings={**crypto.settings, "brief": {**crypto.settings["brief"], "audience": "Autre"}})
    assert brief.brief_settings(repo.get(crypto.id).settings)["audience"] == "Autre"
    assert repo.get(audio.id).settings == audio.settings


def test_brief_text_never_contains_provider_secrets(repo):
    text = brief.brief_to_text(brief.build_brief(_project(repo, "LodyCrypto"), "Demande"))
    assert "api_key" not in text.lower() and "sk-" not in text
