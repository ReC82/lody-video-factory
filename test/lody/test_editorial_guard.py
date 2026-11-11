"""Garde-fou éditorial avant génération (#167) : niveau novice, script ↔ storyboard, texte parlé.

Module pur : aucun réseau, aucune base, aucun fournisseur payant. Les scénarios de régression sont les fichiers
``docs/editorial/*.preprod.json`` (SDI V1 #137, SDI V2 #160, Tally #140, Genlock #141, mélangeur #142) et le
modèle novice HDMI → SDI qui doit passer sans alerte.
"""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

from lody.animation.contract import validate_document
from lody.editorial.checks import A_REVOIR, BLOQUANT, INFO, check_document, codes, norm
from lody.editorial.preprod import PreprodError, from_storyboard, storyboard_path, validate_preprod
from lody.editorial.report import to_markdown

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "docs" / "editorial"


def load(name: str) -> dict:
    return validate_preprod(json.loads((EXAMPLES / f"{name}.preprod.json").read_text(encoding="utf-8")))


def hits(report, code: str) -> list:
    return [f for f in report.findings if f.code == code]


def out_of_level(report) -> set[str]:
    """Termes signalés « hors niveau », en minuscules sans accents."""
    terms = set()
    for f in hits(report, "hors-niveau"):
        terms.add(norm(f.message.split("« ", 1)[1].split(" »", 1)[0]))
    return terms


def minimal(**brief) -> dict:
    base = {"objective": "Comprendre une chose.", "audience": "novice", "assumed_knowledge": ["télé", "câble"],
            "new_terms": [{"term": "SDI", "explanation": "le câble vidéo des pros"}],
            "examples": ["la télé du salon"], "cta": "Et toi, tu la vois où ?"}
    base.update(brief)
    return base


def doc(scenes: list[dict], **brief) -> dict:
    return validate_preprod({"schema": "lody.preprod/1", "brief": minimal(**brief), "scenes": scenes})


def scene(id: str = "s1", spoken: str = "Le SDI : c'est le câble vidéo des pros. Et toi, tu la vois où ?",
          visual: str = "Un long câble branché sur une caméra de plateau, Voltix le montre.", **extra) -> dict:
    return {"id": id, "spoken": spoken, "visual": visual, "duration_s": 6.0, **extra}


# -- forme du document -----------------------------------------------------------------------------------------
def test_validation_collects_precise_errors():
    raw = {"schema": "lody.preprod/1", "brief": {"audience": "expert", "oops": 1, "scene_seconds": [8, 5]},
           "scenes": [{"id": "A B", "visual": 3}, {"id": "ok", "script": "x"}, {"id": "ok"}]}
    with pytest.raises(PreprodError) as error:
        validate_preprod(raw)
    errors = error.value.errors
    assert "brief" in errors and "oops" in errors["brief"]
    assert "brief.audience" not in errors  # le brief a une clé inconnue : ses champs ne sont pas examinés
    assert errors["scenes[0].id"].startswith("identifiant attendu")
    assert errors["scenes[0].visual"] == "texte attendu."
    assert "script" in errors["scenes[1]"]
    assert errors["scenes[2].id"] == "identifiant de scène en double."


def test_validation_checks_brief_fields_and_schema():
    with pytest.raises(PreprodError) as error:
        validate_preprod({"schema": "autre/1", "brief": {"audience": "expert", "scene_seconds": [8, 5],
                                                         "words_per_second": 0, "new_terms": [{"explanation": "x"}]},
                          "scenes": []})
    errors = error.value.errors
    assert set(errors) >= {"schema", "brief.audience", "brief.scene_seconds", "brief.words_per_second",
                           "brief.new_terms[0]", "scenes"}


def test_validation_returns_independent_normalized_copy():
    raw = {"schema": "lody.preprod/1", "brief": minimal(), "scenes": [{"id": "s1"}]}
    document = validate_preprod(raw)
    assert document["scenes"][0] == {"id": "s1", "spoken": "", "visual": "", "captions": [], "displayed": []}
    assert raw["scenes"][0] == {"id": "s1"}


