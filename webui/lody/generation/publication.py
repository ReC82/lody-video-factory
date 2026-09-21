"""Métadonnées de publication : génération locale, validation, contrôle anti-conseil financier, exports texte.

Tout est **local, déterministe et gratuit** : aucune donnée n'est envoyée à un fournisseur. Les propositions sont construites
UNIQUEMENT depuis le projet de la production (son profil de publication), son sujet et son script final : aucune valeur
d'un autre projet, aucun vocabulaire métier dans les défauts de la plateforme. Toutes les valeurs sont modifiables ensuite.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from lody.generation import typography
from lody.generation.storyboard import split_sentences
from lody.secrets_guard import SECRET_MESSAGE, find_secret_path

TITLE_MAX = 70          # proposition ; YouTube accepte 100
TITLE_HARD_MAX = 100
SHORT_DESCRIPTION_MAX = 160
YOUTUBE_HASHTAGS_MAX = 5
TAGS_MAX_CHARS = 450    # YouTube : 500 caractères de mots-clés au total
TIKTOK_HASHTAGS_MAX = 4
TIKTOK_CAPTION_MAX = 150
DEFAULT_CATEGORIES = {"pedagogique": "Éducation", "tutoriel": "Éducation", "actualite": "Actualités et politique",
                      "storytelling": "Divertissement", "divertissement": "Divertissement"}
TEXT_FIELDS = ("title", "short_description", "description_youtube", "pinned_comment", "category", "playlist", "video_language",
               "text_language", "file_name", "warning", "tiktok_caption", "tiktok_hook", "tiktok_comment")
LIST_FIELDS = ("title_alternatives", "hashtags", "tags", "tiktok_hashtags")
BOOL_FIELDS = ("made_for_kids",)
FIELD_LABELS = {
    "title": "Titre principal", "title_alternatives": "Titres alternatifs", "short_description": "Description courte",
    "description_youtube": "Description YouTube", "hashtags": "Hashtags", "tags": "Tags / mots-clés",
    "pinned_comment": "Commentaire épinglé suggéré", "category": "Catégorie suggérée", "playlist": "Playlist suggérée",
    "video_language": "Langue de la vidéo", "text_language": "Langue du titre et de la description",
    "made_for_kids": "Destinée aux enfants", "file_name": "Nom de fichier", "warning": "Avertissement",
    "tiktok_caption": "Légende TikTok / Shorts", "tiktok_hook": "Texte d’accroche", "tiktok_hashtags": "Hashtags TikTok / Shorts",
    "tiktok_comment": "Commentaire suggéré",
}
LIMITS = {"title": 100, "short_description": 300, "description_youtube": 5000, "pinned_comment": 500, "category": 60, "playlist": 100,
          "video_language": 8, "text_language": 8, "file_name": 100, "warning": 500, "tiktok_caption": 300, "tiktok_hook": 100,
          "tiktok_comment": 300}

_STOPWORDS = frozenset("""
a à au aux avec ce ces cet cette ceci cela ça car ce-qui dans de des du elle elles en et est été être être eux il ils je
la le les leur leurs lui ma mais me même mes moi mon ne ni nos notre nous on ou où par pas peu plus pour pourquoi qu que
quel quelle quels quelles qui quoi sa sans se ses si son sont sur ta te tes toi ton tu un une vos votre vous y ont était
fait faire peut peuvent veut veux voir vais va vont comme donc alors aussi ainsi après avant chez cela ceux depuis encore
entre très tout tous toute toutes trop bien moins sous vers déjà ici là quand comment combien dont lorsque puis sera seront
ai as avons avez aurai serait pourrait peux pouvez faut fallait dit dire dis certains certaines autre autres chaque quelque
quelques cependant pourtant toujours jamais rien plusieurs souvent seulement simplement concrètement techniquement
""".split())

# -- anti-conseil financier ----------------------------------------------------------------------------------------------
_NEGATION = re.compile(r"\b(ne|n[’']|pas|aucun|aucune|sans|jamais|nul|non)\b", re.IGNORECASE)
_CLAIM_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bgaranti(?:e|s|es)?\b", "promesse de garantie"),
    (r"\brendements?\b", "promesse de rendement"),
    (r"\bgagner (?:de l[’']argent|gros|beaucoup|des millions?)\b", "promesse de gain"),
    (r"\bdevenir (?:riche|millionnaire)\b", "promesse d’enrichissement"),
    (r"\bfaire fortune\b", "promesse d’enrichissement"),
    (r"\b(?:argent facile|revenu passif|revenus passifs)\b", "promesse de gain facile"),
    (r"\bsans risques?\b", "absence de risque affirmée"),
    (r"\b(?:investis(?:sez)?|ach[eè]te(?:z)?)\b(?! pas)", "incitation à acheter ou investir"),
    (r"\b(?:à acheter|a acheter) (?:maintenant|vite|absolument)\b", "incitation à acheter"),
    (r"\bx ?\d{2,}\b", "multiplication de gains annoncée"),
    (r"\b\d{2,}\s?%\s+(?:de\s+)?(?:gains?|profits?|rendements?)\b", "pourcentage de gain annoncé"),
    (r"\b(?:profits?|plus-values?) (?:assuré|garanti|rapide)", "profit annoncé"),
    (r"\b(?:prochain|next) (?:bitcoin|ethereum|solana)\b", "promesse spéculative"),
    (r"\bopportunité (?:unique|à saisir|en or)\b", "promesse d’opportunité"),
    (r"\b(?:mon|nos|ce|voici (?:mes|nos)|mes) conseils? d[’']investissement\b", "conseil d’investissement"),
    (r"\b(?:lambo|to the moon|moon(?:er)?)\b", "vocabulaire spéculatif"),
)
_DISCLAIMER_SENTENCE = re.compile(r"[^.!?\n]*\b(?:n[’']est pas|ne constitue pas|ne sont pas)\b[^.!?\n]*(?:conseil|recommandation)[^.!?\n]*[.!?]?", re.IGNORECASE)


def financial_claims(text: str) -> list[str]:
    """Formulations assimilables à une promesse financière ou à un conseil d'investissement (liste vide = rien à signaler).

    Les phrases qui NIENT explicitement un conseil (« ceci n'est pas un conseil d'investissement ») sont ignorées, ainsi
    que les termes précédés d'une négation proche (« aucune promesse de rendement garanti », « jamais garanti »).
    """
    cleaned = _DISCLAIMER_SENTENCE.sub(" ", text or "")
    found: list[str] = []
    for pattern, reason in _CLAIM_PATTERNS:
        for match in re.finditer(pattern, cleaned, re.IGNORECASE):
            before = cleaned[max(0, match.start() - 40):match.start()]
            if _NEGATION.search(before) and reason != "conseil d’investissement":
                continue
            entry = f"« {match.group(0).strip()} » : {reason}"
            if entry not in found:
                found.append(entry)
    return found


# -- utilitaires -------------------------------------------------------------------------------------------------------------
def primary_language(code: str) -> str:
    return (code or "fr").lower().replace("_", "-").split("-")[0][:3] or "fr"


def slugify(text: str, limit: int = 60) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    return slug[:limit].rstrip("-") or "video"


def _cut(text: str, limit: int) -> str:
    """Coupe au mot près, sans laisser de ponctuation pendante."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text.rstrip(" ,;:—-")
    return text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:—-.")


