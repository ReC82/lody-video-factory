"""Nouvelle version d'une vidéo animée, scène par scène (#159) : édition simple, service, aperçu, interface.

Aucun réseau, aucun fournisseur payant, aucun vrai config.toml : moteur de scène factice (mire ffmpeg), worker
exécuté dans le processus du test, base et dossier de données temporaires.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

from lody import settings
from lody.animation import scene_edit
from lody.animation import worker as w
from lody.animation.assets import AnimationAssetRepository
from lody.animation.contract import validate_document
from lody.generation import reedit_v2
from lody.generation.animation_connector import AnimationConnector
from lody.generation.models import ProductionStatus as S
from lody.generation.service import LaunchError, ProductionService
from lody.generation.store import ProductionRepository
from lody.projects import ProjectRepository
from test.lody.fakes import SyncExecutor
from test.lody.test_animation_provider import FFMPEG, FPS, FakeRenderer, World, _file, needs_ffmpeg
from test.lody.test_generation_service import FULL_BOOK, SUBJECT

PROVIDER = "animation_2d"


class Studio(World):
    """Le monde de test du fournisseur 2D, sur des chemins choisis, avec un service et un worker synchrones."""

    def __init__(self, data: Path, db: Path):
        self.data, self.db = Path(data), Path(db)
        self.projects = ProjectRepository(self.db)
        self.assets = AnimationAssetRepository(self.db)
        self.renderer = FakeRenderer()
        self.connector = AnimationConnector(root=self.data, asset_repo=self.assets, renderer=f"{sys.executable} -c pass",
                                            ffmpeg=FFMPEG, launcher=self._run)
        self.service = ProductionService(ProductionRepository(self.db), {PROVIDER: self.connector}, SyncExecutor(),
                                         price_book=lambda: FULL_BOOK, animation_asset_repo=self.assets)

    def _run(self, directory: Path) -> None:
        w.run_job(directory, self.renderer, ffmpeg=FFMPEG)

    def catalog(self, project_id: str, tag: str) -> tuple[str, str]:
        hero, decor = super().catalog(project_id, tag)
        svg = _file(self.data, f"animation-assets/{tag}/hero2.svg", b"<svg xmlns='http://www.w3.org/2000/svg'><g/></svg>")
        self.assets.add_version(project_id, hero, {  # v2 : plus de gestes et d'expressions déclarés
            "format": "svg", "width": 600, "height": 900, "expressions": ["joie", "surprise"],
            "gestures": ["salut", "pointer"], "files": [{"role": "base", **svg}]})
        return hero, decor

    def raw(self, project_id: str) -> dict:
        document = self.storyboard(project_id)
        document.pop("frozen")
        for scene in document["scenes"]:
            hero = scene["render"]["layers"][1]["objects"][0]
            hero["expressions"] = [{"frame": 0, "name": "joie"}]
            scene["render"]["layers"].insert(0, {"id": "textes", "objects": [{
                "id": "titre", "type": "text", "text": {"content": f"Titre {scene['id']}", "style": "title"},
                "position": [{"frame": 0, "x": 160, "y": 80}]}]})
        return document

    def finished_v1(self, name: str = "Voltix test"):
        project = self.project(name)
        draft = self.service.prepare(project, SUBJECT, provider_id=PROVIDER, animation=self.raw(project.id))
        self.service.confirm(draft.id)
        v1 = self.service.refresh(draft.id)
        assert v1.status is S.TERMINEE, v1.error_message
        return project, v1


HERO = "perso/heros"


@pytest.fixture(autouse=True)
def _remotion_evaluation(monkeypatch):
    """#154/#156 : licence Remotion non décidée → rendu permis seulement en évaluation DEV explicite."""
    monkeypatch.delenv("LODY_REMOTION_LICENSE", raising=False)
    monkeypatch.setenv("LODY_REMOTION_EVALUATION", "1")


