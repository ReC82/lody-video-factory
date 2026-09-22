"""Migrations, coût, storyboard, dépôt : logique pure, sans réseau."""

from __future__ import annotations

import sqlite3
from decimal import Decimal

import pytest

from lody import db
from lody.generation import costing, storyboard
from lody.generation.models import GenerationRequest, ProductionStatus, VoiceSpec
from lody.generation.provider import estimate_units
from lody.generation.store import ProductionRepository
from lody.projects import ProjectRepository
from lody.seeds import SEED_PROJECTS

CRYPTO = next(seed for seed in SEED_PROJECTS if seed["seed_key"] == "lodycrypto")


def _project(tmp_path):
    repo = ProjectRepository(tmp_path / "lody.sqlite3")
    repo.seed_defaults(SEED_PROJECTS)
    return next(p for p in repo.list_projects() if p.name == "LodyCrypto")


# -- migrations ------------------------------------------------------------------
def test_migrations_create_tables_and_are_idempotent(tmp_path):
    path = tmp_path / "lody.sqlite3"
    ProjectRepository(path)
    ProductionRepository(path)
    ProductionRepository(path)  # rejeu : sans effet
    connection = sqlite3.connect(path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION == 5
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"projects", "productions", "publication_kits", "characters", "locations"} <= tables
    columns = {row[1] for row in connection.execute("PRAGMA table_info(productions)")}
    assert {"project_id", "version", "parent_production_id", "subject", "brief", "script", "storyboard",
            "visual_prompts", "params", "cost_currency", "cost_detail", "confirmed_at", "provider",
            "external_task_id", "status", "progress", "current_step", "error_message", "created_at",
            "started_at", "finished_at", "video_ref", "assets", "snapshot", "trace"} <= columns
    # « idempotency_key » est la clé d'idempotence locale, pas une clé d'API.
    assert not [c for c in columns if ("key" in c or "token" in c or "secret" in c) and c != "idempotency_key"]
    character_columns = {row[1] for row in connection.execute("PRAGMA table_info(characters)")}
    location_columns = {row[1] for row in connection.execute("PRAGMA table_info(locations)")}
    assert {"project_id", "name", "is_primary", "is_active"} <= character_columns
    assert {"project_id", "name", "is_primary", "is_active"} <= location_columns
    # Ni personnages ni lieux ne stockent d'image (aucune colonne BLOB, aucune colonne "image") ni de secret.
    for table_columns in (character_columns, location_columns):
        assert not [c for c in table_columns if "image" in c or "key" in c or "token" in c or "secret" in c]


def test_upgrade_from_schema_v1_keeps_existing_projects(tmp_path):
    path = tmp_path / "old.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(db.PROJECTS_SCHEMA)
    connection.execute("PRAGMA user_version = 1")
    connection.execute(
        "INSERT INTO projects (id, name, created_at, updated_at, language, format, content_type, text_provider,"
        " visual_provider, voice_provider, music_provider) VALUES ('prj_old', 'Ancien', 'x', 'x', 'fr-FR', '9:16',"
        " 'pedagogique', 'openai', 'openai_image', 'elevenlabs', 'none')")
    connection.commit()
    connection.close()
    ProductionRepository(path)
    upgraded = sqlite3.connect(path)
    assert upgraded.execute("PRAGMA user_version").fetchone()[0] == 5
    assert upgraded.execute("SELECT name FROM projects").fetchall() == [("Ancien",)]
    assert upgraded.execute("SELECT COUNT(*) FROM productions").fetchone()[0] == 0