# -- modèle novice : prêt, et jamais modifié ---------------------------------------------------------------------
def test_novice_model_passes_without_any_alert_and_is_never_modified():
    document = load("hdmi-vers-sdi-novice")
    before = copy.deepcopy(document)
    report = check_document(document)
    assert report.verdict == "prêt", [f.message for f in report.findings]
    assert report.findings == []
    assert document == before  # aucune correction automatique
    assert report.new_terms == ["SDI"]
    assert 25 <= report.speech_total_s <= 45


# -- régressions : épisodes réels ---------------------------------------------------------------------------------
def test_regression_sdi_v1_flags_unseen_cable_jargon_and_pauses():
    report = check_document(load("regression-sdi-v1"))
    shown = hits(report, "montre-sans-visuel")
    assert [(f.scene, f.path) for f in shown] == [("accroche", "scenes[0].visual")]
    assert "cable" in shown[0].message
    assert {"3g-sdi", "12g-sdi", "hd-sdi", "4k", "bnc", "melangeur video", "regie"} <= out_of_level(report)
    assert [f.scene for f in hits(report, "tts-pause")] == ["debits"]
    assert "debits" in {f.scene for f in hits(report, "scene-longue")}
    assert "affiche-avant-dit" in codes(report.findings)
    assert report.verdict == "à relire"


def test_regression_sdi_v2_exceeds_novice_level_but_fixed_the_hook():
    report = check_document(load("regression-sdi-v2"))
    assert {"broadcast", "grille de routage", "impedance caracteristique", "3g-sdi", "12g-sdi", "hd-sdi", "coaxial",
            "bnc", "debit", "convertisseur"} <= out_of_level(report)
    found = codes(report.findings)
    # La V2 a corrigé l'accroche, les pauses et l'affichage : le contrôle ne les signale plus.
    assert not found & {"montre-sans-visuel", "tts-pause", "phonetique-affichee", "tts-sigle", "premiere-occurrence"}
    assert "cta-absent" in found


def test_regression_tally_term_used_before_explanation():
    report = check_document(load("regression-tally"))
    first = hits(report, "premiere-occurrence")
    assert [(f.scene, f.path) for f in first] == [("question", "scenes[2].spoken")]
    assert "previsualisation" in out_of_level(report)
    assert "tally" not in out_of_level(report)  # notion de l'épisode, déclarée
    assert "cta-absent" in codes(report.findings)


def test_regression_genlock_flags_jargon_but_accepts_its_explained_notion():
    report = check_document(load("regression-genlock"))
    assert {"cadence", "broadcast", "signal de reference", "resynchroniser", "melangeur"} <= out_of_level(report)
    assert "premiere-occurrence" not in codes(report.findings)  # « Ça s'appelle le Genlock. »
    assert "cta-hors-scene" not in codes(report.findings)


def test_regression_mixer_flags_jargon_and_voice_overflow():
    report = check_document(load("regression-melangeur"))
    assert {"regie", "previsualisation"} <= out_of_level(report)
    assert "melangeur" not in out_of_level(report)  # alias de la notion « mélangeur vidéo »
    overflow = hits(report, "voix-deborde")
    assert [(f.level, f.scene) for f in overflow] == [(BLOQUANT, "vocabulaire")]
    assert report.verdict == "à corriger"


# -- contrôles unitaires : niveau novice ---------------------------------------------------------------------------
def test_brief_missing_parts_are_reported():
    report = check_document(validate_preprod({"schema": "lody.preprod/1", "brief": {}, "scenes": [scene()]}))
    found = codes(report.findings)
    assert {"objectif-absent", "public-implicite", "connaissances-absentes", "notion-absente", "exemple-absent",
            "cta-absent"} <= found
    assert report.verdict == "à corriger"


def test_one_new_notion_per_novice_episode():
    terms = [{"term": "SDI", "explanation": "x"}, {"term": "BNC", "explanation": "y"}, {"term": "coaxial"}]
    report = check_document(doc([scene()], new_terms=terms))
    [many] = hits(report, "plusieurs-notions")
    assert "« BNC », « coaxial »" in many.suggestion
    assert [f.path for f in hits(report, "explication-absente")] == ["brief.new_terms[2].explanation"]
    assert {"BNC", "coaxial"} <= {f.message.split("« ")[1].split(" »")[0] for f in hits(report, "notion-jamais-dite")}
    initiated = check_document(doc([scene()], new_terms=terms, audience="initie"))
    assert "plusieurs-notions" not in codes(initiated.findings)