# -- module pur : résumé et modifications -------------------------------------------------------------------------
def _frozen(tmp_path) -> tuple[Studio, dict]:
    studio = Studio(tmp_path / "data", tmp_path / "lody.sqlite3")
    project = studio.project("Édition")
    from lody.animation.freeze import freeze_document

    return studio, freeze_document(studio.assets, project.id, studio.raw(project.id))


def test_summary_lists_time_dialogue_character_and_schemas(tmp_path):
    _, document = _frozen(tmp_path)
    first, second = scene_edit.summarize(document)
    assert (first.index, first.id, first.start_seconds, first.end_seconds) == (1, "accueil", 0.0, 1.0)
    assert (second.start_seconds, second.end_seconds, second.seconds) == (1.0, 2.0, 1.0)
    assert second.cues[0][0] == "Ensuite" and second.cues[0][1] == round((FPS + 2) / FPS, 2)
    assert second.speaker == "Héros a" and second.texts == (("textes/titre", "Titre suite", "title"),)
    (hero,) = second.characters
    assert hero.name == "Héros a" and hero.version == 2 and hero.gestures == ((0, 12, "salut"),)
    assert hero.expressions == ((0, "joie"),)
    assert [item.name for item in second.schemas] == ["Décor a"] and second.zoom == 1.0


def test_edit_scene_changes_only_that_scene_and_never_the_original(tmp_path):
    _, document = _frozen(tmp_path)
    before = copy.deepcopy(document)
    edited = scene_edit.edit_scene(document, "suite", {
        "gestures": {(HERO, 0): "pointer"}, "expressions": {(HERO, 0): "surprise"},
        "cues": {0: "Puis"}, "texts": {"textes/titre": "Nouveau titre"}, "framing": "serre"})
    assert document == before  # l'original (V1) n'est jamais modifié
    assert "frozen" not in edited and edited["scenes"][0] == {k: v for k, v in before["scenes"][0].items()}
    scene = edited["scenes"][1]
    hero = scene["render"]["layers"][2]["objects"][0]
    assert hero["gestures"][0] == {"start": 0, "end": 12, "name": "pointer"}
    assert hero["expressions"][0]["name"] == "surprise"
    assert scene["render"]["speech"]["cues"][0]["text"] == "Puis"
    assert scene["intent"]["narration"].startswith("Puis une deuxième scène")  # la narration suit la réplique
    assert scene["render"]["layers"][0]["objects"][0]["text"]["content"] == "Nouveau titre"
    assert scene["render"]["camera"]["keyframes"][0]["zoom"] == 1.5


def test_framing_keeps_an_existing_camera_movement_proportional(tmp_path):
    _, document = _frozen(tmp_path)
    document = copy.deepcopy(document)
    document.pop("frozen")
    document["scenes"][0]["render"]["camera"] = {"keyframes": [
        {"frame": 0, "x": 160, "y": 284, "zoom": 1.0, "rotation": 0},
        {"frame": FPS, "x": 160, "y": 284, "zoom": 1.1, "rotation": 0}]}
    edited = scene_edit.edit_scene(document, "accueil", {"framing": "moyen"})
    assert [key["zoom"] for key in edited["scenes"][0]["render"]["camera"]["keyframes"]] == [1.2, 1.32]
    assert scene_edit.framing_of(1.32) == "moyen" and scene_edit.framing_of(0.9) == "large"


def test_duration_stretches_the_whole_scene_animation(tmp_path):
    _, document = _frozen(tmp_path)
    edited = scene_edit.edit_scene(document, "accueil", {"seconds": 2.0})
    scene = edited["scenes"][0]
    assert scene["duration_frames"] == 2 * FPS
    hero = scene["render"]["layers"][2]["objects"][0]
    assert hero["gestures"][0] == {"start": 0, "end": 24, "name": "salut"}
    assert scene["render"]["speech"]["cues"][0] == {"text": "Bonjour", "start": 4, "end": 40}
    assert scene["render"]["fallback"]["frame"] == 10
    validate_document(edited)
    assert edited["scenes"][1] == {k: v for k, v in document["scenes"][1].items()}