def _shorten(text: str, limit: int) -> str:
    """Raccourcit sans couper une idée en plein milieu : à la dernière virgule/point-virgule/deux-points, sinon au mot + « … »."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    head = text[:limit]
    boundary = max(head.rfind(sep) for sep in (", ", "; ", " : ", " — "))
    if boundary >= limit * 0.45:
        return head[:boundary].rstrip(" ,;:—") + "…"
    return head.rsplit(" ", 1)[0].rstrip(" ,;:—.") + "…"


def _capitalize(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def hashtag(word: str) -> str:
    """« Web3 gaming » → #Web3Gaming ; « NFT » → #NFT ; accents retirés (les hashtags ne les gardent pas partout)."""
    parts = re.findall(r"[A-Za-zÀ-ÿ0-9]+", word)
    joined = "".join(p if p.isupper() or p[:1].isdigit() or any(c.isdigit() for c in p) else p[:1].upper() + p[1:] for p in parts)
    plain = unicodedata.normalize("NFKD", joined).encode("ascii", "ignore").decode("ascii")
    return "#" + plain[:29] if plain else ""


# Mots trop génériques ou verbaux pour servir de mot-clé (jamais de vocabulaire métier ici).
_WEAK = frozenset("""jeu jeux chose choses exemple idée idees valeur monde fois point objet objets registre numérique
acheter cliquer connecter comprendre tester disparaître partir repart conversation incompréhensible milieu impression
années année plusieurs certain intéressant possible possibles réels réel vraiment""".split())


