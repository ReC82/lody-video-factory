"""Kit de publication : création, édition persistante, miniature, sous-titres, contrôles, ZIP, fond payant. Faux fournisseurs."""

from __future__ import annotations

import io
import json
import re
import zipfile
from decimal import Decimal

import pytest
from PIL import Image

from lody import settings
from lody.generation import kit_files, media, thumbnail
from lody.generation import kit_service as ks
from lody.generation.costing import PriceBook
from lody.generation.kit_service import KitService
from lody.generation.kit_store import KitRepository
from lody.generation.models import ErrorKind, ProviderError, RemoteState, TaskSnapshot
from lody.generation.models import ProductionStatus as S
from lody.generation.service import LaunchError, ProductionService
from lody.generation.store import ProductionRepository
from lody.projects import ProjectRepository
from lody.seeds import SEED_PROJECTS
from test.lody.fakes import SCRIPT, ScriptedConnector, SyncExecutor, _png

PRICES = PriceBook("EUR", {("text", "openai"): Decimal("0.02"), ("visual", "openai_image"): Decimal("0.04"),
                           ("voice", "elevenlabs"): Decimal("0.30"), ("music", "elevenlabs"): Decimal("0.10")})
SUBJECT_C = "Crée le Short #0 : « Pourquoi je lance cette série ». Je ne suis pas expert financier."
SUBJECT_A = "Explique simplement la différence entre Fill et Key en télévision."
RAW_SRT = ("1\n00:00:00,000 --> 00:00:04,000\nSi tu découvres le Web3 gaming\n\n2\n00:00:04,000 --> 00:00:08,000\nmais l’  idée de base est simple\n\n"
           "3\n00:00:08,000 --> 00:00:12,000\nc’ est un registre qu’ il n’ y a\n\n4\n00:00:12,000 --> 00:00:99,000\nDernier sous-titre trop long\n")


@pytest.fixture(autouse=True)
def _isolated(lody_env, monkeypatch):
    monkeypatch.setattr(media, "probe_duration_ms", lambda path, timeout=20.0: 52_400)


class Env:
    def __init__(self, tmp_path):
        self.path = settings.db_path()
        self.projects = ProjectRepository(self.path)
        self.projects.seed_defaults(SEED_PROJECTS)
        self.connector = ScriptedConnector(tmp_path / "storage")
        self.connector.subtitles_text = RAW_SRT
        self.build()

    def build(self):
        self.productions = ProductionService(ProductionRepository(self.path), {"scripted": self.connector}, SyncExecutor(),
                                             price_book=lambda: PRICES)
        self.kits = KitService(KitRepository(self.path), self.productions, ProjectRepository(self.path), SyncExecutor(),
                               price_book=lambda: PRICES)

    def project(self, name):
        return next(p for p in self.projects.list_projects() if p.name == name)

    def finish(self, project_name="LodyCrypto", subject=SUBJECT_C, images=2):
        project = self.project(project_name)
        draft = self.productions.prepare(project, subject, provider_id="scripted")
        running = self.productions.confirm(draft.id, accept_partial=True)
        task = running.external_task_id
        folder = self.connector.root / "tasks" / task
        folder.mkdir(parents=True, exist_ok=True)
        self.connector.scene_images = []
        for i in range(images):
            name = f"openai-image-{i:04x}aa.png"
            (folder / name).write_bytes(_png(f"{task}-{i}", (512, 768)))
            self.connector.scene_images.append({"ref": f"tasks/{task}/{name}", "name": name})
        self.connector.queue(self.connector.done(task))
        return self.productions.refresh(running.id)


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


# -- création : gratuite, locale, à la demande -----------------------------------------------------------------------------------------
def test_kit_is_created_on_demand_for_a_finished_production_without_any_paid_call(env):
    production = env.finish()
    paid_before = env.connector.calls.count("submit") + env.connector.calls.count("write_script")
    kit = env.kits.ensure_kit(production.id)
    assert env.kits.ensure_kit(production.id).id == kit.id                       # idempotent : un seul kit par production
    assert kit.production_id == production.id and kit.project_id == production.project_id and kit.generated_with == "local"
    assert kit.metadata == kit.initial_metadata and kit.metadata["title"] == "Pourquoi je lance cette série"
    assert kit.thumbnail["text"] == "Pourquoi je lance cette série" and kit.thumbnail["variant"] == "centre"
    assert kit.thumbnail["background"]["kind"] == "scene" and kit.history[0]["action"].startswith("création")
    assert env.connector.calls.count("submit") + env.connector.calls.count("write_script") == paid_before
    assert "thumbnail_background" not in env.connector.calls