@pytest.mark.parametrize(("changes", "message"), [
    ({"cues": {0: "   "}}, "ne peut pas être vide"),
    ({"cues": {0: "x" * 61}}, "trop long"),
    ({"cues": {5: "Bonjour"}}, "n'existe pas"),
    ({"texts": {HERO: "Texte"}}, "pas un texte affiché"),
    ({"gestures": {(HERO, 3): "salut"}}, "n'existe pas"),
    ({"framing": "plongée"}, "Cadrage inconnu"),
    ({"seconds": 0.2}, "Durée de scène"),
    ({"versions": {"textes/titre": 1}}, "n'utilise pas d'asset"),
])
def test_impossible_edits_are_refused_with_a_readable_message(tmp_path, changes, message):
    _, document = _frozen(tmp_path)
    with pytest.raises(scene_edit.SceneEditError, match=message):
        scene_edit.edit_scene(document, "accueil", changes)
    with pytest.raises(scene_edit.SceneEditError, match="Cette scène n'existe pas"):
        scene_edit.edit_scene(document, "inconnue", {"framing": "large"})
    assert not scene_edit.has_changes({"expressions": {}, "cues": {}}) and scene_edit.has_changes({"framing": "large"})


# -- service : brouillon, gel, plan, aperçu, génération, comparaison --------------------------------------------
@needs_ffmpeg
def test_new_version_rerenders_only_the_edited_scene_and_keeps_v1_intact(tmp_path):
    studio = Studio(tmp_path / "data", tmp_path / "lody.sqlite3")
    project, v1 = studio.finished_v1()
    assert studio.renderer.rendered == [1, 2]
    v1_animation, v1_video = copy.deepcopy(v1.animation), studio.service.resolve_video(v1)
    v1_bytes = v1_video.read_bytes()

    draft = studio.service.create_v2(v1.id)
    assert draft.animation == v1.animation and reedit_v2.plan_for(studio.service, draft)["summary"]["scenes_to_render"] == 0
    edited = scene_edit.edit_scene(draft.animation, "suite", {"gestures": {(HERO, 0): "pointer"}})
    ready = studio.service.prepare_animation_v2(draft.id, edited)
    assert ready.status is S.EN_ATTENTE_CONFIRMATION and ready.animation["frozen"]["sha256"] != v1.animation["frozen"]["sha256"]
    assert ready.cost_high == 0 and studio.service.repo.get(v1.id).animation == v1_animation

    plan = reedit_v2.plan_for(studio.service, ready)
    assert plan["summary"]["scenes_reused"] == 1 and plan["summary"]["scenes_to_render"] == 1
    assert plan["summary"]["render_seconds_saved_measured"] and plan["scenes"][1]["reason"] == "geste"

    # Aperçu ½ : aucune version créée, aucune confirmation ; relancé, il ne refait rien.
    assert reedit_v2.preview_state(studio.service, ready).state == "none"
    task = reedit_v2.start_preview(studio.service, ready)
    preview = reedit_v2.preview_state(studio.service, ready)
    assert preview.state == "done" and preview.video.is_file()
    assert reedit_v2.start_preview(studio.service, ready) == task
    manifest = w.read_json(preview.video.parent / "manifest.json")
    assert manifest["preview"] is True and manifest["width"] < 320
    assert len(studio.service.repo.list_for_project(project.id)) == 2
    assert studio.service.repo.get(ready.id).status is S.EN_ATTENTE_CONFIRMATION

    studio.renderer.rendered.clear()
    studio.service.confirm(ready.id)
    v2 = studio.service.refresh(ready.id)
    assert v2.status is S.TERMINEE and v2.label == "V2"
    assert studio.renderer.rendered == [2]  # scène 1 reprise du cache de la V1
    data = reedit_v2.comparison(studio.service, v2)
    assert data["scenes_reused"] == 1 and data["scenes_rendered"] == 1 and data["render_ms_saved"] > 0
    assert data["parent"].id == v1.id and data["previous_video"] == v1_video
    assert data["qa"]["status"] in ("ok", "warning") and data["qa_checks"] is not None
    assert v1_video.read_bytes() == v1_bytes and studio.service.repo.get(v1.id).status is S.TERMINEE

    thumbs = reedit_v2.scene_thumbnails(studio.service, v1)
    assert set(thumbs) == {"accueil", "suite"} and all(path.is_file() for path in thumbs.values())
    assert all(path.parent.name == reedit_v2.THUMBS_DIR for path in thumbs.values())  # hors du dossier de la V1