def _stem(key: str) -> str:
    return key[:-1] if len(key) > 4 and key.endswith(("s", "x")) else key


def keywords(script: str, limit: int = 6) -> list[str]:
    """Mots-clés du script : sigles (NFT), mots avec chiffre (Web3) et mots répétés, hors mots vides, verbes et mots génériques."""
    words = re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ0-9’'\-]{1,}|[A-Za-zÀ-ÿ]+\d+", script or "")
    counts: Counter[str] = Counter()
    first: dict[str, tuple[int, str]] = {}
    for position, raw in enumerate(words):
        word = raw.strip("’'-")
        key = _stem(word.lower())
        if len(word) < 3 or word.lower() in _STOPWORDS or word.lower() in _WEAK or key in _WEAK or "’" in word or "'" in word:
            continue
        if word.lower().endswith(("er", "ment")) and not word.isupper():
            continue  # infinitifs et adverbes : pas des mots-clés
        counts[key] += 1
        first.setdefault(key, (position, word if word.isupper() or any(c.isdigit() for c in word) else word.lower()))
    def eligible(key: str) -> bool:
        original = first[key][1]
        return counts[key] >= 2 or original.isupper() or any(c.isdigit() for c in original)
    def score(key: str) -> float:
        original = first[key][1]
        bonus = 3 if original.isupper() and len(original) >= 2 else (2 if any(c.isdigit() for c in original) else 0)
        return counts[key] + bonus + min(len(key), 12) / 12
    ranked = sorted((k for k in counts if eligible(k)), key=lambda k: (-score(k), first[k][0]))[:limit]
    ranked.sort(key=lambda k: first[k][0])
    return [first[k][1] for k in ranked]


def _join_list(items: list[str]) -> str:
    return items[0] if len(items) == 1 else (", ".join(items[:-1]) + " et " + items[-1] if items else "")


def parse_hashtags(text: str) -> list[str]:
    tags = []
    for raw in re.split(r"[\s,;]+", text or ""):
        raw = raw.strip()
        if raw:
            tag = raw if raw.startswith("#") else "#" + raw
            if tag not in tags:
                tags.append(tag)
    return tags


def parse_tags(text: str) -> list[str]:
    tags: list[str] = []
    for raw in re.split(r"[,\n;]+", text or ""):
        tag = " ".join(raw.replace("#", "").split())
        if tag and tag.lower() not in {t.lower() for t in tags}:
            tags.append(tag)
    return tags


# -- génération --------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class PublicationInput:
    project_name: str
    subject: str
    script: str
    language: str = "fr-FR"
    content_type: str = "pedagogique"
    description: str = ""
    version_label: str = "V1"
    duration_seconds: float | None = None
    profile: dict[str, Any] = field(default_factory=dict)   # brief.publication du projet ; vide = défauts neutres


def _safe(text: str) -> str:
    """Le texte, ou « » s'il contient une promesse financière : une proposition ne reprend jamais une telle formulation."""
    return "" if financial_claims(text) else text