def test_kit_only_exists_for_finished_productions(env):
    project = env.project("LodyCrypto")
    draft = env.productions.prepare(project, SUBJECT_C, provider_id="scripted")
    with pytest.raises(LaunchError, match="production terminée"):
        env.kits.ensure_kit(draft.id)


def test_lodycrypto_kit_uses_its_own_profile(env):
    meta = env.kits.ensure_kit(env.finish().id).metadata
    assert meta["playlist"] == "LodyCrypto — Comprendre le Web3 gaming" and meta["made_for_kids"] is False
    assert meta["hashtags"][:3] == ["#LodyCrypto", "#Crypto", "#Web3Gaming"] and meta["video_language"] == "fr"
    assert "n’est pas un conseil d’investissement" in meta["description_youtube"]
    assert meta["file_name"].startswith("lodycrypto-v1-")


# -- édition, historique, persistance, restauration ----------------------------------------------------------------------------------
def test_manual_edits_persist_across_restart_with_a_minimal_history(env):
    kit = env.kits.ensure_kit(env.finish().id)
    env.kits.save_metadata(kit.id, {"title": "Mon titre à moi", "hashtags": "#UnSeul #Deux", "made_for_kids": True})
    env.build()                                                                   # redémarrage : nouveaux services, même base
    saved = env.kits.kits.get(kit.id)
    assert saved.metadata["title"] == "Mon titre à moi" and saved.metadata["hashtags"] == ["#UnSeul", "#Deux"] and saved.metadata["made_for_kids"] is True
    assert saved.initial_metadata["title"] == "Pourquoi je lance cette série"      # les propositions initiales sont conservées
    last = saved.history[-1]
    assert last["action"] == "modification manuelle" and set(last["fields"]) == {"title", "hashtags", "made_for_kids"}
    assert last["before"]["title"] == "Pourquoi je lance cette série"
    assert env.kits.save_metadata(kit.id, {"title": "Mon titre à moi"}).history == saved.history   # rien de changé : pas d'entrée


def test_invalid_and_secret_edits_are_refused_and_change_nothing(env):
    kit = env.kits.ensure_kit(env.finish().id)
    with pytest.raises(LaunchError, match="Titre"):
        env.kits.save_metadata(kit.id, {"title": "x" * 150})
    with pytest.raises(LaunchError):
        env.kits.save_metadata(kit.id, {"description_youtube": "ma clé sk-abcdefghijklmnopqrstuvwxyz123456"})
    with pytest.raises(LaunchError):
        env.kits.save_metadata(kit.id, {"file_name": "../x.mp4"})
    assert env.kits.kits.get(kit.id).metadata == kit.metadata


def test_restore_and_free_regeneration_keep_the_previous_values_in_the_history(env):
    kit = env.kits.ensure_kit(env.finish().id)
    env.kits.save_metadata(kit.id, {"title": "Édité"})
    restored = env.kits.restore_initial(kit.id)
    assert restored.metadata == kit.initial_metadata and restored.history[-1]["action"].startswith("restauration")
    assert restored.history[-1]["before"]["title"] == "Édité"
    env.kits.save_metadata(kit.id, {"title": "Encore édité"})
    fresh = env.kits.regenerate_metadata(kit.id)
    assert fresh.metadata["title"] == "Pourquoi je lance cette série" and fresh.history[-1]["before"]["title"] == "Encore édité"
    assert env.connector.calls.count("submit") == 1 and env.productions.repo.get(kit.production_id).status is S.TERMINEE   # vidéo intacte


def test_a_v2_has_its_own_kit_and_v1_is_untouched(env):
    v1 = env.finish()
    kit1 = env.kits.ensure_kit(v1.id)
    env.kits.save_metadata(kit1.id, {"title": "Titre de la V1"})
    before = env.kits.kits.get(kit1.id)
    v2_draft = env.productions.create_v2(v1.id)
    v2_prepared = env.productions.prepare_v2(v2_draft.id, SCRIPT + " Une phrase ajoutée à la main pour la deuxième version.")
    running = env.productions.confirm(v2_prepared.id, accept_partial=True)
    (env.connector.root / "tasks" / running.external_task_id).mkdir(parents=True, exist_ok=True)
    env.connector.queue(env.connector.done(running.external_task_id))
    v2 = env.productions.refresh(running.id)
    kit2 = env.kits.ensure_kit(v2.id)
    assert kit2.id != kit1.id and kit2.production_id == v2.id and kit2.metadata["title"] != "Titre de la V1"
    assert kit2.metadata["file_name"].startswith("lodycrypto-v2-")
    assert env.kits.kits.get(kit1.id) == before                                   # V1 strictement inchangée


