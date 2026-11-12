"""Choix de la voix d'un personnage par écoute comparative (#153) — logique pure : candidats réels, empreinte,
cache, estimation avant appel, accord obligatoire, attribution durable et contrat de re-voisement 2D.
Aucun appel réseau : la synthèse est un faux."""

from __future__ import annotations

from decimal import Decimal

import pytest

from lody import settings, voice_casting as vc
from lody.characters import CharacterRepository
from lody.generation import costing
from lody.generation.elevenlabs_voices import VoiceInfo
from lody.projects import ProjectRepository

FR = VoiceInfo("fr-voice-1", "Léa", category="generated", labels={"language": "fr", "gender": "female"})
FR2 = VoiceInfo("fr-voice-2", "Hugo", category="professional", labels={"accent": "French"})
MULTI = VoiceInfo("multi-3", "Nova", category="generated", labels={"use_case": "multilingual narration"})
EN = VoiceInfo("en-voice-4", "Adam", category="professional", labels={"language": "en"})
OTHER = VoiceInfo("fr-voice-5", "Zoé", category="generated", labels={"language": "fr"})


class _Synth:
    def __init__(self, fail_on: str | None = None):
        self.calls: list[tuple[str, str, dict]] = []
        self.fail_on = fail_on

    def __call__(self, text, voice_id, settings_):
        self.calls.append((text, voice_id, dict(settings_)))
        if voice_id == self.fail_on:
            raise RuntimeError("quota ElevenLabs dépassé")
        return f"mp3:{voice_id}:{settings_['speed']}".encode()


# -- candidats -------------------------------------------------------------------------------------------------
def test_suggestions_only_come_from_the_catalogue_french_first_then_multilingual_never_another_language():
    suggested = vc.suggest_candidates([EN, MULTI, FR, FR2, OTHER])
    assert [v.voice_id for v in suggested] == ["fr-voice-1", "fr-voice-2", "fr-voice-5"]
    assert [v.voice_id for v in vc.suggest_candidates([EN, MULTI, FR])] == ["fr-voice-1", "multi-3"]
    assert vc.suggest_candidates([EN]) == []  # jamais une voix anglaise imposée faute de mieux


def test_the_already_saved_voice_comes_first_when_it_is_in_the_catalogue():
    suggested = vc.suggest_candidates([FR, FR2, OTHER, EN], current_voice_id="en-voice-4")
    assert suggested[0] is EN and len(suggested) == vc.MAX_CANDIDATES
    # Un identifiant absent du catalogue n'est jamais fabriqué en voix.
    assert all(v.voice_id != "inconnu" for v in vc.suggest_candidates([FR], current_voice_id="inconnu"))


# -- profils ---------------------------------------------------------------------------------------------------
def test_profiles_only_use_documented_settings_within_range_and_vary_speed():
    speeds = []
    for item in vc.PROFILES:
        for model in vc.MODELS:
            values = item.settings(model)
            assert set(values) == {"model_id", "stability", "similarity_boost", "style", "use_speaker_boost", "speed"}
            assert 0 <= values["stability"] <= 1 and 0 <= values["style"] <= 1 and 0.7 <= values["speed"] <= 1.2
        speeds.append(item.speed)
    assert speeds == sorted(speeds) and len(set(speeds)) == 3


def test_eleven_v3_only_receives_one_of_its_three_stability_levels():
    for item in vc.PROFILES:
        assert item.settings("eleven_v3")["stability"] in (0.0, 0.5, 1.0)


def test_profile_settings_are_accepted_by_the_character_sheet_validation():
    from lody.characters import validate_voice_direction

    for item in vc.PROFILES:
        assert validate_voice_direction(item.settings("eleven_multilingual_v2"))["speed"] == item.speed


def test_sample_dialogue_is_about_sdi_and_bnc():
    assert "SDI" in vc.SAMPLE_DIALOGUE and "BNC" in vc.SAMPLE_DIALOGUE and len(vc.SAMPLE_DIALOGUE) < 300