def test_out_of_level_is_info_for_an_initiated_audience():
    spoken = "Le SDI : c'est le câble vidéo des pros, en 3G-SDI. Et toi, tu la vois où ?"
    novice = check_document(doc([scene(spoken=spoken)], pronunciations={"3G-SDI": "trois gé èss di aï"}))
    assert [f.level for f in hits(novice, "hors-niveau")] == [A_REVOIR]
    initiated = check_document(doc([scene(spoken=spoken)], audience="initie",
                                   pronunciations={"3G-SDI": "trois gé èss di aï"}))
    assert [f.level for f in hits(initiated, "hors-niveau")] == [INFO]


def test_extra_jargon_and_assumed_knowledge():
    spoken = "Le SDI : c'est le câble vidéo des pros, avec un multiplexeur. Et toi, tu la vois où ?"
    report = check_document(doc([scene(spoken=spoken)], extra_jargon=["multiplexeur"]))
    assert out_of_level(report) == {"multiplexeur"}
    known = check_document(doc([scene(spoken=spoken)], extra_jargon=["multiplexeur"],
                               assumed_knowledge=["télé", "câble", "multiplexeur"]))
    assert out_of_level(known) == set()


def test_common_word_never_covers_jargon():
    spoken = "Le SDI : c'est le câble vidéo des pros, vers un mélangeur vidéo. Et toi, tu la vois où ?"
    report = check_document(doc([scene(spoken=spoken)], assumed_knowledge=["vidéo", "câble"]))
    assert out_of_level(report) == {"melangeur video"}


def test_first_occurrence_without_explanation():
    scenes = [scene("s1", "Aujourd'hui on parle du SDI.", displayed=["SDI"]),
              scene("s2", "Il est partout. On le croise souvent. Et toi, tu la vois où ?")]
    report = check_document(doc(scenes))
    assert [(f.scene, f.path) for f in hits(report, "premiere-occurrence")] == [("s1", "scenes[0].spoken")]
    assert "le câble vidéo des pros" in hits(report, "premiere-occurrence")[0].suggestion


def test_term_displayed_before_being_said():
    scenes = [scene("s1", "Bonjour à tous, regardez bien la télé.", displayed=["SDI"]), scene("s2")]
    report = check_document(doc(scenes))
    assert [(f.scene, f.path) for f in hits(report, "affiche-avant-dit")] == [("s1", "scenes[0].displayed")]
    assert "notion-jamais-dite" not in codes(report.findings)


def test_cta_must_not_expose_untaught_technology_and_must_close_the_episode():
    report = check_document(doc([scene(spoken="Le SDI : c'est le câble des pros. Au revoir.")],
                                cta="Prochain épisode : le Genlock et le timecode !"))
    [untaught] = hits(report, "cta-non-enseigne")
    assert "timecode" in untaught.message
    assert [f.scene for f in hits(report, "cta-hors-scene")] == ["s1"]


# -- contrôles unitaires : script ↔ storyboard ---------------------------------------------------------------------
def test_missing_or_vague_visual():
    report = check_document(doc([scene("s1", visual=""), scene("s2", visual="Voltix.")]))
    assert [(f.level, f.scene) for f in hits(report, "visuel-absent")] == [(BLOQUANT, "s1")]
    assert [f.scene for f in hits(report, "visuel-vague")] == ["s2"]
    assert report.verdict == "à corriger"


@pytest.mark.parametrize("sentence, flagged", [
    ("Tu vois ce câble ?", True),
    ("Regarde cette fiche métallique !", True),
    ("Voici ces voyants rouges.", True),
    ("Ce câble transporte l'image.", False),  # pas de geste « montrer » : pas de vérification
    ("Regarde cette vidéo jusqu'au bout.", False),  # « vidéo » n'est pas un objet à montrer
])
def test_voice_pointing_at_something_not_in_the_picture(sentence, flagged):
    visual = "Voltix seul au centre de l'écran, il sourit et salue."
    report = check_document(doc([scene(spoken=f"Le SDI : c'est le câble des pros. {sentence}", visual=visual)]))
    assert bool(hits(report, "montre-sans-visuel")) is flagged
    shown = check_document(doc([scene(spoken=f"Le SDI : c'est le câble des pros. {sentence}",
                                      visual=visual + " Un câble, une fiche et des voyants rouges au premier plan.")]))
    assert not hits(shown, "montre-sans-visuel")