def _quoted_title(subject: str) -> str:
    match = re.search(r"«\s*([^»]{6,90}?)\s*»|\"([^\"]{6,90})\"", subject or "")
    return _safe(_cut((match.group(1) or match.group(2)).strip(" .!?:;"), TITLE_MAX)) if match else ""


def _headline(hook: str) -> str:
    clause = re.split(r"[,;:—]", hook, maxsplit=1)[0].strip()
    text = clause if len(clause.split()) >= 4 else hook
    return _cut(text.strip(" .!?"), TITLE_MAX)


def _titles(inp: PublicationInput, kws: list[str], sentences: list[str], duration_label: str) -> list[str]:
    quoted = _quoted_title(inp.subject)
    hook = _safe(_headline(sentences[0])) if sentences else ""
    weak_hook = not hook or re.match(r"^(si|quand|lorsque|et|mais|donc)\b", hook, re.IGNORECASE) is not None
    top = kws[:3]
    keyword_title = f"{_capitalize(_join_list(top))} : l’essentiel" if top else ""
    main = quoted or ("" if weak_hook else hook) or keyword_title or _safe(_cut(inp.subject, TITLE_MAX)) or inp.project_name
    candidates = [
        f"{_capitalize(kws[0])} : ce qu’il faut comprendre" if kws else "",
        f"{_capitalize(_join_list(kws[:2]))} expliqués simplement" if len(kws) >= 2 else "",
        f"{_capitalize(kws[0])} en {duration_label}" if kws and duration_label else "",
        keyword_title, hook, f"{main} (explication simple)",
    ]
    titles = [_capitalize(_cut(main, TITLE_MAX))]
    for candidate in candidates:
        candidate = _capitalize(_cut(candidate, TITLE_MAX))
        if candidate and candidate.lower() not in {t.lower() for t in titles} and not financial_claims(candidate):
            titles.append(candidate)
        if len(titles) == 3:
            break
    while len(titles) < 3:
        titles.append(f"{titles[0]} — {inp.version_label}")
    return [typography.normalize_for_language(t, inp.language) for t in titles[:3]]


def _duration_label(seconds: float | None) -> str:
    if not seconds:
        return ""
    return "moins d’une minute" if seconds <= 60 else ("une minute" if seconds <= 75 else f"{round(seconds / 60)} minutes")


