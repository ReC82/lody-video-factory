"""Métadonnées de publication : génération locale, français, règles anti-conseil financier, validation, isolation."""

from __future__ import annotations

import re

import pytest

from lody.generation import publication as pub

SUBJECT = "Crée le Short #0 de 45 à 60 secondes : « Pourquoi je lance cette série ». J’explique que je ne suis pas expert financier."
SCRIPT = ("Si tu entends parler de crypto, de NFT ou de jeux Web3 et que tu as l’impression d’arriver au milieu d’une conversation "
          "incompréhensible, cette série est pour toi. Je ne suis pas expert financier, je ne vais pas te dire quoi acheter, et ce "
          "contenu n’est pas un conseil d’investissement. Depuis plusieurs années, je teste des jeux Web3, c’est-à-dire des jeux qui "
          "utilisent la blockchain, une sorte de registre public. Concrètement, dans certains jeux, un objet peut être lié à un NFT, "
          "donc un certificat numérique. Ça peut être intéressant techniquement, mais ça ne veut pas dire que ça a de la valeur. "
          "Le piège, c’est de confondre usage, spéculation et promesse de gain. Il y a des arnaques et des pertes possibles. "
          "L’idée à retenir : comprendre avant de cliquer. Dans le prochain épisode, on repart de zéro : c’est quoi la crypto ?")
PROFILE = {
    "playlist": "LodyCrypto — Comprendre le Web3 gaming", "category": "Éducation",
    "hashtags": ["#LodyCrypto", "#Crypto", "#Web3Gaming"], "tags": ["crypto pour débutants", "web3 gaming"],
    "disclaimer": "Contenu pédagogique : ceci n’est pas un conseil d’investissement ni une recommandation d’achat.",
    "made_for_kids": False, "no_financial_claims": True,
    "series_blurb": "Cette série part des bases de la crypto pour aller vers le Web3 gaming, sans promesse de gain.",
    "comment_prompt": "Quel mot du Web3 te pose problème ? On l’explique dans un prochain épisode."}


def _generate(**overrides):
    fields = dict(project_name="LodyCrypto", subject=SUBJECT, script=SCRIPT, language="fr-FR", content_type="pedagogique",
                  description="Une vidéo pédagogique pour débutants.", version_label="V1", duration_seconds=56.1, profile=PROFILE)
    fields.update(overrides)
    return pub.generate_metadata(pub.PublicationInput(**fields))


def test_lodycrypto_metadata_is_complete_french_and_project_specific():
    meta = _generate()
    assert meta["title"] == "Pourquoi je lance cette série" and len(meta["title_alternatives"]) == 2
    assert len({meta["title"], *meta["title_alternatives"]}) == 3 and all(len(t) <= pub.TITLE_MAX for t in [meta["title"], *meta["title_alternatives"]])
    assert meta["video_language"] == "fr" and meta["text_language"] == "fr" and meta["made_for_kids"] is False
    assert pub.audience_label(meta["made_for_kids"]) == "Non destinée aux enfants"
    assert meta["category"] == "Éducation" and meta["playlist"] == "LodyCrypto — Comprendre le Web3 gaming"
    assert meta["hashtags"][:3] == ["#LodyCrypto", "#Crypto", "#Web3Gaming"] and 3 <= len(meta["hashtags"]) <= pub.YOUTUBE_HASHTAGS_MAX
    assert all(not t.startswith("#") for t in meta["tags"]) and "crypto pour débutants" in meta["tags"]
    assert sum(len(t) + 1 for t in meta["tags"]) <= pub.TAGS_MAX_CHARS
    assert meta["file_name"] == "lodycrypto-v1-pourquoi-je-lance-cette-serie.mp4"
    description = meta["description_youtube"]
    assert "Dans cette vidéo :" in description and "Sous-titres en français disponibles." in description
    assert PROFILE["disclaimer"] in description and "Web3 gaming" in description and "#LodyCrypto" in description
    assert meta["pinned_comment"] == PROFILE["comment_prompt"] and meta["short_description"].startswith("Si tu entends")
    assert len(meta["short_description"]) <= pub.SHORT_DESCRIPTION_MAX + 1 and meta["warning"] == ""