def test_durations_follow_the_voice():
    long_text = " ".join(["mot"] * 30)
    scenes = [scene("courte", duration_s=3.0, spoken="Le SDI : c'est le câble des pros."),
              scene("longue", duration_s=9.0, spoken="Une phrase de quelques mots seulement ici."),
              scene("deborde", duration_s=6.0, spoken=long_text),
              scene("mesuree", duration_s=6.0, audio_s=5.5, spoken=long_text),
              scene("muette", spoken="", visual="Plan large du plateau, les caméras s'allument une à une.")]
    report = check_document(doc(scenes, target_seconds=[40, 60]))
    assert [f.scene for f in hits(report, "scene-courte")] == ["courte"]
    assert [f.scene for f in hits(report, "scene-longue")] == ["longue"]
    assert [f.scene for f in hits(report, "voix-deborde")] == ["deborde"]
    assert [f.scene for f in hits(report, "ecran-sans-voix")] == ["longue"]
    assert [f.scene for f in hits(report, "scene-muette")] == ["muette"]
    assert hits(report, "duree-totale")
    timing = {t.id: t for t in report.timings}
    assert timing["mesuree"].measured and timing["mesuree"].speech_s == 5.5
    assert timing["deborde"].speech_s == round(30 / 2.6, 2)
    assert timing["muette"].planned_s == 6.0


def test_planned_duration_defaults_to_the_voice():
    report = check_document(doc([{"id": "s1", "spoken": " ".join(["mot"] * 13), "visual": "Un câble branché "
                                  "sur une caméra de plateau."}], words_per_second=2.6))
    assert report.timings[0].planned_s == 5.5
    assert not hits(report, "scene-courte")


# -- contrôles unitaires : texte parlé ----------------------------------------------------------------------------
def test_undeclared_acronym_symbol_unit_and_pause():
    spoken = "Le SDI : c'est le câble des pros, 75 Ω, en HD, SDI, à 12 Gbit/s. Et toi, tu la vois où ?"
    report = check_document(doc([scene(spoken=spoken)], audience="initie"))
    assert {"SDI", "HD"} <= {f.message.split("« ")[1].split(" »")[0] for f in hits(report, "tts-sigle")}
    assert hits(report, "tts-unite") and hits(report, "tts-pause")
    symbol = check_document(doc([scene(spoken="Le SDI : c'est 75 Ω. Et toi, tu la vois où ?")]))
    assert "Ω" in hits(symbol, "tts-symbole")[0].message


def test_declared_pronunciation_is_info_and_must_be_heard():
    report = check_document(doc([scene()], pronunciations={"SDI": "èss di aï"}))
    [info] = hits(report, "prononciation-declaree")
    assert info.level == INFO and "écoute" in info.message
    assert not hits(report, "tts-sigle")
    assert report.verdict == "prêt"


def test_phonetic_form_on_screen_is_blocking():
    scenes = [scene(spoken="Le èss di aï : c'est le câble des pros. Et toi, tu la vois où ?",
                    captions=["Le èss di aï : c'est le câble des pros.", "Et toi, tu la vois où ?"],
                    displayed=["SDI"])]
    report = check_document(doc(scenes, pronunciations={"SDI": "èss di aï"}))
    [shown] = hits(report, "phonetique-affichee")
    assert shown.level == BLOQUANT and shown.path == "scenes[0].captions[0]" and "« SDI »" in shown.message
    fixed = copy.deepcopy(scenes)
    fixed[0]["captions"][0] = "Le SDI : c'est le câble des pros."
    clean = check_document(doc(fixed, pronunciations={"SDI": "èss di aï"}))
    assert not clean.findings, [f.message for f in clean.findings]


def test_voice_and_captions_must_match_and_stay_short():
    scenes = [scene(captions=["Le SDI : c'est le câble des pros, et il passe partout dans les studios de "
                              "télévision du monde entier.", "Et toi ?"])]
    report = check_document(doc(scenes))
    assert hits(report, "voix-sous-titres") and hits(report, "sous-titre-long")


