"""Templates de production (#110, PR A) : modèle, validation adossée au brief, CRUD, isolation.

Aucun Streamlit, aucun réseau, aucun fournisseur : couche pure.
"""

import json
import sqlite3

import pytest

from lody import brief as brief_lib
from lody.projects import ProjectRepository
from lody.templates import (
    NAME_MAX,
    NOTE_KEYS,
    NOTE_MAX,
    OVERLAY_KEYS,
    ProductionTemplate,
    TemplateNotFound,
    TemplateRepository,
    TemplateValidationError,
    validate_payload,
)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "lody.sqlite3"


@pytest.fixture
def projects(db_path):
    return ProjectRepository(db_path)


@pytest.fixture
def project(projects):
    return projects.create(name="Projet hôte")


@pytest.fixture
def other_project(projects):
    return projects.create(name="Autre projet")


@pytest.fixture
def repo(db_path, projects):
    return TemplateRepository(db_path)


PEDAGOGIQUE = {
    "duration_min": 40,
    "duration_max": 50,
    "narration_pace": "rapide",
    "structure": ["Hook en 2 secondes", "Notion expliquée", "Exemple concret", "Récap final"],
    "scenes_per_minute_min": 7,
    "scenes_per_minute_max": 9,
    "hook_notes": "Commencer par une question concrète, jamais par une définition.",
}


# -- le payload est un recouvrement PARTIEL ---------------------------------------------------------------
def test_an_absent_key_means_inherit_from_the_project(repo, project):
    """Cœur du modèle : le template ne porte QUE ce qu'il impose. Sinon chaque défaut du brief deviendrait
    une valeur imposée, et on ne pourrait plus distinguer « hérité » de « voulu »."""
    template = repo.create(project.id, name="Minimal", payload={"narration_pace": "calme"})
    assert template.payload == {"narration_pace": "calme"}
    assert template.overlay() == {"narration_pace": "calme"}
    assert "duration_min" not in template.payload  # jamais comblé par un défaut du brief


def test_a_full_recipe_keeps_exactly_the_declared_keys(repo, project):
    template = repo.create(project.id, name="Short pédagogique", description="45 s, 4 étapes",
                           payload=dict(PEDAGOGIQUE))
    assert set(template.payload) == set(PEDAGOGIQUE)
    assert template.payload["structure"] == PEDAGOGIQUE["structure"]
    assert repo.get(project.id, template.id) == template  # relu depuis SQLite à l'identique


def test_the_short_pedagogique_template_applies_over_a_project_brief(repo, project, projects):
    """Test de template « Short pédagogique » exigé par le ticket : son recouvrement se superpose au brief
    du projet, et les clés non déclarées restent celles du projet."""
    projects.update(project.id, settings={"brief": {"audience": "Débutants", "narration_pace": "normal",
                                                    "duration_min": 20, "duration_max": 30}})
    template = repo.create(project.id, name="Short pédagogique", payload=dict(PEDAGOGIQUE))
    effective = {**brief_lib.brief_settings(projects.get(project.id).settings), **template.overlay()}
    assert (effective["duration_min"], effective["duration_max"]) == (40, 50)  # imposé par le template
    assert effective["narration_pace"] == "rapide"                             # imposé par le template
    assert effective["audience"] == "Débutants"                                # hérité du projet
    assert brief_lib.brief_settings(projects.get(project.id).settings)["duration_min"] == 20  # projet intact