# -- empreinte et cache ----------------------------------------------------------------------------------------
def test_fingerprint_changes_with_voice_model_settings_or_text_and_is_stable_otherwise():
    base = vc.profile("vif").settings("eleven_multilingual_v2")
    ref = vc.fingerprint("v1", base, "Bonjour")
    assert ref == vc.fingerprint("v1", dict(reversed(list(base.items()))), "Bonjour")
    assert ref != vc.fingerprint("v2", base, "Bonjour")
    assert ref != vc.fingerprint("v1", vc.profile("vif").settings("eleven_v3"), "Bonjour")
    assert ref != vc.fingerprint("v1", {**base, "speed": 1.0}, "Bonjour")
    assert ref != vc.fingerprint("v1", base, "Bonjour !")


def test_audio_cache_roundtrip_and_rejects_path_tricks(tmp_path):
    cache = vc.AudioCache(tmp_path)
    key = vc.fingerprint("v1", {}, "x")
    assert cache.get(key) is None and not cache.has(key)
    cache.put(key, b"abc")
    assert cache.get(key) == b"abc" and cache.has(key)
    with pytest.raises(ValueError):
        cache.path("../../etc/passwd")


def test_default_cache_lives_in_the_lody_data_dir(lody_env):
    assert vc.AudioCache().root == settings.data_dir() / "voice-samples"


# -- plan, estimation, accord ----------------------------------------------------------------------------------
def test_comparison_plan_is_voices_times_profiles_same_text_capped_at_three_voices():
    plan = vc.comparison_plan([FR, FR2, OTHER, EN], ["naturel", "vif", "vif"])
    assert len(plan) == 6 and {s.text for s in plan} == {vc.SAMPLE_DIALOGUE}
    assert "en-voice-4" not in {s.voice.voice_id for s in plan}
    with pytest.raises(ValueError):
        vc.comparison_plan([FR], ["vif"], model_id="modele-invente")


def test_estimate_before_any_call_counts_only_uncached_characters(tmp_path):
    cache = vc.AudioCache(tmp_path)
    plan = vc.comparison_plan([FR, FR2, OTHER], ["vif"])
    book = costing.PriceBook(currency="EUR", rates={("voice", "elevenlabs"): Decimal("0.30")})
    first = vc.estimate_samples(plan, cache, book)
    assert (first.total, first.to_generate, first.cached) == (3, 3, 0)
    assert first.characters == 3 * len(vc.SAMPLE_DIALOGUE)
    exact = Decimal("0.30") * first.characters / 1000
    assert exact <= first.amount < exact + Decimal("0.01")  # arrondi au centime supérieur, jamais sous-estimé
    assert "≈" in first.describe() and "€" in first.describe()

    cache.put(plan[0].key, b"x")
    second = vc.estimate_samples(plan, cache, book)
    assert (second.to_generate, second.cached) == (2, 1)
    assert "déjà en cache" in second.describe()


def test_estimate_without_a_configured_rate_is_never_presented_as_free(tmp_path):
    estimate = vc.estimate_samples(vc.comparison_plan([FR], ["vif"]), vc.AudioCache(tmp_path), costing.PriceBook())
    assert estimate.amount is None and not estimate.free
    assert "non configuré" in estimate.describe()


def test_generation_is_refused_without_explicit_approval(tmp_path):
    synth = _Synth()
    with pytest.raises(PermissionError):
        vc.generate_missing(vc.comparison_plan([FR], ["vif"]), vc.AudioCache(tmp_path), synth, approved=False)
    assert synth.calls == []


def test_generation_only_calls_for_missing_samples_and_reuses_the_cache(tmp_path):
    cache = vc.AudioCache(tmp_path)
    plan = vc.comparison_plan([FR, FR2], ["naturel", "vif"])
    synth = _Synth()
    outcome = vc.generate_missing(plan, cache, synth, approved=True)
    assert len(synth.calls) == 4 and len(outcome.generated) == 4 and not outcome.errors
    text, voice_id, sent = synth.calls[0]
    assert text == vc.SAMPLE_DIALOGUE and voice_id == "fr-voice-1" and sent["model_id"] == "eleven_multilingual_v2"

    again = vc.generate_missing(plan, cache, synth, approved=True)
    assert len(synth.calls) == 4 and len(again.reused) == 4  # aucun nouvel appel payant
    assert vc.estimate_samples(plan, cache, costing.PriceBook()).free