@pytest.mark.parametrize("spoken", ["Scène 2 : le SDI arrive.", "Le SDI en 9:16, c'est le câble des pros.",
                                    "Épisode 3 — le SDI.", "Images fixes uniquement pour le SDI."])
def test_production_instructions_in_the_voice_are_blocking(spoken):
    report = check_document(doc([scene(spoken=spoken)]))
    assert [f.level for f in hits(report, "consigne-dans-voix")] == [BLOQUANT]


# -- storyboard animé lody.animation/1 ----------------------------------------------------------------------------
def test_from_storyboard_maps_fields_and_paths():
    storyboard = validate_document(json.loads((ROOT / "docs" / "animation" / "voltix-150" / "storyboard.json")
                                              .read_text(encoding="utf-8")))
    document = from_storyboard(storyboard, minimal(cta="À bientôt"))
    first = document["scenes"][0]
    assert first["id"] == "accueil" and first["spoken"].startswith("Salut, moi c'est Voltix")
    assert first["captions"][0] == "Salut, moi c'est Voltix !"
    assert "Le câble SDI" in first["displayed"]
    assert "voltix" in first["visual"]
    assert first["duration_s"] == round(167 / 30, 3)
    report = check_document(document)
    assert "coaxial" in out_of_level(report)
    assert storyboard_path("scenes[1].captions[2]") == "scenes[1].render.speech.cues[2]"
    assert storyboard_path("scenes[0].spoken") == "scenes[0].intent.narration"
    assert storyboard_path("scenes[0].duration_s") == "scenes[0].duration_frames"
    assert storyboard_path("brief.cta") == "brief.cta"


def test_from_storyboard_rejects_other_documents():
    with pytest.raises(PreprodError):
        from_storyboard({"schema": "lody.preprod/1"}, minimal())


# -- vue lisible et script ----------------------------------------------------------------------------------------
def test_markdown_view_lists_every_scene_with_its_alerts():
    document = load("regression-sdi-v1")
    text = to_markdown(document, check_document(document))
    assert "**⚠️ À relire" in text
    assert sum(line.startswith("|") for line in text.splitlines()) == 2 + len(document["scenes"])
    assert "| 1 | `accroche` |" in text and "montre-sans-visuel" in text
    assert "scène `accroche`, champ `scenes[0].visual`" in text
    assert "aucune correction automatique" in text


def test_report_as_dict_is_json_serializable():
    report = check_document(load("regression-tally"))
    data = json.loads(json.dumps(report.as_dict(), ensure_ascii=False))
    assert data["verdict"] == "à relire" and data["counts"][A_REVOIR] == len(
        [f for f in report.findings if f.level == A_REVOIR])
    assert data["settings"]["scene_seconds"] == [5.0, 7.0]


def _cli():
    spec = importlib.util.spec_from_file_location("verifier_preproduction",
                                                  ROOT / "scripts" / "editorial" / "verifier_preproduction.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_exit_codes_and_outputs(tmp_path, capsys):
    cli = _cli()
    assert cli.main([str(EXAMPLES / "hdmi-vers-sdi-novice.preprod.json")]) == 0
    assert "✅ Prêt" in capsys.readouterr().out
    assert cli.main([str(EXAMPLES / "regression-melangeur.preprod.json"), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["verdict"] == "à corriger"
    bad = tmp_path / "bad.json"
    bad.write_text('{"schema": "lody.preprod/1"}', encoding="utf-8")
    assert cli.main([str(bad)]) == 2
    storyboard = ROOT / "docs" / "animation" / "voltix-150" / "storyboard.json"
    assert cli.main([str(storyboard)]) == 2  # un storyboard animé exige son brief
    brief = tmp_path / "brief.json"
    brief.write_text(json.dumps({"brief": minimal()}), encoding="utf-8")
    out = tmp_path / "rapport.json"
    assert cli.main([str(storyboard), "--brief", str(brief), "--json", "--sortie", str(out)]) == 0
    paths = {f["path"] for f in json.loads(out.read_text(encoding="utf-8"))["findings"]}
    assert any(".intent.narration" in p or ".render." in p for p in paths)
    assert not any(p.endswith((".spoken", ".displayed")) for p in paths)