# -- miniature ----------------------------------------------------------------------------------------------------------------------------
def test_three_compositions_from_one_background_cost_nothing_and_show_the_exact_text(env):
    production = env.finish()
    kit = env.kits.ensure_kit(production.id)
    env.kits.set_thumbnail(kit.id, text="Crypto & NFT, simplement")
    kit = env.kits.kits.get(kit.id)
    results = env.kits.render_all(kit, production)
    assert set(results) == {"centre", "haut", "lateral"} and all(layout.text == "Crypto & NFT, simplement" for _, layout in results.values())
    assert len({thumbnail.to_png(image) for image, _ in results.values()}) == 3
    assert "thumbnail_background" not in env.connector.calls                      # aucun appel payant : le texte est rendu localement


def test_choosing_text_variant_and_background_persists_and_rebuilds_for_free(env):
    production = env.finish()
    kit = env.kits.ensure_kit(production.id)
    other = env.connector.scene_images[1]["ref"]
    env.kits.set_thumbnail(kit.id, text="Deux mots", variant="lateral", background_ref=other)
    env.build()
    saved = env.kits.kits.get(kit.id)
    assert saved.thumbnail["text"] == "Deux mots" and saved.thumbnail["variant"] == "lateral" and saved.thumbnail["background"]["ref"] == other
    png, jpg = env.kits.thumbnail_bytes(saved, production, "png"), env.kits.thumbnail_bytes(saved, production, "jpeg")
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and jpg[:2] == b"\xff\xd8"
    assert Image.open(io.BytesIO(png)).size == (1080, 1920)
    _, layout = env.kits.render_selected(saved, production)
    assert layout.variant == "lateral" and layout.text == "Deux mots"
    assert {e["action"] for e in saved.history} >= {"miniature"}


def test_thumbnail_refuses_empty_text_unknown_variant_secrets_and_foreign_backgrounds(env):
    production = env.finish()
    kit = env.kits.ensure_kit(production.id)
    for bad in ({"text": "   "}, {"variant": "diagonale"}, {"text": "sk-abcdefghijklmnopqrstuvwxyz123456"},
                {"background_ref": "tasks/autre-tache-999999/openai-image-0000aa.png"}, {"background_ref": "../../etc/passwd"}):
        with pytest.raises(LaunchError):
            env.kits.set_thumbnail(kit.id, **bad)
    assert env.kits.kits.get(kit.id).thumbnail == kit.thumbnail


def test_scene_image_backgrounds_carry_a_contamination_warning_for_old_productions(env):
    production = env.finish()
    with env.productions.repo._connect() as connection:          # production antérieure à la traçabilité : aucune trace enregistrée
        connection.execute("UPDATE productions SET trace = '{}' WHERE id = ?", (production.id,))
    notes = env.kits.background_warnings(env.productions.repo.get(production.id))
    assert any("gabarit d’images global" in note for note in notes) and any("visage" in note for note in notes)
    traced = env.productions.repo.update(production.id, trace={"image_template": {"applied": False}, "scenes": []})
    assert not any("gabarit" in note for note in env.kits.background_warnings(traced))


# -- sous-titres -------------------------------------------------------------------------------------------------------------------------
def test_subtitles_are_rebuilt_from_the_existing_srt_with_french_fixed_and_synced_to_the_final_video(env):
    production = env.finish()
    bundle = env.kits.subtitles(production)
    assert bundle.available and bundle.check.ok
    for phrase in ("l’idée", "c’est", "qu’il", "n’y"):
        assert phrase in bundle.srt and phrase in bundle.vtt
    assert not re.search(r"[’'][ \t]+\w", bundle.srt) and "  " not in bundle.srt.replace("\n\n", "\n")
    assert bundle.cues[-1].end_ms == 52_400 and bundle.check.last_end_ms <= 52_400          # jamais après la dernière image
    assert bundle.vtt.startswith("WEBVTT") and "00:00:04.000 --> 00:00:08.000" in bundle.vtt
    assert env.connector.calls.count("read_subtitles") >= 1 and "submit" in env.connector.calls and env.connector.calls.count("submit") == 1