def test_upgrade_from_schema_v2_adds_snapshot_and_trace_without_touching_existing_productions(tmp_path):
    """Base v2 réelle (productions déjà présentes) : les anciennes lignes gardent leurs données, snapshot/trace = {}."""
    path = tmp_path / "v2.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(db.PROJECTS_SCHEMA + db.PRODUCTIONS_SCHEMA)
    connection.execute("PRAGMA user_version = 2")
    connection.execute(
        "INSERT INTO projects (id, name, created_at, updated_at, language, format, content_type, text_provider,"
        " visual_provider, voice_provider, music_provider) VALUES ('prj_a', 'A', 'x', 'x', 'fr-FR', '9:16',"
        " 'pedagogique', 'openai', 'openai_image', 'elevenlabs', 'none')")
    connection.execute(
        "INSERT INTO productions (id, project_id, root_production_id, version, subject, provider, idempotency_key,"
        " status, created_at, updated_at, script) VALUES ('prd_old', 'prj_a', 'prd_old', 1, 'Sujet', 'p', 'k',"
        " 'TERMINEE', 'x', 'x', 'Un script.')")
    connection.commit()
    connection.close()
    repo = ProductionRepository(path)
    ProductionRepository(path)  # rejeu : sans effet, pas de colonne dupliquée
    old = repo.get("prd_old")
    assert (old.script, old.snapshot, old.trace) == ("Un script.", {}, {})
    assert sqlite3.connect(path).execute("PRAGMA user_version").fetchone()[0] == 5


def test_upgrade_from_schema_v4_adds_characters_and_locations_without_touching_existing_kits(tmp_path):
    """Base v4 réelle — le schéma actuellement déployé (projects + productions + publication_kits) — avec un
    projet, une production et un kit de publication déjà présents. La migration additive v5 (#30/#31) doit
    laisser tout cela strictement intact et n'ajouter que characters/locations, vides."""
    path = tmp_path / "v4.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(db.PROJECTS_SCHEMA + db.PRODUCTIONS_SCHEMA)
    db._productions_isolation(connection)
    connection.executescript(db.KITS_SCHEMA)
    connection.execute("PRAGMA user_version = 4")
    connection.execute(
        "INSERT INTO projects (id, name, created_at, updated_at, language, format, content_type, text_provider,"
        " visual_provider, voice_provider, music_provider) VALUES ('prj_a', 'A', 'x', 'x', 'fr-FR', '9:16',"
        " 'pedagogique', 'openai', 'openai_image', 'elevenlabs', 'none')")
    connection.execute(
        "INSERT INTO productions (id, project_id, root_production_id, version, subject, provider, idempotency_key,"
        " status, created_at, updated_at, script) VALUES ('prd_old', 'prj_a', 'prd_old', 1, 'Sujet', 'p', 'k',"
        " 'TERMINEE', 'x', 'x', 'Un script.')")
    connection.execute(
        "INSERT INTO publication_kits (id, production_id, project_id, created_at, updated_at)"
        " VALUES ('kit_old', 'prd_old', 'prj_a', 'x', 'x')")
    connection.commit()
    connection.close()

    repo = ProductionRepository(path)
    ProductionRepository(path)  # rejeu : sans effet
    old = repo.get("prd_old")
    assert (old.script, old.snapshot, old.trace) == ("Un script.", {}, {})  # inchangé

    upgraded = sqlite3.connect(path)
    assert upgraded.execute("PRAGMA user_version").fetchone()[0] == 5
    assert upgraded.execute("SELECT name FROM projects").fetchall() == [("A",)]
    assert upgraded.execute("SELECT id FROM publication_kits").fetchall() == [("kit_old",)]
    tables = {row[0] for row in upgraded.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"characters", "locations"} <= tables
    assert upgraded.execute("SELECT COUNT(*) FROM characters").fetchone()[0] == 0
    assert upgraded.execute("SELECT COUNT(*) FROM locations").fetchone()[0] == 0


