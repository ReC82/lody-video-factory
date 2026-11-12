"""Recette contrôlée du rendu 2D parlé (#187) : protocole V1 → V2 (réplique scène 2) → V3 (visuel scène 1).

Rejoue, SANS aucun appel ElevenLabs ni réseau externe, ce que le propriétaire vérifiera en DEV : appels de synthèse
comptés par un faux ElevenLabs, coût annoncé, accord exigé, cache de scène, repères recalés et sous-titres,
erreurs de configuration. Moteur de scène factice (mire ffmpeg), worker exécuté dans le processus du test, base et
dossier de données temporaires, aucun config.toml.
"""

from __future__ import annotations

import http.server
import json
import socket
import threading
from pathlib import Path

import pytest

from lody.animation import pilot, recette, reedit, scene_edit, voiceover
from lody.animation import worker as w
from lody.characters import CharacterRepository
from lody.generation import reedit_v2
from lody.generation.models import ProductionStatus as S
from lody.generation.service import LaunchError
from test.lody.test_animation_reedit_ui import Studio
from test.lody.test_animation_voiceover import VOICE_ID, FakeSynth
from test.lody.test_generation_service import SUBJECT

RECETTE = Path(__file__).resolve().parents[2] / "docs" / "animation" / "recette-187"
pytestmark = pytest.mark.skipif(not w.ffmpeg_exe(), reason="ffmpeg indisponible")


@pytest.fixture(autouse=True)
def _remotion_evaluation(monkeypatch):
    monkeypatch.delenv("LODY_REMOTION_LICENSE", raising=False)
    monkeypatch.setenv("LODY_REMOTION_EVALUATION", "1")


class VoicedStudio(Studio):
    """Studio du rendu 2D dont le héros est relié à la fiche « Voltix » portant une voix ElevenLabs (#153)."""

    def __init__(self, tmp_path: Path, synth=None):
        super().__init__(tmp_path / "data", tmp_path / "data" / "lody.sqlite3")  # = settings.db_path()
        self.characters = CharacterRepository(self.db)
        self.connector._character_repo = self.characters
        self.connector._synthesize = synth
        self.synth = synth

    def voiced_v1_draft(self, voice: bool = True):
        project = self.project("Recette 187")
        raw = self.raw(project.id)
        hero = next(obj["asset"]["id"] for layer in raw["scenes"][0]["render"]["layers"]
                    for obj in layer["objects"] if obj["id"] == "heros")
        fields = {"voice_provider": "elevenlabs", "voice_name": "Voix Voltix", "external_voice_id": VOICE_ID} if voice else {}
        character = self.characters.create(project.id, name="Voltix", **fields)
        self.assets.update(project.id, hero, character_id=character.id)
        return project, self.service.prepare(project, SUBJECT, provider_id="animation_2d", animation=raw)

    def run(self, production_id: str):
        self.service.confirm(production_id, accept_partial=True)
        return self.service.refresh(production_id)

    def directory(self, production) -> Path:
        return w.find_job_dir(self.data, production.external_task_id)


def _timing(directory: Path) -> dict:
    return voiceover.read_timing(directory)


def _audit(directory: Path) -> dict:
    return json.loads((directory / "manifest.json").read_text(encoding="utf-8"))["audit"]