@needs_ffmpeg
def test_prepare_animation_v2_refuses_launched_versions_and_invalid_documents(tmp_path):
    studio = Studio(tmp_path / "data", tmp_path / "lody.sqlite3")
    _, v1 = studio.finished_v1()
    with pytest.raises(LaunchError, match="ne peut plus être modifiée"):
        studio.service.prepare_animation_v2(v1.id, v1.animation)
    draft = studio.service.create_v2(v1.id)
    broken = scene_edit.edit_scene(draft.animation, "suite", {})
    broken["scenes"][1]["render"]["layers"][2]["objects"][0]["gestures"][0]["name"] = "danser"
    with pytest.raises(LaunchError, match="non déclaré"):
        studio.service.prepare_animation_v2(draft.id, broken)
    assert studio.service.repo.get(draft.id).animation == v1.animation
    with pytest.raises(LaunchError, match="nouvelle version en préparation"):
        reedit_v2.start_preview(studio.service, v1)


@needs_ffmpeg
def test_v2_preview_and_launch_respect_the_remotion_licence_gate(tmp_path, monkeypatch):
    """#184 : la réédition V2 (aperçu ½ comme génération) ne contourne pas la garde de licence du worker #156."""
    studio = Studio(tmp_path / "data", tmp_path / "lody.sqlite3")
    _, v1 = studio.finished_v1()
    v1_bytes = studio.service.resolve_video(v1).read_bytes()
    draft = studio.service.create_v2(v1.id)
    ready = studio.service.prepare_animation_v2(
        draft.id, scene_edit.edit_scene(draft.animation, "suite", {"gestures": {(HERO, 0): "pointer"}}))
    studio.renderer.rendered.clear()
    monkeypatch.delenv("LODY_REMOTION_EVALUATION")
    with pytest.raises(LaunchError, match="Licence Remotion"):
        reedit_v2.start_preview(studio.service, ready)
    with pytest.raises(LaunchError, match="configuration de production est incomplète"):
        studio.service.confirm(ready.id)
    assert studio.renderer.rendered == [] and reedit_v2.preview_state(studio.service, ready).state == "none"
    assert studio.service.repo.get(ready.id).status is S.EN_ATTENTE_CONFIRMATION
    assert studio.service.resolve_video(v1).read_bytes() == v1_bytes
    monkeypatch.setenv("LODY_REMOTION_LICENSE", "company:PO-TEST")  # décision écrite → l'aperçu repart
    assert reedit_v2.preview_state(studio.service, ready).state == "none"
    reedit_v2.start_preview(studio.service, ready)
    assert reedit_v2.preview_state(studio.service, ready).state == "done"


def test_modified_line_is_never_presented_as_revoiced_before_169():
    """#184 : tant que la voix réelle du worker (#169) n'est pas fusionnée, aucune réplique n'est dite re-voisée."""
    assert reedit_v2.VOICE_REGENERATION_AVAILABLE is False
    quiet = {"summary": {"voice_to_regenerate": []}}
    changed = {"summary": {"voice_to_regenerate": ["suite"]}}
    assert reedit_v2.voice_notice(quiet, True) == ("Coût : 0 € — rendu 2D local, aucun appel externe.", False)
    for has_voice in (False, True):
        message, consent = reedit_v2.voice_notice(changed, has_voice)
        assert not consent and "aucun coût" in message and "Voix à régénérer" not in message
    assert "PAS re-voisées" in reedit_v2.voice_notice(changed, True)[0]