def test_snapshot_is_immutable_after_confirmation(tmp_path):
    project = _project(tmp_path)
    repo = ProductionRepository(tmp_path / "lody.sqlite3")
    draft = repo.create(project_id=project.id, subject="Sujet", provider="p", status=ProductionStatus.EN_ATTENTE_CONFIRMATION,
                        snapshot={"version": "v1"})
    with pytest.raises(ValueError):
        repo.update(draft.id, snapshot={"version": "autre"})             # update() ne touche jamais l'instantané
    assert repo.transition(draft.id, [ProductionStatus.EN_ATTENTE_CONFIRMATION], snapshot={"version": "v2"})  # brouillon : refait
    with pytest.raises(ValueError):
        repo.transition(draft.id, [ProductionStatus.CONFIRMEE, ProductionStatus.EN_COURS], snapshot={"version": "v3"})
    assert repo.get(draft.id).snapshot == {"version": "v2"}


# -- coût ---------------------------------------------------------------------------
def _request(**changes):
    base = GenerationRequest(subject="Explique la blockchain", voice=VoiceSpec("elevenlabs", "abc123", "Kev", "m"),
                             music_provider="elevenlabs", duration_min=45, duration_max=60,
                             scenes_per_minute_min=8, scenes_per_minute_max=10, narration_pace="rapide")
    return base.with_updates(**changes) if changes else base


def _book(**rates):
    return costing.PriceBook("EUR", {tuple(key.split("__")): Decimal(str(value)) for key, value in rates.items()})


def test_estimate_with_every_rate_is_complete_and_indicative():
    request = _request()
    book = _book(text__openai=0.02, visual__openai_image=0.04, voice__elevenlabs=0.30, music__elevenlabs=0.10)
    estimate = costing.estimate_cost(request, estimate_units(request), book)
    assert not estimate.partial and estimate.indicative
    lines = {line.component: line for line in estimate.lines}
    assert lines["visual"].quantity == (6, 10)  # 45 s × 8/min … 60 s × 10/min
    assert lines["visual"].low == Decimal("0.24") and lines["visual"].high == Decimal("0.40")
    assert lines["text"].low == lines["text"].high == Decimal("0.02")
    assert estimate.total_low < estimate.total_high
    assert costing.format_total(estimate).startswith("≈ ") and "€" in costing.format_total(estimate)


def test_missing_rate_makes_total_partial_and_never_invents_a_price():
    request = _request()
    book = _book(text__openai=0.02, visual__openai_image=0.04)  # voix et musique sans tarif
    estimate = costing.estimate_cost(request, estimate_units(request), book)
    assert estimate.partial
    unpriced = {line.component for line in estimate.lines if line.status == "unpriced"}
    assert unpriced == {"voice", "music"}
    assert all(line.rate is None and line.low is None for line in estimate.lines if line.status == "unpriced")
    assert "(partiel)" in costing.format_total(estimate)


def test_without_any_price_book_nothing_is_priced():
    request = _request()
    estimate = costing.estimate_cost(request, estimate_units(request), costing.PriceBook())
    assert estimate.partial and estimate.total_high is None
    assert costing.format_total(estimate) == "tarif non configuré"


def test_free_providers_are_free_not_unpriced():
    request = _request(text_provider="manual", visual_provider="local", music_provider="none",
                       voice=VoiceSpec("edge", "", "fr-FR-Denise", ""), script="mot " * 40)
    estimate = costing.estimate_cost(request, estimate_units(request), costing.PriceBook())
    assert not estimate.partial and estimate.total_high == Decimal(0)


def test_supplied_script_gives_exact_quantities_and_no_text_call():
    script = ("Une phrase courte. " * 30).strip()
    request = _request(script=script)
    units = estimate_units(request)
    assert units.text_calls == 0
    assert units.characters == (len(script), len(script))


def test_price_book_is_read_from_a_configurable_file(tmp_path):
    path = tmp_path / "pricing.toml"
    path.write_text('currency = "usd"\n[visual]\nopenai_image = 0.05\n[voice]\nelevenlabs = "0.2"\n[music]\nelevenlabs = -3\n',
                    encoding="utf-8")
    book = costing.load_price_book(path)
    assert book.currency == "USD"
    assert book.rate("visual", "openai_image") == Decimal("0.05")
    assert book.rate("voice", "elevenlabs") == Decimal("0.2")
    assert book.rate("music", "elevenlabs") is None  # négatif refusé