def test_one_failing_sample_does_not_block_the_others(tmp_path):
    cache = vc.AudioCache(tmp_path)
    plan = vc.comparison_plan([FR, FR2], ["vif"])
    outcome = vc.generate_missing(plan, cache, _Synth(fail_on="fr-voice-1"), approved=True)
    assert outcome.errors[plan[0].key] == "quota ElevenLabs dépassé"
    assert cache.has(plan[1].key) and not cache.has(plan[0].key)


def test_approval_token_changes_when_the_plan_changes():
    a = vc.approval_token(vc.comparison_plan([FR, FR2], ["vif"]))
    assert a == vc.approval_token(vc.comparison_plan([FR, FR2], ["vif"]))
    assert a != vc.approval_token(vc.comparison_plan([FR, FR2], ["vif", "naturel"]))
    assert a != vc.approval_token(vc.comparison_plan([FR, FR2], ["vif"], text="Autre texte."))


# -- attribution durable ---------------------------------------------------------------------------------------
def _voltix(lody_env):
    projects = ProjectRepository(settings.db_path())
    project = projects.create(name="Voltix TV")
    repo = CharacterRepository(settings.db_path())
    voltix = repo.create(project.id, name="Voltix", role="Mascotte", voice_provider="edge", voice_name="piper")
    other = repo.create(project.id, name="Gaston")
    return repo, project, voltix, other


def test_assign_default_voice_persists_voice_and_settings_on_this_character_only(lody_env):
    repo, project, voltix, other = _voltix(lody_env)
    updated = vc.assign_default_voice(repo, project.id, voltix.id, FR, vc.profile("vif"), "eleven_v3")
    assert (updated.voice_provider, updated.voice_name, updated.external_voice_id) == ("elevenlabs", "Léa", "fr-voice-1")
    assert updated.voice_direction == vc.profile("vif").settings("eleven_v3")
    assert updated.role == "Mascotte"  # aucun autre champ touché
    untouched = repo.get(project.id, other.id)
    assert untouched.external_voice_id == "" and untouched.voice_direction == {}
    # Relu plus tard (nouvel épisode) : la voix est toujours là, sans la renseigner à nouveau.
    assert vc.character_voice_settings(repo.get(project.id, voltix.id)) == ("fr-voice-1", updated.voice_direction)


# -- contrat pour le pipeline 2D -------------------------------------------------------------------------------
def test_without_a_chosen_voice_the_character_is_provisional_and_lines_cannot_be_planned(lody_env, tmp_path):
    repo, project, voltix, _ = _voltix(lody_env)
    assert vc.character_voice_settings(voltix) is None
    assert vc.character_voice_fingerprint(voltix) == "provisional"
    with pytest.raises(ValueError):
        vc.plan_lines(voltix, ["Bonjour"], vc.AudioCache(tmp_path))


def test_revoice_only_recomputes_audio_dependent_stages(lody_env):
    repo, project, voltix, _ = _voltix(lody_env)
    before = vc.character_voice_fingerprint(voltix)
    voltix = vc.assign_default_voice(repo, project.id, voltix.id, FR, vc.profile("vif"))
    plan = vc.revoice_plan(before, voltix)
    assert plan["changed"] and set(plan["recompute"]) == {"voice", "visemes", "subtitles", "mix"}
    assert {"illustrations", "rig", "storyboard"} <= set(plan["preserve"])
    assert vc.revoice_plan(plan["fingerprint"], voltix) == {**plan, "changed": False, "recompute": []}


def test_plan_lines_reuses_unchanged_lines_and_estimates_only_new_ones(lody_env, tmp_path):
    repo, project, voltix, _ = _voltix(lody_env)
    voltix = vc.assign_default_voice(repo, project.id, voltix.id, FR, vc.profile("vif"))
    cache = vc.AudioCache(tmp_path)
    lines, estimate = vc.plan_lines(voltix, ["Ligne une.", "Ligne deux."], cache, costing.PriceBook())
    assert estimate.to_generate == 2
    cache.put(lines[0].key, b"x")
    lines, estimate = vc.plan_lines(voltix, ["Ligne  une.", "Ligne deux modifiée."], cache, costing.PriceBook())
    assert [line.cached for line in lines] == [True, False]  # espaces normalisés : même réplique
    assert estimate.to_generate == 1 and estimate.characters == len("Ligne deux modifiée.")