def test_generated_texts_have_no_financial_promise_or_advice():
    meta = _generate()
    assert pub.financial_claims(pub.youtube_text(meta) + pub.tiktok_text(meta)) == []
    assert "garanti" not in " ".join(pub.all_text_fields(meta)).lower().replace("promesse", "")


def test_a_risky_script_is_never_quoted_into_the_proposals_and_raises_a_warning():
    risky = ("Cette crypto peut te faire gagner de l’argent facilement. Investis maintenant, c’est un rendement garanti. "
             "Beaucoup de gens veulent devenir riche avec elle. Voici un conseil d’investissement : achète maintenant. "
             "On explique aussi comment fonctionne un wallet et ce qu’est une blockchain pour comprendre. "
             "Le prochain épisode parle des risques réels et de la sécurité de tes comptes.")
    meta = _generate(script=risky, subject="Explique ce qu’est une crypto")
    text = pub.youtube_text(meta) + pub.tiktok_text(meta)
    for phrase in ("gagner de l’argent", "rendement garanti", "devenir riche", "Investis maintenant", "achète maintenant"):
        assert phrase not in text, phrase
    assert pub.financial_claims(text) == []


@pytest.mark.parametrize("text,expected", [
    ("Ce token est garanti de faire x10 cette année", 2),
    ("Investissez maintenant pour un rendement de 300 % de gains", 3),
    ("Tu vas devenir riche, c’est sans risque", 2),
    ("Le prochain bitcoin va faire fortune", 2),
    ("Voici mon conseil d’investissement : tout miser", 1),
    ("Achetez avant que ce soit trop tard", 1),
])
def test_financial_claims_are_detected(text, expected):
    assert len(pub.financial_claims(text)) >= expected, pub.financial_claims(text)


@pytest.mark.parametrize("text", [
    "Ceci n’est pas un conseil d’investissement.",
    "Aucune promesse de rendement garanti : jamais garanti.",
    "Ce contenu ne constitue pas une recommandation d’achat.",
    "Je ne te dis pas de gagner de l’argent : je ne suis pas expert financier.",
    "Comprendre la blockchain, les wallets et les NFT sans jargon.",
    "",
])
def test_negated_and_neutral_wording_is_not_flagged(text):
    assert pub.financial_claims(text) == []


def test_keywords_prefer_acronyms_digits_and_repeated_nouns_over_verbs_and_generic_words():
    kws = pub.keywords(SCRIPT)
    assert "NFT" in kws and "Web3" in kws and "crypto" in kws
    for weak in ("acheter", "cliquer", "conversation", "jeux", "incompréhensible"):
        assert weak not in kws
    assert pub.hashtag("Web3 gaming") == "#Web3Gaming" and pub.hashtag("NFT") == "#NFT" and pub.hashtag("Éducation créative") == "#EducationCreative"


def test_tiktok_kit_is_short_adapted_and_reasonable():
    meta = _generate()
    assert len(meta["tiktok_caption"]) <= pub.TIKTOK_CAPTION_MAX and len(meta["tiktok_hashtags"]) <= pub.TIKTOK_HASHTAGS_MAX
    assert meta["tiktok_hook"] == "Pourquoi je lance cette série" and meta["tiktok_hashtags"][0] == "#LodyCrypto"
    assert "#fyp" not in " ".join(meta["tiktok_hashtags"]).lower()          # pas de liste artificielle
    assert not meta["tiktok_caption"].endswith((" ,", " :")) and meta["tiktok_comment"]