# -- protocole complet -------------------------------------------------------------------------------------------
def test_protocol_v1_v2_v3_pays_only_the_modified_line_and_reuses_the_rest(tmp_path, monkeypatch):
    synth = FakeSynth(tmp_path)
    studio = VoicedStudio(tmp_path, synth)
    _, draft = studio.voiced_v1_draft()

    # V1 : deux scènes parlées, deux appels, coût annoncé = caractères des deux répliques.
    assert reedit_v2.billable_voice_characters(studio.service, draft) == len("Bonjour") + len("Ensuite")
    v1 = studio.run(draft.id)
    assert v1.status is S.TERMINEE, v1.error_message
    assert [call[0] for call in synth.calls] == ["Bonjour", "Ensuite"]
    one = studio.directory(v1)
    assert set(_timing(one)) == {"1", "2"} and _audit(one)["rendered_scenes"] == [1, 2]

    # V2 : seule la réplique de la scène 2 change → un seul appel, accord exigé, aperçu jamais payant.
    v2 = studio.service.prepare_animation_v2(studio.service.create_v2(v1.id).id, scene_edit.edit_scene(
        v1.animation, "suite", {"cues": {0: "Puis"}}))
    plan = reedit_v2.plan_for(studio.service, v2)
    assert [(e["index"], e["action"], e["voice"]) for e in plan["scenes"]] == [(1, "reuse", "keep"), (2, "render", "regenerate")]
    billable = reedit_v2.billable_voice_characters(studio.service, v2)
    assert billable == len("Puis")
    message, consent = reedit_v2.voice_notice(plan, reedit_v2.has_voice(studio.service, v1), billable)
    assert consent and "4 caractère(s)" in message and "jamais lancée sans ton accord" in message
    with pytest.raises(LaunchError, match="Aperçu impossible.*payant"):
        reedit_v2.start_preview(studio.service, v2)
    assert len(synth.calls) == 2  # l'aperçu n'a rien payé
    v2 = studio.run(v2.id)
    assert v2.status is S.TERMINEE, v2.error_message
    assert [call[0] for call in synth.calls] == ["Bonjour", "Ensuite", "Puis"]
    two = studio.directory(v2)
    assert _timing(two)["1"]["key"] == _timing(one)["1"]["key"]
    assert _timing(two)["2"]["key"] != _timing(one)["2"]["key"]
    assert [c["text"] for c in _timing(two)["2"]["cues"]] == ["Puis"]
    assert "Puis" in (two / "subtitles.srt").read_text(encoding="utf-8")
    assert (_audit(two)["reused_scenes"], _audit(two)["rendered_scenes"]) == ([1], [2])

    # V3 : visuel seul sur la scène 1 → aucun appel, coût 0, aperçu gratuit permis, scène 2 reprise.
    v3 = studio.service.prepare_animation_v2(studio.service.create_v2(v2.id).id, scene_edit.edit_scene(
        v2.animation, "accueil", {"framing": "serre"}))
    plan = reedit_v2.plan_for(studio.service, v3)
    assert [(e["index"], e["action"], e["voice"]) for e in plan["scenes"]] == [(1, "render", "keep"), (2, "reuse", "keep")]
    assert reedit_v2.billable_voice_characters(studio.service, v3) == 0
    assert reedit_v2.voice_notice(plan, True, 0) == ("Coût : 0 € — rendu 2D local, aucun appel externe.", False)
    reedit_v2.start_preview(studio.service, v3)
    assert reedit_v2.preview_state(studio.service, v3).state == "done"
    v3 = studio.run(v3.id)
    assert v3.status is S.TERMINEE, v3.error_message
    assert len(synth.calls) == 3  # aucune resynthèse en V3 (ni par l'aperçu, ni par la génération)
    three = studio.directory(v3)
    assert {k: v["key"] for k, v in _timing(three).items()} == {k: v["key"] for k, v in _timing(two).items()}
    assert (_audit(three)["reused_scenes"], _audit(three)["rendered_scenes"]) == ([2], [1])

    # L'outil de recette du propriétaire rend le même verdict à partir des seuls dossiers de rendu.
    verdicts = recette.verify(one, two, three)
    assert all(v.ok for v in verdicts), [v for v in verdicts if not v.ok]
    assert recette.main(["verify", "--dirs", str(one), str(two), str(three)]) == 0
    monkeypatch.setenv("LODY_DATA_DIR", str(studio.data))  # commande du propriétaire, par identifiant
    assert recette.main(["verify", "--productions", v1.id, v2.id, v3.id]) == 0
    # Et détecte une V3 qui aurait re-synthétisé ou tout re-rendu (dossiers inversés).
    assert not all(v.ok for v in recette.verify(one, three, two))