def test_missing_engine_subtitles_are_reported_never_invented(env):
    env.connector.subtitles_text = None
    production = env.finish()
    bundle = env.kits.subtitles(production)
    assert not bundle.available and bundle.srt == "" and bundle.check is None
    kit = env.kits.ensure_kit(production.id)
    check = {c.key: c for c in env.kits.checks(kit, production)}["subtitles"]
    assert check.ok is False and "incrustés" in check.detail


# -- contrôle avant publication --------------------------------------------------------------------------------------------------------------
def test_checks_report_duration_format_thumbnail_subtitles_and_wording(env):
    production = env.finish()
    kit = env.kits.ensure_kit(production.id)
    checks = {c.key: c for c in env.kits.checks(kit, production)}
    assert checks["duration"].ok and "52" in checks["duration"].detail and checks["format"].ok and checks["thumbnail"].ok
    assert checks["subtitles"].ok and checks["financial"].ok and checks["internal"].ok
    env.kits.save_metadata(kit.id, {"description_youtube": "Investis maintenant : rendement garanti, tu vas devenir riche !"})
    checks = {c.key: c for c in env.kits.checks(env.kits.kits.get(kit.id), production)}
    assert checks["financial"].ok is False and "rendement" in checks["financial"].detail


def test_checks_flag_internal_data_and_the_zip_refuses_to_export_it(env):
    production = env.finish()
    kit = env.kits.ensure_kit(production.id)
    env.kits.save_metadata(kit.id, {"description_youtube": "voir /MoneyPrinterTurbo/storage/tasks/abc et config.toml"})
    kit = env.kits.kits.get(kit.id)
    assert {c.key: c for c in env.kits.checks(kit, production)}["internal"].ok is False
    with pytest.raises(ValueError, match="donnée interne"):
        env.kits.build_zip(kit, production)


# -- archive ---------------------------------------------------------------------------------------------------------------------------------
def test_zip_contains_the_required_files_and_nothing_internal(env):
    production = env.finish()
    kit = env.kits.ensure_kit(production.id)
    data = env.kits.build_zip(kit, production)
    archive = zipfile.ZipFile(io.BytesIO(data))
    assert sorted(archive.namelist()) == ["metadata.json", "publication-tiktok.txt", "publication-youtube.txt", "subtitles-fr.srt",
                                          "subtitles-fr.vtt", "thumbnail.png", "video.mp4"]
    assert archive.read("video.mp4") == b"fake-mp4" and archive.read("thumbnail.png")[:8] == b"\x89PNG\r\n\x1a\n"
    assert "l’idée" in archive.read("subtitles-fr.srt").decode("utf-8") and archive.read("subtitles-fr.vtt").startswith(b"WEBVTT")
    assert kit.metadata["title"] in archive.read("publication-youtube.txt").decode("utf-8")
    assert kit.metadata["tiktok_caption"] in archive.read("publication-tiktok.txt").decode("utf-8")
    meta = json.loads(archive.read("metadata.json"))
    assert meta["project"] == "LodyCrypto" and meta["version"] == "V1" and meta["publication"]["title"] == kit.metadata["title"]
    assert meta["video"] == {"file": "video.mp4", "duration_seconds": 52.4, "aspect": "9:16", "language": "fr"}
    assert meta["subtitles"]["burned_in_video"] is True and meta["thumbnail"]["file"] == "thumbnail.png"
    everything = b"".join(archive.read(name) for name in archive.namelist() if name not in ("video.mp4", "thumbnail.png")).decode("utf-8")
    assert not ks._INTERNAL.search(everything) and "task-0001" not in everything and str(env.connector.root) not in everything
    assert "sk-" not in everything and "api_key" not in everything and "FAKE-" not in everything


def test_zip_is_deterministic_and_reflects_edits(env):
    production = env.finish()
    kit = env.kits.ensure_kit(production.id)
    assert env.kits.build_zip(kit, production) == env.kits.build_zip(kit, production)
    env.kits.save_metadata(kit.id, {"title": "Titre modifié"})
    edited = zipfile.ZipFile(io.BytesIO(env.kits.build_zip(env.kits.kits.get(kit.id), production)))
    assert "Titre modifié" in edited.read("publication-youtube.txt").decode("utf-8")