def generate_metadata(inp: PublicationInput) -> dict[str, Any]:
    """Toutes les propositions de publication, prêtes à être relues et modifiées."""
    profile = inp.profile or {}
    lang = primary_language(inp.language)
    script = typography.normalize_for_language(inp.script, inp.language)
    sentences = split_sentences(script)
    kws = keywords(script)
    label = _duration_label(inp.duration_seconds)
    titles = _titles(inp, kws, sentences, label)
    title, alternatives = titles[0], titles[1:]

    def clean_sentence(sentence: str) -> bool:
        return not financial_claims(sentence)

    usable = [s for s in sentences if clean_sentence(s)]
    first_two = " ".join(usable[:2])
    intro = (first_two if len(first_two) <= 300 else usable[0]) if usable else _safe(_shorten(inp.subject, 260))
    intro = _shorten(intro, 300)
    short = _shorten(usable[0], SHORT_DESCRIPTION_MAX) if usable else _safe(_shorten(inp.subject, SHORT_DESCRIPTION_MAX))
    points = [_shorten(s.rstrip(".!? "), 115) for s in usable[2:-1] if len(s.split()) >= 5][:3]

    profile_tags = [t for t in profile.get("hashtags", []) if isinstance(t, str) and t.startswith("#")]
    topic_tags = [hashtag(k) for k in kws if hashtag(k)]
    project_tag = hashtag(inp.project_name)
    hashtags: list[str] = []
    for tag in [*profile_tags, *([project_tag] if not profile_tags and project_tag else []), *topic_tags]:
        if tag and tag.lower() not in {h.lower() for h in hashtags}:
            hashtags.append(tag)
    hashtags = hashtags[:YOUTUBE_HASHTAGS_MAX]

    tags: list[str] = []
    for tag in [*(t for t in profile.get("tags", []) if isinstance(t, str)), inp.project_name, *kws]:
        tag = " ".join(str(tag).replace("#", "").split())
        if tag and tag.lower() not in {t.lower() for t in tags} and sum(len(t) + 1 for t in tags) + len(tag) <= TAGS_MAX_CHARS:
            tags.append(tag)

    disclaimer = str(profile.get("disclaimer", "")).strip()
    about_source = str(profile.get("series_blurb", "")).strip() or inp.description
    about = _cut(about_source, 220) if about_source else ""
    lines = [intro, ""]
    if points:
        lines += ["Dans cette vidéo :", *[f"• {p}" for p in points], ""]
    if about:
        lines += [about, ""]
    if disclaimer:
        lines += [disclaimer, ""]
    lines += [" ".join(hashtags[:3]), "Sous-titres en français disponibles." if lang == "fr" else "Subtitles available."]
    description = typography.normalize_for_language("\n".join(line for line in lines).strip(), inp.language)

    comment_prompt = str(profile.get("comment_prompt", "")).strip() or "Quelle question veux-tu qu’on traite dans le prochain épisode ? Dis-le en commentaire."
    pinned = typography.normalize_for_language(comment_prompt, inp.language)
    file_name = f"{slugify(inp.project_name, 24)}-{slugify(inp.version_label, 6)}-{slugify(title, 40)}.mp4"
    tiktok_tags = (profile_tags[:2] + [t for t in topic_tags if t.lower() not in {p.lower() for p in profile_tags[:2]}])[:TIKTOK_HASHTAGS_MAX]
    tiktok_caption = _shorten(short, TIKTOK_CAPTION_MAX - sum(len(t) + 1 for t in tiktok_tags)) + ((" " + " ".join(tiktok_tags)) if tiktok_tags else "")
    hook_words = title.split()
    metadata: dict[str, Any] = {
        "title": title, "title_alternatives": alternatives, "short_description": short, "description_youtube": description,
        "hashtags": hashtags, "tags": tags, "pinned_comment": pinned,
        "category": str(profile.get("category", "")).strip() or DEFAULT_CATEGORIES.get(inp.content_type, "Éducation"),
        "playlist": str(profile.get("playlist", "")).strip() or inp.project_name,
        "video_language": lang, "text_language": lang, "made_for_kids": bool(profile.get("made_for_kids", False)),
        "file_name": file_name, "warning": "",
        "tiktok_caption": tiktok_caption, "tiktok_hook": " ".join(hook_words[:8]), "tiktok_hashtags": tiktok_tags,
        "tiktok_comment": pinned,
    }
    if profile.get("no_financial_claims"):
        problems = financial_claims(_all_text(metadata))
        if problems:
            metadata["warning"] = "Formulations à vérifier avant publication : " + " ; ".join(problems[:4])
    return metadata


def thumbnail_text_from(metadata: dict[str, Any], subject: str, script: str) -> str:
    """Texte court proposé pour la miniature (2 à 5 mots) : titre du sujet s'il est court, sinon mots-clés."""
    for candidate in (_quoted_title(subject), metadata.get("title", "")):
        words = candidate.split()
        if 2 <= len(words) <= 5 and not financial_claims(candidate):
            return " ".join(words)
    kws = keywords(script, 4)
    if len(kws) >= 2:
        return f"{kws[0]} & {kws[1]}, simplement"
    return " ".join((metadata.get("title", "") or "").split()[:4])


# -- validation d'une modification manuelle ---------------------------------------------------------------------------------------
def all_text_fields(metadata: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key, value in metadata.items():
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, list):
            values.extend(str(item) for item in value)
    return values


def _all_text(metadata: dict[str, Any]) -> str:
    return "\n".join(all_text_fields(metadata))


