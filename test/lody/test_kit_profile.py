"""Profil de publication PROPRE à chaque projet : validation, graines, migration additive, réglages. Sans fournisseur."""

from __future__ import annotations

import copy
import json
import sqlite3

import pytest

from lody import brief as brief_lib
from lody import settings
from lody.projects import ProjectRepository
from lody.seeds import ADDITIVE_BRIEF_KEYS, SEED_PROJECTS

apptest = pytest.importorskip("streamlit.testing.v1")
streamlit = pytest.importorskip("streamlit")


@pytest.fixture
def repo(tmp_path):
    repository = ProjectRepository(tmp_path / "lody.sqlite3")
    repository.seed_defaults(SEED_PROJECTS)
    return repository


def _named(repo, name):
    return next(p for p in repo.list_projects() if p.name == name)


def _publication(project):
    return brief_lib.brief_settings(project.settings)["publication"]


def test_seed_projects_have_their_own_publication_profile(repo):
    crypto, audio = _publication(_named(repo, "LodyCrypto")), _publication(_named(repo, "Audiovisuel"))
    assert "#LodyCrypto" in crypto["hashtags"] and crypto["made_for_kids"] is False and crypto["no_financial_claims"] is True
    assert "investissement" in crypto["disclaimer"]
    assert "#LodyCrypto" not in str(audio) and "Web3" not in str(audio)


def test_platform_default_publication_profile_is_neutral():
    assert brief_lib.DEFAULT_BRIEF["publication"] == {}


@pytest.mark.parametrize("field, value", [("hashtags", ["#deux mots"]), ("hashtags", [f"#t{i}" for i in range(11)]),
                                          ("tags", [f"tag{i}" for i in range(21)]), ("playlist", "x" * 101),
                                          ("disclaimer", "y" * 301)])
def test_invalid_publication_values_are_refused_with_a_field_error(field, value):
    _, errors = brief_lib.validate_brief({"publication": {field: value}})
    assert f"brief.publication.{field}" in errors


def test_hashtags_and_tags_are_normalised_from_free_text():
    clean, errors = brief_lib.validate_brief({"publication": {"hashtags": "lody, #Crypto  lody", "tags": "web3 gaming, #NFT\nnft"}})
    assert not errors
    assert clean["publication"]["hashtags"] == ["#lody", "#Crypto"] and clean["publication"]["tags"] == ["web3 gaming", "NFT"]


def test_additive_upgrade_adds_the_profile_to_an_old_project_without_touching_its_own_values(repo):
    crypto = _named(repo, "LodyCrypto")
    stored = copy.deepcopy(crypto.settings)
    del stored[brief_lib.BRIEF_KEY]["publication"]
    stored[brief_lib.BRIEF_KEY]["visual_rules"] = "MA règle personnelle"
    with sqlite3.connect(repo.db_path) as connection:                 # état d'avant le chantier : écrit tel quel, sans validation
        connection.execute("UPDATE projects SET settings = ? WHERE id = ?", (json.dumps(stored), crypto.id))
    assert "publication" not in repo.get(crypto.id).settings[brief_lib.BRIEF_KEY]
    assert repo.upgrade_seeds(SEED_PROJECTS) == 1
    brief = repo.get(crypto.id).settings[brief_lib.BRIEF_KEY]
    assert "#LodyCrypto" in brief["publication"]["hashtags"] and brief["visual_rules"] == "MA règle personnelle"
    assert repo.upgrade_seeds(SEED_PROJECTS) == 0                    # idempotent


def test_a_user_edited_profile_is_never_overwritten_by_the_seed(repo):
    crypto = _named(repo, "LodyCrypto")
    edited = copy.deepcopy(crypto.settings)
    edited[brief_lib.BRIEF_KEY]["publication"] = {"playlist": "Ma playlist"}
    repo.update(crypto.id, settings=edited)
    repo.upgrade_seeds(SEED_PROJECTS)
    assert repo.get(crypto.id).settings[brief_lib.BRIEF_KEY]["publication"] == {"playlist": "Ma playlist"}
    assert "publication" in ADDITIVE_BRIEF_KEYS


def test_settings_page_edits_and_persists_only_this_projects_profile(lody_env):
    streamlit.cache_resource.clear()
    repo = ProjectRepository(settings.db_path())
    repo.seed_defaults(SEED_PROJECTS)
    crypto, audio = _named(repo, "LodyCrypto"), _named(repo, "Audiovisuel")
    audio_before = copy.deepcopy(repo.get(audio.id).settings)
    app = apptest.AppTest.from_file(str(__import__("pathlib").Path(__file__).resolve().parents[2] / "webui" / "Lody.py"), default_timeout=30)
    app.query_params["projet"], app.query_params["vue"] = crypto.id, "parametres"
    app.run()
    assert any(expander.label.startswith("Publication") for expander in app.expander)
    next(w for w in app.text_input if str(w.key).endswith("_pub_playlist")).set_value("Nouvelle playlist")
    next(w for w in app.text_input if str(w.key).endswith("_pub_hashtags")).set_value("#LodyCrypto #Web3")
    next(b for b in app.get("button") if b.label == "Enregistrer les paramètres").click()
    app.run()
    saved = _publication(repo.get(crypto.id))
    assert saved["playlist"] == "Nouvelle playlist" and saved["hashtags"] == ["#LodyCrypto", "#Web3"]
    assert repo.get(audio.id).settings == audio_before