def test_zip_uses_the_repaired_render_when_one_exists(env):
    production = env.finish()
    folder = env.connector.root / "tasks" / production.external_task_id / "repair"
    folder.mkdir()
    (folder / "final-1-repaired.mp4").write_bytes(b"repaired-mp4")
    env.productions.repo.update(production.id, assets=[*production.assets, {"kind": "repaired_video",
                                "ref": f"tasks/{production.external_task_id}/repair/final-1-repaired.mp4"}])
    production = env.productions.repo.get(production.id)
    kit = env.kits.ensure_kit(production.id)
    assert zipfile.ZipFile(io.BytesIO(env.kits.build_zip(kit, production))).read("video.mp4") == b"repaired-mp4"
    assert env.kits.video_path(production)[1] is True


# -- fond payant : estimation puis confirmation --------------------------------------------------------------------------------------------------
def test_the_legacy_engine_cannot_generate_a_background_and_nothing_is_called(env):
    kit = env.kits.ensure_kit(env.finish().id)
    with pytest.raises(LaunchError, match="image de scène"):
        env.kits.prepare_background(kit.id)
    assert "thumbnail_background" not in env.connector.calls


def test_regenerating_the_background_needs_an_estimate_then_one_explicit_confirmation(env):
    production = env.finish()
    env.connector.supports_thumbnail_background = True     # activé après la production : aucun fond initial
    kit = env.kits.ensure_kit(production.id)
    assert env.kits.confirm_background(kit.id).background_job.get("state", "idle") == "idle"     # rien à confirmer : aucun appel
    prepared = env.kits.prepare_background(kit.id)
    job = prepared.background_job
    assert job["state"] == "awaiting_confirmation" and job["estimate"]["high"] == "0.04" and job["estimate"]["partial"] is False
    assert "Ne montre jamais" in job["prompt"] and "Aucun texte" in job["prompt"] and "aucun visage humain" in job["prompt"]
    assert "thumbnail_background" not in env.connector.calls                                       # estimer ne coûte rien
    env.kits.confirm_background(kit.id)
    env.kits.confirm_background(kit.id)                                                            # double clic
    assert env.connector.calls.count("thumbnail_background") == 1                                  # UN seul appel payant
    done = env.kits.kits.get(kit.id)
    assert done.background_job["state"] == "idle" and done.thumbnail["background"]["kind"] == "generated"
    assert done.thumbnail["generated_refs"] == [done.thumbnail["background"]["ref"]]
    assert kit_files.resolve_background(done.thumbnail["background"]["ref"], production.id).read_bytes()[:4] == b"\x89PNG"
    assert any("confirmation explicite" in e["action"] for e in done.history)
    assert env.kits.render_selected(done, production)[1].text == done.thumbnail["text"]


def test_an_unpriced_background_needs_the_partial_cost_acknowledgement(env):
    production = env.finish()
    env.connector.supports_thumbnail_background = True
    env.kits._price_book = lambda: PriceBook()
    kit = env.kits.ensure_kit(production.id)
    assert env.kits.prepare_background(kit.id).background_job["estimate"]["partial"] is True
    with pytest.raises(LaunchError, match="accepter|acceptant"):
        env.kits.confirm_background(kit.id)
    assert "thumbnail_background" not in env.connector.calls
    env.kits.confirm_background(kit.id, accept_partial=True)
    assert env.connector.calls.count("thumbnail_background") == 1


def test_a_failed_paid_background_keeps_the_kit_and_can_be_retried_only_after_a_new_estimate(env):
    production = env.finish()
    env.connector.supports_thumbnail_background = True
    env.connector.background_error = ProviderError(ErrorKind.QUOTA, "Quota insuffisant chez le fournisseur.")
    kit = env.kits.ensure_kit(production.id)
    env.kits.prepare_background(kit.id)
    env.kits.confirm_background(kit.id)
    failed = env.kits.kits.get(kit.id)
    assert failed.background_job["state"] == "failed" and "Quota" in failed.background_job["message"]
    assert failed.thumbnail == kit.thumbnail and failed.metadata == kit.metadata               # rien n'est perdu
    calls = env.connector.calls.count("thumbnail_background")
    env.kits.confirm_background(kit.id)                                                         # sans nouvelle estimation : sans effet
    assert env.connector.calls.count("thumbnail_background") == calls
    env.connector.background_error = None
    env.kits.prepare_background(kit.id)
    env.kits.confirm_background(kit.id)
    assert env.connector.calls.count("thumbnail_background") == calls + 1