def test_platform_defaults_are_neutral_without_a_project_profile():
    business = re.compile(r"crypto|blockchain|web3|nft|broadcast|régie|fill|conseil d’investissement|LodyCrypto", re.I)
    meta = _generate(project_name="Cuisine Simple", subject="Explique comment réussir une omelette moelleuse en trois étapes.",
                     script="Casse deux œufs dans un bol. Bats-les avec une pincée de sel. Verse dans la poêle chaude et remue doucement. "
                            "Plie l’omelette quand elle est encore brillante. Sers-la aussitôt avec une salade.",
                     profile={}, description="Des recettes courtes.")
    assert not business.search(pub.youtube_text(meta) + pub.tiktok_text(meta))
    assert meta["playlist"] == "Cuisine Simple" and meta["category"] == "Éducation" and meta["hashtags"][0] == "#CuisineSimple"
    assert meta["made_for_kids"] is False and meta["pinned_comment"].startswith("Quelle question")


def test_english_projects_get_english_language_codes_and_no_french_typography_changes():
    meta = _generate(language="en-US", script="Bring water to a boil. Add the pasta and stir. Drain it when it is tender.", subject="How to cook pasta")
    assert meta["video_language"] == "en" and "Subtitles available." in meta["description_youtube"]


def test_manual_edits_are_validated_and_normalised():
    clean, errors = pub.validate_metadata({
        "title": "  L’  idée   de base  ", "hashtags": "#Un deux, #Trois", "tags": "a, b\n#c, a", "made_for_kids": True,
        "description_youtube": "Ligne 1\r\nc’ est bien", "file_name": "mon-fichier.mp4", "title_alternatives": "Alt un\nAlt deux"})
    assert errors == {} and clean["title"] == "L’idée de base" and clean["hashtags"] == ["#Un", "#deux", "#Trois"]
    assert clean["tags"] == ["a", "b", "c"] and clean["made_for_kids"] is True
    assert clean["description_youtube"] == "Ligne 1\nc’est bien" and clean["title_alternatives"] == ["Alt un", "Alt deux"]
    _, errors = pub.validate_metadata({"title": "", "file_name": "../evil.mp4", "hashtags": ["#" + "x" * 40], "tags": ["y" * 300, "z" * 300]})
    assert set(errors) == {"title", "file_name", "hashtags", "tags"}
    _, errors = pub.validate_metadata({"title": "x" * 101, "title_alternatives": ["a", "b", "c"]})
    assert "title" in errors and "title_alternatives" in errors


def test_secrets_are_refused_in_manual_edits():
    _, errors = pub.validate_metadata({"description_youtube": "voici ma clé sk-abcdefghijklmnopqrstuvwxyz123456"})
    assert "_" in errors and "clé" in errors["_"]


def test_text_exports_contain_every_field_for_copy_and_paste():
    meta = _generate()
    youtube, tiktok = pub.youtube_text(meta), pub.tiktok_text(meta)
    for expected in (meta["title"], meta["title_alternatives"][0], meta["description_youtube"], " ".join(meta["hashtags"]),
                     ", ".join(meta["tags"]), meta["playlist"], meta["category"], "Non destinée aux enfants", meta["pinned_comment"]):
        assert expected in youtube
    for expected in (meta["tiktok_caption"], meta["tiktok_hook"], " ".join(meta["tiktok_hashtags"]), meta["tiktok_comment"]):
        assert expected in tiktok
    assert "/MoneyPrinterTurbo" not in youtube + tiktok and "tasks/" not in youtube + tiktok


def test_thumbnail_text_is_short_truthful_and_free_of_promises():
    meta = _generate()
    assert pub.thumbnail_text_from(meta, SUBJECT, SCRIPT) == "Pourquoi je lance cette série"
    assert pub.thumbnail_text_from({"title": "Un titre vraiment beaucoup trop long pour la miniature"}, "sans guillemets", SCRIPT).endswith("simplement")
    for text in (pub.thumbnail_text_from(meta, SUBJECT, SCRIPT),):
        assert 2 <= len(text.split()) <= 5 and pub.financial_claims(text) == []