# -- interface (AppTest) ------------------------------------------------------------------------------------------
apptest = pytest.importorskip("streamlit.testing.v1")
streamlit = pytest.importorskip("streamlit")
WEBUI_DIR = Path(__file__).resolve().parents[2] / "webui"


@pytest.fixture
def studio(lody_env, monkeypatch):
    streamlit.cache_resource.clear()
    holder: dict[str, Studio] = {}

    def build():
        holder.setdefault("studio", Studio(settings.data_dir(), settings.db_path()))
        return holder["studio"].service

    monkeypatch.setattr("lody.app.build_service", build)
    build()
    yield holder["studio"]
    streamlit.cache_resource.clear()


def _button(app, label):
    return next(button for button in app.button if button.label == label)


def _text(app):
    return " ".join(str(item.value) for item in app.markdown)


@needs_ffmpeg
def test_owner_edits_scene_2_previews_and_generates_v2_without_json(studio):
    project, v1 = studio.finished_v1("Voltix interface")
    app = apptest.AppTest.from_file(str(WEBUI_DIR / "Lody.py"), default_timeout=60)
    app.query_params.update({"projet": project.id, "vue": "suivi", "production": v1.id})
    app = app.run()
    assert not app.exception
    app = _button(app, "Créer une nouvelle version").click().run()
    text = _text(app)
    assert "Nouvelle version" in text and "reste intacte" in text and "Scène 2" in text and "Héros a" in text
    assert not app.text_area and "Générer la V2" not in [b.label for b in app.button]  # ni JSON, ni génération à vide
    assert len(app.image) == 2  # miniature de chaque scène, tirée de la V1

    app = [b for b in app.button if b.label == "Modifier cette scène"][1].click().run()
    gesture = next(box for box in app.selectbox if box.label.startswith("Geste"))
    assert gesture.options == ["salut", "pointer"]  # seulement les gestes déclarés par l'asset
    gesture.set_value("pointer")
    app = next(b for b in app.button if b.label == "Enregistrer la scène").click().run()
    assert not app.exception, app.exception
    text = _text(app)
    assert "Plan de réédition" in text and "Scène 2 — à recalculer : geste" in text and "Scène 1 — réutilisée" in text
    assert "Coût : 0 €" in text
    metrics = {metric.label: metric.value for metric in app.metric}
    assert metrics["Scènes réutilisées"] == "1" and metrics["Scènes à recalculer"] == "1"

    app = _button(app, "Aperçu rapide").click().run()
    assert not app.exception
    assert len(studio.service.repo.list_for_project(project.id)) == 2  # l'aperçu ne crée pas de version

    studio.renderer.rendered.clear()
    app = _button(app, "Générer la V2").click().run()
    assert not app.exception, app.exception
    assert studio.renderer.rendered == [2]
    text = _text(app)
    assert "Réédition et contrôle qualité" in text and "Comparer avec la V1" in text
    metrics = {metric.label: metric.value for metric in app.metric}
    assert metrics["Scènes réutilisées"] == "1" and metrics["Scènes recalculées"] == "1"
    assert "Revenir à la V1" in [b.label for b in app.button]
    v1_after = studio.service.repo.get(v1.id)
    assert v1_after.status is S.TERMINEE and v1_after.animation == v1.animation


@needs_ffmpeg
def test_cancel_and_empty_edit_keep_the_draft_untouched(studio):
    project, v1 = studio.finished_v1("Voltix annulation")
    draft = studio.service.create_v2(v1.id)
    app = apptest.AppTest.from_file(str(WEBUI_DIR / "Lody.py"), default_timeout=60)
    app.query_params.update({"projet": project.id, "vue": "v2", "production": draft.id})
    app = app.run()
    app = [b for b in app.button if b.label == "Modifier cette scène"][0].click().run()
    app = next(b for b in app.button if b.label == "Enregistrer la scène").click().run()
    assert "Aucune modification à enregistrer" in _text(app)
    app = next(b for b in app.button if b.label == "Annuler").click().run()
    assert not app.exception and "Modifier cette scène" in [b.label for b in app.button]
    assert studio.service.repo.get(draft.id).status is S.BROUILLON