def test_cancelling_an_estimate_calls_nothing(env):
    production = env.finish()
    env.connector.supports_thumbnail_background = True
    kit = env.kits.ensure_kit(production.id)
    env.kits.prepare_background(kit.id)
    assert env.kits.cancel_background(kit.id).background_job == {"state": "idle"}
    assert "thumbnail_background" not in env.connector.calls


# -- fond payant inclus dans l'estimation initiale et la confirmation unique ---------------------------------------------------------------------------
def test_initial_estimate_includes_the_background_and_one_confirmation_generates_it_once(env):
    env.connector.supports_thumbnail_background = True
    project = env.project("LodyCrypto")
    draft = env.productions.prepare(project, SUBJECT_C, provider_id="scripted")
    lines = {line["component"]: line for line in draft.cost_detail["lines"]}
    assert "thumbnail" in lines and lines["thumbnail"]["label"] == "Miniature (fond d’image)" and lines["thumbnail"]["high"] == "0.04"
    assert draft.cost_detail["thumbnails"] == 1
    with_thumb = draft.cost_high
    env.connector.supports_thumbnail_background = False
    assert env.productions.prepare(project, SUBJECT_C, provider_id="scripted", draft_id=draft.id).cost_high == with_thumb - Decimal("0.04")
    env.connector.supports_thumbnail_background = True
    env.productions.prepare(project, SUBJECT_C, provider_id="scripted", draft_id=draft.id)
    assert "thumbnail_background" not in env.connector.calls                                    # préparer ne coûte rien
    running = env.productions.confirm(draft.id, accept_partial=True)
    assert env.connector.calls.count("thumbnail_background") == 1                              # confirmation unique : un appel de fond
    stored = env.productions.repo.get(running.id)
    asset = next(a for a in stored.assets if a["kind"] == "thumbnail_background")
    assert kit_files.resolve_background(asset["ref"], running.id).is_file()
    assert "prompt" in stored.trace["thumbnail_background"] and "Ne montre jamais" in stored.trace["thumbnail_background"]["prompt"]
    env.connector.queue(env.connector.done(running.external_task_id))
    done = env.productions.refresh(running.id)
    assert any(a["kind"] == "thumbnail_background" for a in done.assets)                       # conservé à la fin de la production
    kit = env.kits.ensure_kit(done.id)
    assert kit.thumbnail["background"]["kind"] == "generated"                                   # fond dédié choisi par défaut


def test_a_failed_initial_background_never_blocks_the_video(env):
    env.connector.supports_thumbnail_background = True
    env.connector.background_error = ProviderError(ErrorKind.PROVIDER, "Erreur du fournisseur d’images.")
    running = env.productions.confirm(env.productions.prepare(env.project("LodyCrypto"), SUBJECT_C, provider_id="scripted").id, accept_partial=True)
    stored = env.productions.repo.get(running.id)
    assert stored.status is S.EN_FILE and stored.external_task_id and not any(a["kind"] == "thumbnail_background" for a in stored.assets)
    assert any("Fond de miniature non généré" in w and "image de scène" in w for w in stored.warnings)
    env.connector.queue(TaskSnapshot(RemoteState.RUNNING, 25, "Voix"))
    assert any("Fond de miniature non généré" in w for w in env.productions.refresh(running.id).warnings)   # l'avertissement survit au suivi


# -- isolation entre projets ---------------------------------------------------------------------------------------------------------------------------
def test_kits_of_two_projects_share_nothing(env):
    crypto = env.kits.ensure_kit(env.finish("LodyCrypto", SUBJECT_C).id)
    env.connector.write_script = lambda request: ("Le Fill est le signal d’image et le Key est le signal de découpe. La régie les combine "
                                                  "pour incruster un graphisme à l’antenne. Le pupitre de la régie envoie le résultat final.")
    av_production = env.finish("Audiovisuel", SUBJECT_A)
    av = env.kits.ensure_kit(av_production.id)
    text_c = json.dumps([crypto.metadata, crypto.thumbnail], ensure_ascii=False)
    text_a = json.dumps([av.metadata, av.thumbnail], ensure_ascii=False)
    for word in ("Fill", "régie", "broadcast", "Réaliste broadcast", "télévision"):
        assert word not in text_c, word
    for word in ("LodyCrypto", "Web3", "crypto", "blockchain", "NFT"):
        assert word.lower() not in text_a.lower(), word
    assert av.metadata["playlist"] == "Audiovisuel" and av.metadata["hashtags"][0] == "#Audiovisuel"    # profil neutre : nom du projet
    assert av.project_id != crypto.project_id and av.production_id != crypto.production_id