def test_quiet_plan_with_an_empty_voice_cache_still_asks_for_consent(tmp_path):
    """Le plan ne voit que le storyboard : si le cache de voix a été vidé, l'accord vient des caractères réels."""
    synth = FakeSynth(tmp_path)
    studio = VoicedStudio(tmp_path, synth)
    _, draft = studio.voiced_v1_draft()
    v1 = studio.run(draft.id)
    v2 = studio.service.prepare_animation_v2(studio.service.create_v2(v1.id).id, scene_edit.edit_scene(
        v1.animation, "accueil", {"framing": "moyen"}))
    for path in studio.connector.voice_cache().root.rglob("*.mp3"):
        path.unlink()
    plan = reedit_v2.plan_for(studio.service, v2)
    assert not plan["summary"]["voice_to_regenerate"]
    billable = reedit_v2.billable_voice_characters(studio.service, v2)
    assert billable == len("Bonjour") + len("Ensuite")
    assert reedit_v2.voice_notice(plan, True, billable)[1] is True
    with pytest.raises(LaunchError, match="Aperçu impossible"):
        reedit_v2.start_preview(studio.service, v2)
    assert len(synth.calls) == 2


# -- erreurs de configuration, sans appel payant ----------------------------------------------------------------------
def test_voltix_card_without_voice_blocks_before_any_call(tmp_path):
    synth = FakeSynth(tmp_path)
    studio = VoicedStudio(tmp_path, synth)
    _, draft = studio.voiced_v1_draft(voice=False)
    with pytest.raises(LaunchError, match="configuration de production est incomplète"):
        studio.service.confirm(draft.id)
    assert synth.calls == [] and studio.service.repo.get(draft.id).status is S.EN_ATTENTE_CONFIRMATION


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_dev_pilot_without_engine_fails_clearly_and_never_renders_a_silent_video(tmp_path, monkeypatch):
    """Pilote DEV tel que livré (#154) : aucun moteur joignable → échec clair, aucune vidéo muette."""
    monkeypatch.setenv("LODY_ENGINE_URL", f"http://127.0.0.1:{_closed_port()}")
    studio = VoicedStudio(tmp_path, synth=None)  # vrai client du moteur, vers un port fermé : aucun réseau externe
    _, draft = studio.voiced_v1_draft()
    failed = studio.run(draft.id)
    assert failed.status is S.ECHEC
    assert "Voix ElevenLabs" in failed.error_message and "injoignable" in failed.error_message
    assert studio.renderer.rendered == [] and not list(studio.connector.voice_cache().root.rglob("*.mp3"))


class _EngineStub(http.server.BaseHTTPRequestHandler):
    """Moteur local qui répond comme ``/api/v1/voice_preview`` quand ElevenLabs n'est pas configuré (HTTP 400)."""

    calls = 0

    def do_POST(self):  # noqa: N802
        type(self).calls += 1
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        body = json.dumps({"status": 400, "message": "req: ElevenLabs is not configured"}).encode()
        self.send_response(400)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_engine_without_elevenlabs_key_stops_at_the_first_scene(tmp_path, monkeypatch):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _EngineStub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("LODY_ENGINE_URL", f"http://127.0.0.1:{server.server_address[1]}")
        studio = VoicedStudio(tmp_path, synth=None)
        _, draft = studio.voiced_v1_draft()
        failed = studio.run(draft.id)
    finally:
        server.shutdown()
    assert failed.status is S.ECHEC and "Voix ElevenLabs" in failed.error_message
    assert "scène 1" in failed.error_message  # écart documenté : la cause exacte n'est que dans le journal
    assert _EngineStub.calls == 1 and studio.renderer.rendered == []