def test_missing_or_broken_price_file_means_no_price(tmp_path):
    assert costing.load_price_book(tmp_path / "absent.toml").rates == {}
    broken = tmp_path / "broken.toml"
    broken.write_text("ceci n'est pas du toml [", encoding="utf-8")
    assert costing.load_price_book(broken).rates == {}


# -- storyboard --------------------------------------------------------------------------
SCRIPT = ("Première idée simple. Deuxième idée avec un exemple concret. Troisième idée, attention au piège. "
          "Quatrième idée : ce qu’il faut retenir. Cinquième idée pour finir. Sixième et dernière idée !")


def test_storyboard_keeps_script_order_and_covers_every_sentence():
    scenes = storyboard.build_storyboard(SCRIPT, 3, visual_style="Univers sombre, cyan", aspect="9:16")
    assert [scene.index for scene in scenes] == [1, 2, 3]
    assert " ".join(scene.narration for scene in scenes) == " ".join(storyboard.split_sentences(SCRIPT))
    assert scenes[0].narration.startswith("Première idée") and scenes[-1].narration.endswith("idée !")
    for scene in scenes:
        assert "Univers sombre, cyan" in scene.prompt and "Aucun texte" in scene.prompt and "verticale" in scene.prompt
        assert scene.seconds > 0 and len(scene.prompt) <= storyboard.MAX_PROMPT


def test_storyboard_never_exceeds_available_sentences_and_handles_empty_script():
    assert len(storyboard.build_storyboard("Une seule phrase.", 8)) == 1
    assert storyboard.build_storyboard("   ", 5) == []
    assert len(storyboard.build_storyboard(SCRIPT, 30)) == 6


# -- dépôt -----------------------------------------------------------------------------------
def test_repository_versions_and_atomic_transition(tmp_path):
    project = _project(tmp_path)
    repo = ProductionRepository(tmp_path / "lody.sqlite3")
    first = repo.create(project_id=project.id, subject="Sujet un", provider="p", status=ProductionStatus.EN_ATTENTE_CONFIRMATION)
    assert first.version == 1 and first.root_production_id == first.id and first.label == "V1"
    second = repo.create(project_id=project.id, subject="Sujet un", provider="p", status=ProductionStatus.BROUILLON,
                         parent_production_id=first.id)
    third = repo.create(project_id=project.id, subject="Sujet un", provider="p", status=ProductionStatus.BROUILLON,
                        parent_production_id=second.id)
    assert (second.version, third.version) == (2, 3) and third.root_production_id == first.id
    assert [p.version for p in repo.chain(first.id)] == [1, 2, 3]
    # la transition n'est gagnée qu'une fois
    assert repo.transition(first.id, [ProductionStatus.EN_ATTENTE_CONFIRMATION], status=ProductionStatus.CONFIRMEE)
    assert not repo.transition(first.id, [ProductionStatus.EN_ATTENTE_CONFIRMATION], status=ProductionStatus.CONFIRMEE)


def test_repository_refuses_secrets_and_unknown_columns(tmp_path):
    project = _project(tmp_path)
    repo = ProductionRepository(tmp_path / "lody.sqlite3")
    production = repo.create(project_id=project.id, subject="Sujet", provider="p", status=ProductionStatus.BROUILLON)
    with pytest.raises(ValueError):
        repo.update(production.id, script="ma clé sk-abcdefghijklmnopqrstuvwxyz123456")
    with pytest.raises(ValueError):
        repo.update(production.id, params={"api_key": "x"})
    with pytest.raises(ValueError):
        repo.update(production.id, id="autre")
    with pytest.raises(ValueError):
        repo.create(project_id=project.id, subject="Sujet avec sk-abcdefghijklmnopqrstuvwxyz123456", provider="p",
                    status=ProductionStatus.BROUILLON)