def test_sentinel_words_of_another_project_never_reach_a_kit_prompt_or_archive(env):
    poison = "broadcast mixing console FILL/KEY AV-SENTINEL-7c1"
    env.projects.create(name="Sentinelle AV", description=poison, language="fr-FR", format="9:16", content_type="pedagogique",
                        tone=poison[:60], visual_style=poison, platforms=[], text_provider="openai", visual_provider="openai_image",
                        voice_provider="elevenlabs", voice_name="Voix", music_provider="none",
                        settings={"brief": {"visual_rules": poison, "visual_avoid": [poison], "audience": poison,
                                            "publication": {"playlist": poison, "hashtags": ["#AVSentinel"], "tags": [poison], "disclaimer": poison}}})
    env.finish("Sentinelle AV", "Sujet AV " + poison)
    crypto_prod = env.finish("LodyCrypto", "Explique la blockchain simplement au public débutant")
    env.connector.supports_thumbnail_background = True
    kit = env.kits.ensure_kit(crypto_prod.id)
    prepared = env.kits.prepare_background(kit.id)
    archive = zipfile.ZipFile(io.BytesIO(env.kits.build_zip(kit, crypto_prod)))
    texts = [json.dumps(kit.metadata, ensure_ascii=False), json.dumps(kit.thumbnail), prepared.background_job["prompt"],
             *(archive.read(n).decode("utf-8") for n in archive.namelist() if n.endswith((".txt", ".json", ".srt", ".vtt")))]
    for text in texts:
        for expression in ("mixing console", "AV-SENTINEL-7c1", "AVSentinel"):
            assert expression.lower() not in text.lower(), expression
    # « broadcast » et « FILL/KEY » n'apparaissent que dans la liste négative PROPRE à LodyCrypto (« Ne montre jamais : … »)
    prompt = prepared.background_job["prompt"]
    outside = prompt.split("Ne montre jamais :")[0]
    assert "broadcast" not in outside.lower() and "fill/key" not in outside.lower() and "aucun schéma FILL/KEY" in prompt
    for text in texts[:2] + texts[3:]:
        assert "broadcast" not in text.lower() and "fill/key" not in text.lower()


def test_a_kit_cannot_be_used_with_another_projects_production(env):
    crypto_kit = env.kits.ensure_kit(env.finish("LodyCrypto", SUBJECT_C).id)
    av_production = env.finish("Audiovisuel", SUBJECT_A)
    tampered = KitRepository(env.path).update(crypto_kit.id, thumbnail=crypto_kit.thumbnail)
    with env.kits.kits._connect() as connection:      # kit rattaché à un autre projet que celui de sa production
        connection.execute("UPDATE publication_kits SET production_id = ? WHERE id = ?", (av_production.id, tampered.id))
    with pytest.raises(LaunchError, match="n’appartient pas au projet"):
        env.kits.production_of(env.kits.kits.get(tampered.id))


# -- productions existantes ---------------------------------------------------------------------------------------------------------------------------
def test_an_existing_production_without_snapshot_or_trace_gets_a_free_kit(env):
    production = env.finish()
    legacy = env.productions.repo.update(production.id, trace={})
    with env.productions.repo._connect() as connection:
        connection.execute("UPDATE productions SET snapshot = '{}', trace = '{}' WHERE id = ?", (legacy.id,))
    paid = env.connector.calls.count("submit")
    kit = env.kits.ensure_kit(legacy.id)
    assert kit.metadata["title"] and env.kits.subtitles(env.productions.repo.get(legacy.id)).available
    assert env.connector.calls.count("submit") == paid and "thumbnail_background" not in env.connector.calls
    assert any("antérieure" in note for note in env.kits.background_warnings(env.productions.repo.get(legacy.id)))