def test_quota_error_on_scene_two_keeps_scene_one_paid_for_the_retry(tmp_path):
    synth = FakeSynth(tmp_path, fail_on=2)
    studio = VoicedStudio(tmp_path, synth)
    _, draft = studio.voiced_v1_draft()
    failed = studio.run(draft.id)
    assert failed.status is S.ECHEC and "quota" in failed.error_message and studio.renderer.rendered == []
    retry = studio.service.prepare(studio.projects.get(draft.project_id), SUBJECT, provider_id="animation_2d",
                                   animation=draft.animation)
    assert reedit_v2.billable_voice_characters(studio.service, retry) == len("Ensuite")  # la scène 1 reste payée


# -- jeu de test du propriétaire (docs/animation/recette-187) -----------------------------------------------------
def _recette(name: str) -> dict:
    return json.loads((RECETTE / f"storyboard-{name}.json").read_text(encoding="utf-8"))


def test_recette_storyboards_are_two_voltix_scenes_and_differ_as_the_protocol_says(tmp_path):
    v1, v2, v3 = _recette("v1"), _recette("v2"), _recette("v3")
    assert [s["id"] for s in v1["scenes"]] == ["accueil", "coaxial"]
    assert {s["render"]["speech"]["speaker"] for s in v1["scenes"]} == {"voltix"}
    assert v2["scenes"][0] == {k: v for k, v in v1["scenes"][0].items()}
    assert v3["scenes"][1] == v2["scenes"][1]
    assert v3["scenes"][0]["render"]["speech"] == v1["scenes"][0]["render"]["speech"]
    assert v2 == scene_edit.edit_scene(v1, "coaxial", {"cues": {1: "… et son impédance vaut 75 ohms."}})
    assert v3 == scene_edit.edit_scene(v2, "accueil", {"framing": "serre"})
    plan2 = reedit.plan(v1, v2, has_voice=True)
    assert [(e["action"], e["voice"]) for e in plan2["scenes"]] == [("reuse", "keep"), ("render", "regenerate")]
    plan3 = reedit.plan(v2, v3, has_voice=True)
    assert [(e["action"], e["voice"]) for e in plan3["scenes"]] == [("render", "keep"), ("reuse", "keep")]

    # Les assets figés sont ceux de la démo Voltix #150 livrée dans l'image (retrouvés par empreinte).
    studio = VoicedStudio(tmp_path)
    project = studio.project("Recette Voltix")
    pilot.install_voltix_demo(studio.assets, project.id, studio.data)
    adopted = pilot.adopt_storyboard(studio.assets, project.id, v1)
    assert len(adopted["scenes"]) == 2


def test_cost_estimate_and_cap_of_the_recette():
    result = recette.estimate([_recette("v1"), _recette("v2"), _recette("v3")])
    assert [(s["calls"], s["scenes"]) for s in result["steps"]] == [(2, [1, 2]), (1, [2]), (0, [])]
    assert result["characters"] == 206 and result["worst_case_characters"] == 206 * recette.ENGINE_ATTEMPTS
    assert result["within_cap"] and result["cap"] == recette.DEFAULT_CAP
    assert recette.main(["estimate", str(RECETTE / "storyboard-v1.json"), "--cap", "100"]) == 1  # plafond dépassé


def test_regeneration_flag_now_matches_the_real_behaviour():
    """#187 : depuis #169 la V2 re-voise vraiment ; le drapeau ne promet plus un comportement absent."""
    assert reedit_v2.VOICE_REGENERATION_AVAILABLE is True
    changed = {"summary": {"voice_to_regenerate": ["suite"]}}
    assert reedit_v2.voice_notice(changed, True, 12)[1] is True
    message, consent = reedit_v2.voice_notice(changed, True, 0)
    assert not consent and "déjà en cache" in message
    message, consent = reedit_v2.voice_notice(changed, False, 0)
    assert not consent and "aucun coût" in message