# -- validation : les limites viennent du brief, jamais d'un second vocabulaire ---------------------------
def test_overlay_keys_are_a_subset_of_the_brief(repo):
    """Garde-fou anti-divergence : une clé de recouvrement qui n'existe plus dans le brief doit casser ce
    test, pas produire un template silencieusement inapplicable."""
    assert set(OVERLAY_KEYS) <= set(brief_lib.DEFAULT_BRIEF)


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"narration_pace": "endiablé"}, "payload.narration_pace"),
        ({"duration_min": 1, "duration_max": 5}, "payload.duration_min"),
        ({"duration_min": 60, "duration_max": 20}, "payload.duration_min"),
        ({"scenes_per_minute_min": 0, "scenes_per_minute_max": 99}, "payload.scenes_per_minute_min"),
        ({"structure": ["x" * 200]}, "payload.structure"),
        ({"visual_rules": "x" * 5000}, "payload.visual_rules"),
    ],
)
def test_invalid_overlay_is_refused_with_the_brief_message(payload, field):
    with pytest.raises(TemplateValidationError) as error:
        validate_payload(payload)
    assert field in error.value.errors, error.value.errors


def test_the_duration_limits_come_from_the_brief_constants():
    """Même borne que le brief : si brief.DURATION_LIMITS bouge, le template suit sans retouche ici."""
    low, high = brief_lib.DURATION_LIMITS
    assert validate_payload({"duration_min": low, "duration_max": high})["duration_max"] == high
    with pytest.raises(TemplateValidationError):
        validate_payload({"duration_min": low, "duration_max": high + 1})


def test_a_range_bound_declared_alone_is_refused():
    """Une borne seule se comparerait à un défaut du brief, pas à l'intention de l'utilisateur."""
    with pytest.raises(TemplateValidationError) as error:
        validate_payload({"duration_min": 40})
    assert "payload.duration_min" in error.value.errors
    assert "ensemble" in error.value.errors["payload.duration_min"]


def test_an_unknown_setting_is_refused_not_ignored(repo, project):
    with pytest.raises(TemplateValidationError) as error:
        repo.create(project.id, name="Bizarre", payload={"video_codec": "h265"})
    assert "video_codec" in error.value.errors["payload"]


def test_a_payload_that_is_not_a_mapping_is_refused():
    for bad in ([], "texte", 3, None):
        with pytest.raises(TemplateValidationError):
            validate_payload(bad)


# -- champs propres au template, repliés dans les consignes à l'application -------------------------------
def test_the_template_only_notes_are_stored_and_exposed_separately(repo, project):
    template = repo.create(project.id, name="Avec consignes", payload={
        "hook_notes": "Question concrète.", "subtitle_notes": "Deux lignes maximum.",
        "music_notes": "Nappe discrète, jamais de percussions.",
    })
    assert template.notes() == {
        "hook_notes": "Question concrète.", "subtitle_notes": "Deux lignes maximum.",
        "music_notes": "Nappe discrète, jamais de percussions.",
    }
    assert template.overlay() == {}  # ce ne sont pas des clés de brief


@pytest.mark.parametrize("key", NOTE_KEYS)
def test_a_note_too_long_is_refused(key):
    assert validate_payload({key: "x" * NOTE_MAX})[key]
    with pytest.raises(TemplateValidationError) as error:
        validate_payload({key: "x" * (NOTE_MAX + 1)})
    assert f"payload.{key}" in error.value.errors


@pytest.mark.parametrize("key", NOTE_KEYS)
def test_an_empty_note_is_not_stored(key):
    assert validate_payload({key: "   "}) == {}  # vide = rien à replier, pas une consigne vide


# -- nom, description, secrets ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("name", "expected"),
    [("", "Donne un nom"), ("   ", "Donne un nom"), ("a", "au moins 2"), ("x" * (NAME_MAX + 1), "trop long")],
)
def test_name_validation_messages_are_human(repo, project, name, expected):
    with pytest.raises(TemplateValidationError) as error:
        repo.create(project.id, name=name)
    assert expected in error.value.errors["name"]
    assert repo.count_for_project(project.id) == 0


def test_duplicate_name_in_the_same_project_is_refused_case_insensitively(repo, project):
    repo.create(project.id, name="Short pédagogique")
    with pytest.raises(TemplateValidationError) as error:
        repo.create(project.id, name="SHORT PÉDAGOGIQUE")
    assert "porte déjà ce nom" in error.value.errors["name"]