def validate_metadata(raw: dict[str, Any], language: str = "fr") -> tuple[dict[str, Any], dict[str, str]]:
    """Nettoie et valide une saisie manuelle ; retourne (valeurs, erreurs par champ). Aucun secret accepté."""
    clean: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for key in TEXT_FIELDS:
        if key not in raw:
            continue
        value = str(raw[key] or "").replace("\r\n", "\n").strip()
        if key not in ("description_youtube", "pinned_comment", "tiktok_comment", "warning", "tiktok_caption"):
            value = " ".join(value.split())
        if len(value) > LIMITS[key]:
            errors[key] = f"{FIELD_LABELS[key]} : {LIMITS[key]} caractères maximum."
        if key in ("title", "short_description", "description_youtube", "pinned_comment", "tiktok_caption", "tiktok_hook", "tiktok_comment"):
            value = typography.normalize_for_language(value, language)
        clean[key] = value
    if "title" in clean and not clean["title"]:
        errors["title"] = "Le titre ne peut pas être vide."
    if "file_name" in clean and clean["file_name"]:
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*\.mp4", clean["file_name"]):
            errors["file_name"] = "Nom de fichier : minuscules, chiffres, tirets, se terminant par .mp4."
    for key in LIST_FIELDS:
        if key not in raw:
            continue
        items = raw[key]
        if isinstance(items, str):
            items = parse_tags(items) if key == "tags" else (parse_hashtags(items) if "hashtags" in key else [i.strip() for i in items.splitlines() if i.strip()])
        items = [" ".join(str(i).split()) for i in items if str(i).strip()]
        clean[key] = items
    if "title_alternatives" in clean:
        if len(clean["title_alternatives"]) > 2 or any(len(t) > TITLE_HARD_MAX for t in clean["title_alternatives"]):
            errors["title_alternatives"] = "Deux titres alternatifs au maximum, 100 caractères chacun."
    for key, maximum in (("hashtags", 15), ("tiktok_hashtags", 10)):
        if key in clean and (len(clean[key]) > maximum or any(not re.fullmatch(r"#[^\s#]{1,29}", tag) for tag in clean[key])):
            errors[key] = f"Hashtags : {maximum} au maximum, sans espace, 30 caractères chacun."
    if "tags" in clean and sum(len(t) + 1 for t in clean["tags"]) > 500:
        errors["tags"] = "Tags : 500 caractères au total au maximum."
    if "made_for_kids" in raw:
        clean["made_for_kids"] = bool(raw["made_for_kids"])
    if find_secret_path(clean, opaque=False):
        errors["_"] = SECRET_MESSAGE
    return clean, errors


# -- exports texte ---------------------------------------------------------------------------------------------------------------------
def audience_label(made_for_kids: bool) -> str:
    return "Destinée aux enfants" if made_for_kids else "Non destinée aux enfants"


def youtube_text(meta: dict[str, Any]) -> str:
    alternatives = "\n".join(f"- {t}" for t in meta.get("title_alternatives", [])) or "-"
    return (
        f"TITRE\n{meta.get('title', '')}\n\nTITRES ALTERNATIFS\n{alternatives}\n\nDESCRIPTION\n{meta.get('description_youtube', '')}\n\n"
        f"HASHTAGS\n{' '.join(meta.get('hashtags', []))}\n\nTAGS (séparés par des virgules)\n{', '.join(meta.get('tags', []))}\n\n"
        f"PLAYLIST\n{meta.get('playlist', '')}\n\nCATÉGORIE\n{meta.get('category', '')}\n\nLANGUE\n{meta.get('video_language', '')}\n\n"
        f"AUDIENCE\n{audience_label(bool(meta.get('made_for_kids')))}\n\nCOMMENTAIRE ÉPINGLÉ\n{meta.get('pinned_comment', '')}\n"
    )


def tiktok_text(meta: dict[str, Any]) -> str:
    return (
        f"LÉGENDE\n{meta.get('tiktok_caption', '')}\n\nTEXTE D’ACCROCHE (à afficher à l’écran)\n{meta.get('tiktok_hook', '')}\n\n"
        f"HASHTAGS\n{' '.join(meta.get('tiktok_hashtags', []))}\n\nCOMMENTAIRE SUGGÉRÉ\n{meta.get('tiktok_comment', '')}\n"
    )