def test_the_same_name_is_free_in_another_project(repo, project, other_project):
    repo.create(project.id, name="Short pédagogique")
    assert repo.create(other_project.id, name="Short pédagogique").project_id == other_project.id


def test_a_secret_in_the_payload_is_refused(repo, project):
    with pytest.raises(TemplateValidationError) as error:
        repo.create(project.id, name="Fuite", payload={"hook_notes": "sk-" + "a" * 40})
    assert "payload" in error.value.errors


# -- CRUD et isolation stricte entre projets --------------------------------------------------------------
def test_update_changes_only_the_given_fields(repo, project):
    template = repo.create(project.id, name="Recette", payload=dict(PEDAGOGIQUE))
    updated = repo.update(project.id, template.id, name="Recette v2")
    assert updated.name == "Recette v2"
    assert updated.payload == template.payload  # payload inchangé : pas de remise à zéro implicite
    assert updated.updated_at >= template.updated_at


def test_update_refuses_an_unknown_field(repo, project):
    """Seuls EDITABLE_FIELDS passent : ni created_at, ni project_id, ni une colonne inventée — une mise à
    jour ne doit jamais pouvoir déplacer un template vers un autre projet ni réécrire ses horodatages."""
    template = repo.create(project.id, name="Recette")
    with pytest.raises(TemplateValidationError) as error:
        repo.update(project.id, template.id, created_at="2000-01-01")
    assert "Champ inconnu" in error.value.errors["_"]


def test_deactivate_hides_the_template_without_deleting_it(repo, project):
    template = repo.create(project.id, name="Ancienne recette")
    repo.deactivate(project.id, template.id)
    assert repo.list_for_project(project.id, include_inactive=False) == []
    assert repo.count_for_project(project.id) == 1  # toujours là : les productions restent reproductibles
    assert repo.restore(project.id, template.id).is_active is True


def test_a_template_of_another_project_is_never_reachable(repo, project, other_project):
    template = repo.create(project.id, name="Privé")
    for call in (lambda: repo.get(other_project.id, template.id),
                 lambda: repo.update(other_project.id, template.id, name="Vol"),
                 lambda: repo.deactivate(other_project.id, template.id)):
        with pytest.raises(TemplateNotFound):
            call()
    assert repo.get(project.id, template.id).name == "Privé"  # intact


def test_creating_a_template_on_a_missing_project_is_refused(repo):
    with pytest.raises(TemplateValidationError) as error:
        repo.create("prj_inexistant", name="Orphelin")
    assert "project_id" in error.value.errors


def test_templates_are_listed_active_first_then_by_name(repo, project):
    repo.create(project.id, name="Zoulou")
    repo.create(project.id, name="Alpha")
    retired = repo.create(project.id, name="Bravo")
    repo.deactivate(project.id, retired.id)
    assert [t.name for t in repo.list_for_project(project.id)] == ["Alpha", "Zoulou", "Bravo"]


# -- duplication explicite : seule passerelle entre projets ------------------------------------------------
def test_duplicate_to_another_project_makes_an_independent_copy(repo, project, other_project):
    source = repo.create(project.id, name="Short pédagogique", description="45 s",
                         payload=dict(PEDAGOGIQUE))
    copy_ = repo.duplicate_to_project(project.id, source.id, other_project.id)
    assert copy_.project_id == other_project.id and copy_.id != source.id
    assert copy_.payload == source.payload and copy_.description == source.description
    # indépendance : modifier la copie ne touche pas l'original
    repo.update(other_project.id, copy_.id, payload={"narration_pace": "calme"})
    assert repo.get(project.id, source.id).payload == source.payload


def test_duplicate_into_a_project_that_already_has_the_name_suffixes_it(repo, project, other_project):
    """Une collision que l'utilisateur n'a pas provoquée ne doit pas faire échouer la duplication."""
    source = repo.create(project.id, name="Short pédagogique")
    repo.create(other_project.id, name="Short pédagogique")
    assert repo.duplicate_to_project(project.id, source.id, other_project.id).name == "Short pédagogique (2)"


def test_duplicate_within_the_same_project_under_a_new_name(repo, project):
    source = repo.create(project.id, name="Base", payload={"narration_pace": "rapide"})
    variant = repo.duplicate_to_project(project.id, source.id, project.id, name="Base — variante")
    assert variant.name == "Base — variante" and variant.payload == source.payload


# -- schéma et rétrocompatibilité -------------------------------------------------------------------------
def test_the_migration_adds_the_table_and_the_production_columns(db_path, projects):
    TemplateRepository(db_path)
    with sqlite3.connect(db_path) as connection:
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "production_templates" in tables
        columns = {r[1] for r in connection.execute("PRAGMA table_info(productions)")}
        assert {"template_id", "template_snapshot"} <= columns


def test_the_production_dataclass_covers_every_column_of_the_table(db_path, projects):
    """Garde-fou : ``ProductionRepository._from_row`` construit ``Production(**dict(row))``, donc TOUTE
    colonne ajoutée à ``productions`` doit exister comme champ de la dataclass — sinon chaque lecture de
    production lève ``TypeError``. C'est exactement ce qui a cassé en ajoutant la migration v8 : la table
    avait deux colonnes de plus que le modèle, et 248 tests sont tombés d'un coup.
    """
    import dataclasses

    from lody.generation.store import Production, ProductionRepository

    ProductionRepository(db_path)
    with sqlite3.connect(db_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(productions)")}
    missing = columns - {f.name for f in dataclasses.fields(Production)}
    assert not missing, f"colonnes absentes de la dataclass Production : {sorted(missing)}"


def test_the_template_columns_are_decoded_as_json_by_the_store(db_path, projects, project):
    """``template_snapshot`` doit être décodé comme les autres colonnes JSON, pas renvoyé en texte brut."""
    from lody.generation.models import ProductionStatus
    from lody.generation.store import ProductionRepository

    repo = ProductionRepository(db_path)
    production = repo.create(project_id=project.id, subject="Sujet", provider="mpt",
                             status=ProductionStatus.BROUILLON)
    assert production.template_id is None          # aucune production n'est liée à un template en PR A
    assert production.template_snapshot == {}      # dict, jamais la chaîne "{}"


def test_existing_productions_keep_a_neutral_template_snapshot(db_path, projects, project):
    """Rétrocompatibilité : une production sans template reste le cas normal — template_id NULL et
    snapshot vide, aucune ligne réécrite par la migration."""
    TemplateRepository(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute(
            "INSERT INTO productions (id, project_id, root_production_id, subject, provider, "
            "idempotency_key, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("prd_sans_template", project.id, "prd_sans_template", "Sujet", "mpt",
             "idem-sans-template", "BROUILLON", "2026-01-01", "2026-01-01"),
        )
        row = connection.execute(
            "SELECT template_id, template_snapshot FROM productions WHERE id = 'prd_sans_template'"
        ).fetchone()
    assert row["template_id"] is None
    assert json.loads(row["template_snapshot"]) == {}


def test_a_template_row_written_with_a_broken_payload_is_read_as_empty(db_path, projects, project):
    """Robustesse de lecture : une ligne écrite hors de cette couche ne doit pas faire planter la liste —
    on n'invente pas un contenu, on renvoie un template sans recouvrement."""
    repo = TemplateRepository(db_path)
    template = repo.create(project.id, name="Cassé")
    with sqlite3.connect(db_path) as connection:
        connection.execute("UPDATE production_templates SET payload = ? WHERE id = ?",
                           ("{pas du json", template.id))
    assert repo.get(project.id, template.id).payload == {}


def test_the_dataclass_exposes_overlay_and_notes_separately():
    template = ProductionTemplate(id="tpl_x", project_id="prj_x", name="T", description="",
                                  payload={"narration_pace": "calme", "hook_notes": "Accroche."})
    assert template.overlay() == {"narration_pace": "calme"}
    assert template.notes() == {"hook_notes": "Accroche."}
