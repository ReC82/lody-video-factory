"""Sélection optionnelle des personnages et du lieu d'une production (epic #29, tickets #35/#36/#37).

Contrat : au lancement, les IDs sélectionnés sont résolus dans le **projet courant** puis copiés en un
instantané immuable (``snapshot.narrative_context``), aux côtés de l'instantané des paramètres du projet
(voir ``make_snapshot``). Modifier ou désactiver ensuite un personnage ou un lieu ne change donc jamais
une production déjà préparée ou lancée — exactement le principe déjà appliqué aux paramètres du projet.

Aucune colonne SQLite dédiée : le ``snapshot`` JSON existant (v3, ``lody/db.py``) suffit déjà à porter des
données arbitraires immuables par production, et il est déjà protégé par le garde-fou secrets (``GUARDED``
dans ``generation/store.py``). Ajouter une table ne ferait que dupliquer ce mécanisme.

``render_prompt_block`` (#36) lit ce ``narrative_context`` DÉJÀ RÉSOLU (jamais les tables éditables) et
produit le texte à ajouter au prompt d'écriture du SCRIPT uniquement — voir ``generation/service.py``
(``_run``) et ``generation/mpt_connector.py`` (``script_prompt``).

``enrich_visual_prompts`` (#37) fait de même pour les prompts d'IMAGE d'un storyboard déjà construit (voir
``generation/storyboard.py``). Aucune voix : c'est volontairement hors de portée (voir tickets ultérieurs).

``reference_images_status`` (#38) dit, pour un ``narrative_context`` déjà résolu, quels personnages/lieux ont
une image de référence et si elle est réellement TRANSMISE au fournisseur — jamais une transmission simulée
pour un fournisseur qui ne la supporte pas (voir ``generation.provider.VideoGenerationProvider.
supports_reference_images``) : le repli est toujours explicite (consigné dans ``params`` de la production,
voir ``generation/service.py``), jamais silencieux.

``script_injection_applied``/``visual_injection_applied`` (#69) sont des lectures PURES, pour le diagnostic
d'une production (``view_tracking.py``) : elles ne changent RIEN au texte envoyé, elles disent seulement si
le bloc déjà produit par ``render_prompt_block``/``enrich_visual_prompts`` apparaît RÉELLEMENT dans le texte
déjà enregistré dans ``production.trace`` (« ce qui est réellement envoyé », voir ``service._trace``) —
jamais déduit de la seule présence d'une sélection : un connecteur qui ignore le bloc reçu (ex. la
simulation, voir ``demo.py``) ne l'injecte jamais, même avec une sélection figée dans le snapshot.

``resolve_voice`` (#70, MVP mono-voix — prérequis technique de #39) choisit AU MAXIMUM un personnage de
référence parmi les personnages SÉLECTIONNÉS (jamais tous les personnages du projet), dans cet ordre :
personnage sélectionné marqué principal ; sinon, si un seul personnage est sélectionné, celui-ci ; sinon la
voix du projet est conservée. N'utilise QUE les champs voix déjà copiés dans le ``narrative_context`` figé
(jamais la fiche personnage actuelle) : une voix incomplète ou un fournisseur non reconnu déclenche un repli
sûr vers la voix du projet, toujours consigné (jamais silencieux, voir le dict d'origine renvoyé).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from lody import catalog
from lody.characters import Character, CharacterNotFound, CharacterRepository
from lody.generation.models import VoiceSpec
from lody.locations import Location, LocationNotFound, LocationRepository

if TYPE_CHECKING:  # pragma: no cover
    from lody.generation.storyboard import Scene

# Version du *format* du bloc narrative_context (indépendante de ``snapshot["version"]``, qui ne couvre que
# les paramètres du projet) : à incrémenter si sa forme change un jour. Passée à 2 par #37, qui ajoute
# "reference_prompt" (absent depuis #35 : "non utilisé tant qu'aucune injection n'existe" — c'est fait ici).
# Passée à 3 par #38, qui ajoute "reference_image" (référence de fichier déjà validée, jamais des octets :
# voir ``lody.reference_images``) — copiée telle quelle pour que la production garde la trace de la
# référence *alors valide*, même si le personnage/lieu est modifié ensuite (voir reference_images.py :
# un fichier remplacé/retiré n'est jamais supprimé, exactement pour que cette copie reste résolvable).
# Passée à 4 par #70, qui ajoute les champs voix (voice_provider/voice_name/external_voice_id) — copiés
# pour que ``resolve_voice`` (ci-dessous) n'ait jamais besoin de relire la fiche personnage actuelle, en
# particulier après le lancement. Toujours ABSENTS de _SCRIPT_CHARACTER_FIELDS/_VISUAL_CHARACTER_FIELDS
# (plus bas) : ces champs ne rejoignent donc jamais le texte d'un prompt.
NARRATIVE_CONTEXT_VERSION = 4

# Champs copiés dans le snapshot : identité + descriptions effectives + voix (#70, jamais utilisée pour le
# texte des prompts — voir _SCRIPT_CHARACTER_FIELDS/_VISUAL_CHARACTER_FIELDS plus bas, qui ne les reprennent
# jamais).
_CHARACTER_FIELDS = (
    "id", "name", "role", "personality", "visual_description", "reference_prompt", "speech_style",
    "permanent_elements", "continuity_notes", "reference_image",
    "voice_provider", "voice_name", "external_voice_id", "is_primary",
)
_LOCATION_FIELDS = (
    "id", "name", "location_type", "description", "reference_prompt", "continuity_notes", "reference_image",
    "is_primary",
)


class NarrativeContextError(Exception):
    """Sélection invalide : identifiant inconnu, d'un autre projet, ou élément désactivé.

    Message déjà lisible par l'utilisateur (traduit en ``LaunchError`` par le service appelant).
    """


def _character_snapshot(character: Character) -> dict[str, Any]:
    return {field: getattr(character, field) for field in _CHARACTER_FIELDS}


def _location_snapshot(location: Location) -> dict[str, Any]:
    return {field: getattr(location, field) for field in _LOCATION_FIELDS}


def resolve_narrative_context(
    project_id: str,
    character_ids: Sequence[str],
    location_id: str | None,
    character_repo: CharacterRepository,
    location_repo: LocationRepository,
) -> dict[str, Any]:
    """Résout la sélection facultative en un instantané immuable.

    Renvoie ``{}`` si rien n'est sélectionné (aucun personnage, aucun lieu) : c'est le cas de toutes les
    productions historiques et de toute nouvelle production sans sélection — comportement inchangé.

    Lève ``NarrativeContextError`` si un identifiant :
    - n'existe pas, ou appartient à un autre projet que ``project_id`` (même refus dans les deux cas,
      comme ``CharacterRepository.get``/``LocationRepository.get``, pour ne rien laisser deviner) ;
    - désigne un élément désactivé (seuls les éléments actifs sont sélectionnables).
    """
    characters: list[dict[str, Any]] = []
    seen: set[str] = set()
    for character_id in character_ids:
        if not character_id or character_id in seen:
            continue  # doublon silencieusement ignoré (même personnage coché deux fois)
        seen.add(character_id)
        try:
            character = character_repo.get(project_id, character_id)
        except CharacterNotFound:
            raise NarrativeContextError(f"Personnage introuvable dans ce projet : {character_id}") from None
        if not character.is_active:
            raise NarrativeContextError(f"Ce personnage est désactivé : {character.name}")
        characters.append(_character_snapshot(character))

    location: dict[str, Any] | None = None
    if location_id:
        try:
            found = location_repo.get(project_id, location_id)
        except LocationNotFound:
            raise NarrativeContextError(f"Lieu introuvable dans ce projet : {location_id}") from None
        if not found.is_active:
            raise NarrativeContextError(f"Ce lieu est désactivé : {found.name}")
        location = _location_snapshot(found)

    if not characters and location is None:
        return {}
    return {"version": NARRATIVE_CONTEXT_VERSION, "characters": characters, "location": location}


# -- rendu dans le prompt d'écriture du script (#36) ------------------------------------------------------------

# Sous-ensemble RESTREINT de _CHARACTER_FIELDS/_LOCATION_FIELDS : seulement ce qui est utile à un SCRIPT (jamais
# la description visuelle ni les éléments permanents, réservés à un futur ticket d'injection image ; jamais
# l'identifiant technique, ni "is_primary" qui n'a de sens que pour l'écran de sélection).
_SCRIPT_CHARACTER_FIELDS = ("role", "personality", "speech_style", "continuity_notes")
_SCRIPT_LOCATION_FIELDS = ("location_type", "description", "continuity_notes")
_FIELD_LABELS = {
    "role": "rôle", "personality": "personnalité", "speech_style": "style de parole", "continuity_notes": "continuité",
    "location_type": "type", "description": "description",
}

# Borne INDÉPENDANTE de la limite globale du prompt (``script_prompt``'s ``limit``) : garantit que ce bloc ne
# peut jamais, à lui seul, dominer le budget du prompt — quel que soit le nombre de personnages sélectionnés.
NARRATIVE_BLOCK_MAX = 900
_HEADER = (
    "### Contexte narratif de cette production (instantané figé de #34/#35 — à distinguer de la demande "
    "ci-dessous, qui reste seule à définir le sujet) ###\n"
    "Utilise ces éléments seulement s’ils servent naturellement le récit : ne force pas leur présence dans "
    "chaque phrase, et n’invente aucun personnage ou lieu qui ne soit pas listé ici."
)
_FOOTER = "### Fin du contexte narratif ###"
_TRUNCATION_NOTE = "(liste tronquée pour tenir dans la limite du prompt)"


def _bounded_block(header: str, lines: Sequence[str], footer: str, limit: int, truncation_note: str) -> str:
    """Assemble ``header``, autant de ``lines`` que le budget ``limit`` le permet, puis ``footer``.

    Coupure NETTE entre éléments (jamais au milieu d'une ligne) : dès qu'une ligne ne tient plus, elle est
    entièrement omise et remplacée par ``truncation_note``. Partagé par #36 (script) et #37 (images) pour un
    seul comportement de troncature, déterministe et testé une fois.
    """
    kept: list[str] = []
    used = len(header) + len(footer) + 2
    for line in lines:
        if used + len(line) + 1 > limit:
            kept.append(truncation_note)
            break
        kept.append(line)
        used += len(line) + 1
    return "\n".join([header, *kept, footer])


def _detail_line(item: dict[str, Any], fields: Sequence[str], labels: dict[str, str]) -> str:
    return "; ".join(f"{labels[field]} : {item[field]}" for field in fields if item.get(field))


def _character_line(character: dict[str, Any]) -> str:
    details = _detail_line(character, _SCRIPT_CHARACTER_FIELDS, _FIELD_LABELS)
    name = character.get("name", "")
    return f"- {name}" + (f" — {details}" if details else "")


def _location_line(location: dict[str, Any]) -> str:
    details = _detail_line(location, _SCRIPT_LOCATION_FIELDS, _FIELD_LABELS)
    name = location.get("name", "")
    return f"Lieu : {name}" + (f" — {details}" if details else "")


def render_prompt_block(narrative_context: dict[str, Any] | None) -> str:
    """Bloc de contexte narratif, clairement délimité, à ajouter au prompt d'écriture du script.

    ``narrative_context`` DOIT venir du ``snapshot`` déjà figé d'une production (jamais des tables
    ``characters``/``locations``, qui peuvent avoir changé depuis) : voir ``resolve_narrative_context``.

    Renvoie une chaîne VIDE — et donc un prompt strictement identique à avant #36 — si ``narrative_context``
    est ``None``, absent, ou ``{}`` : c'est le cas de toute production sans sélection (#34), et de toute
    production antérieure à #35 qui n'a jamais eu ce bloc dans son snapshot.

    Ordre déterministe (celui, déjà figé, des personnages dans le snapshot, puis le lieu en dernier) ; taille
    bornée par ``NARRATIVE_BLOCK_MAX`` (troncature nette entre éléments, jamais au milieu d'un champ).
    """
    if not narrative_context:
        return ""
    lines = [_character_line(character) for character in narrative_context.get("characters") or []]
    location = narrative_context.get("location")
    if location:
        lines.append(_location_line(location))
    if not lines:
        return ""
    return _bounded_block(_HEADER, lines, _FOOTER, NARRATIVE_BLOCK_MAX, _TRUNCATION_NOTE)


# -- enrichissement des prompts d'image d'un storyboard déjà construit (#37) --------------------------------------

# Sous-ensemble RESTREINT et DISJOINT de _SCRIPT_CHARACTER_FIELDS/_SCRIPT_LOCATION_FIELDS (#36) : seulement ce
# qui est utile à une IMAGE (jamais le rôle, la personnalité, ni le style de parole, qui n'ont pas de sens
# visuel et restent réservés au script).
_VISUAL_CHARACTER_FIELDS = ("visual_description", "reference_prompt", "permanent_elements", "continuity_notes")
_VISUAL_LOCATION_FIELDS = ("location_type", "description", "reference_prompt", "continuity_notes")
_VISUAL_FIELD_LABELS = {
    "visual_description": "apparence", "reference_prompt": "prompt de référence",
    "permanent_elements": "tenue, accessoires et éléments permanents", "continuity_notes": "continuité",
    "location_type": "type", "description": "apparence",
}

# Borne PAR SCÈNE (indépendante de ``storyboard.MAX_PROMPT``, qui tronque le prompt final en dernier recours) :
# garantit qu'ajouter la continuité visuelle ne peut jamais, à elle seule, dominer le budget d'une scène.
VISUAL_BLOCK_MAX = 500
_VISUAL_HEADER = "### Continuité visuelle (instantané de production, #34/#35/#37) ###"
_VISUAL_FOOTER = "### Fin de la continuité visuelle ###"
_CHARACTER_CONTINUITY_RULE = (
    "Conserve les mêmes traits physiques, vêtements, accessoires, proportions et palette de couleurs pour "
    "chaque personnage ci-dessus, sur tous les plans où il apparaît."
)
_LOCATION_CONTINUITY_RULE = "Conserve la disposition permanente de ce lieu (mêmes éléments, même agencement) sur tous les plans."
_VISUAL_TRUNCATION_NOTE = "(continuité tronquée pour tenir dans la limite du prompt)"


def _character_visual_full(character: dict[str, Any]) -> str:
    details = _detail_line(character, _VISUAL_CHARACTER_FIELDS, _VISUAL_FIELD_LABELS)
    name = character.get("name", "")
    return f"- {name}" + (f" — {details}" if details else "")


def _character_visual_repeat(character: dict[str, Any], first_index: int) -> str:
    name = character.get("name", "")
    return f"- {name} (déjà décrit·e à la scène {first_index} : même apparence, mêmes vêtements/accessoires)"


def _location_visual_full(location: dict[str, Any]) -> str:
    details = _detail_line(location, _VISUAL_LOCATION_FIELDS, _VISUAL_FIELD_LABELS)
    name = location.get("name", "")
    return f"Lieu : {name}" + (f" — {details}" if details else "")


def _location_visual_repeat(location: dict[str, Any], first_index: int) -> str:
    name = location.get("name", "")
    return f"Lieu : {name} (déjà décrit à la scène {first_index} : même disposition)"


def _mentions(narration: str, name: str) -> bool:
    """Correspondance simple, déterministe : le nom (non vide) apparaît dans le texte, sans tenir compte de la casse.

    Volontairement basique (pas d'analyse linguistique) : un nom très court ou très commun peut, en théorie,
    correspondre à l'intérieur d'un autre mot. C'est un compromis assumé pour rester déterministe et testable ;
    voir le ticket #37 (risque « incohérence d'apparence entre scènes »).
    """
    return bool(name) and name.strip().lower() in narration.lower()


def _scene_visual_lines(narration: str, characters: Sequence[dict[str, Any]], location: dict[str, Any] | None,
                        first_seen: dict[str, int], scene_index: int) -> list[str]:
    """Lignes de continuité pour UNE scène : personnages mentionnés dans sa narration, puis le lieu (s'il existe,
    dans TOUTES les scènes — il représente le cadre de toute la vidéo, comme le style visuel du projet).

    Description complète à la première apparition d'un élément (``scene_index`` le plus bas où il est utilisé),
    rappel bref ensuite — pour ne jamais répéter inutilement un long bloc (voir le ticket #37).
    """
    lines: list[str] = []
    present = [character for character in characters if _mentions(narration, character.get("name", ""))]
    for character in present:
        key = character.get("id") or character.get("name", "")
        if key in first_seen:
            lines.append(_character_visual_repeat(character, first_seen[key]))
        else:
            lines.append(_character_visual_full(character))
            first_seen[key] = scene_index
    if present:
        lines.append(_CHARACTER_CONTINUITY_RULE)
    if location:
        loc_key = "\0location"  # jamais un id/nom réel : ne peut pas entrer en collision avec un personnage
        if loc_key in first_seen:
            lines.append(_location_visual_repeat(location, first_seen[loc_key]))
        else:
            lines.append(_location_visual_full(location))
            first_seen[loc_key] = scene_index
        lines.append(_LOCATION_CONTINUITY_RULE)
    return lines


def enrich_visual_prompts(scenes: Sequence["Scene"], narrative_context: dict[str, Any] | None) -> list["Scene"]:
    """Ajoute un bloc « Continuité visuelle », clairement délimité, aux prompts d'un storyboard DÉJÀ CONSTRUIT
    (voir ``storyboard.build_storyboard``) — ne change JAMAIS leur nombre, leur ordre, ni leur narration
    (seul ``Scene.prompt`` est modifié) : le nombre de scènes et l'estimation de coût restent donc inchangés.

    ``narrative_context`` DOIT venir du ``snapshot`` déjà figé d'une production (jamais des tables
    ``characters``/``locations``, qui peuvent avoir changé depuis) : voir ``resolve_narrative_context``.

    Renvoie ``scenes`` INCHANGÉES — et donc des prompts strictement identiques à avant #37 — si
    ``narrative_context`` est ``None``, absent, ou ``{}`` : c'est le cas de toute production sans sélection
    (#34), et de toute production antérieure à #35 qui n'a jamais eu ce bloc dans son snapshot.

    Un personnage n'est ajouté qu'aux scènes dont la narration mentionne son nom — jamais injecté aveuglément
    dans toute la distribution. Le lieu s'applique à toutes les scènes. Taille bornée par ``VISUAL_BLOCK_MAX``
    (troncature nette entre éléments), ordre stable (celui déjà figé dans le snapshot).
    """
    if not narrative_context:
        return list(scenes)
    characters = narrative_context.get("characters") or []
    location = narrative_context.get("location")
    if not characters and not location:
        return list(scenes)

    first_seen: dict[str, int] = {}
    enriched: list[Scene] = []
    for scene in scenes:
        lines = _scene_visual_lines(scene.narration, characters, location, first_seen, scene.index)
        if not lines:
            enriched.append(scene)
            continue
        block = _bounded_block(_VISUAL_HEADER, lines, _VISUAL_FOOTER, VISUAL_BLOCK_MAX, _VISUAL_TRUNCATION_NOTE)
        enriched.append(replace(scene, prompt=(scene.prompt + "\n\n" + block)))
    return enriched


# -- statut des images de référence pour le fournisseur choisi (#38) ---------------------------------------------

def reference_images_status(narrative_context: dict[str, Any] | None, provider_supports_reference_images: bool) -> dict[str, Any]:
    """Quels personnages/lieux du ``narrative_context`` ont une image de référence, et si elle est réellement
    transmise au fournisseur — à consigner dans les paramètres de la production (jamais utilisée pour changer
    le texte du script ou des prompts, voir ``render_prompt_block``/``enrich_visual_prompts`` ci-dessus).

    Aujourd'hui, ``provider_supports_reference_images`` vaut toujours ``False`` (aucun connecteur ne sait
    encore transmettre une image de référence, voir ``generation.provider``) : toute image présente est donc
    toujours en repli — JAMAIS silencieusement, cette fonction le rend explicite et vérifiable. Le jour où un
    fournisseur déclare le support, ``used``/``fallback`` en tiennent compte automatiquement.

    Renvoie ``{"present": [], "used": [], "fallback": [], "provider_supports": ...}`` si rien n'a d'image
    (dont le cas ``narrative_context`` vide/``None`` : aucun changement de comportement).
    """
    characters = (narrative_context or {}).get("characters") or []
    location = (narrative_context or {}).get("location")
    present = [character["name"] for character in characters if character.get("reference_image")]
    if location and location.get("reference_image"):
        present.append(location["name"])
    if not present:
        return {"present": [], "used": [], "fallback": [], "provider_supports": provider_supports_reference_images}
    if provider_supports_reference_images:
        return {"present": present, "used": list(present), "fallback": [], "provider_supports": True}
    return {"present": present, "used": [], "fallback": list(present), "provider_supports": False}


# -- diagnostic : injection réellement appliquée ou non (#69) ------------------------------------------------------

def script_injection_applied(narrative_context: dict[str, Any] | None, sent_prompt_text: str | None) -> bool:
    """Vrai seulement si le bloc de contexte narratif (voir ``_HEADER``/``render_prompt_block``) apparaît
    RÉELLEMENT dans ``sent_prompt_text`` — le texte du prompt de script déjà enregistré dans
    ``production.trace["script_request"]["video_script_prompt"]``. Jamais déduit de la seule présence d'une
    sélection dans le snapshot : un script fourni par l'utilisateur (aucun appel texte, donc aucun
    ``script_request``) ou un connecteur qui ignore le bloc reçu (la simulation, par exemple) donnent
    toujours ``False`` ici, même avec une sélection figée."""
    return bool(narrative_context) and _HEADER in (sent_prompt_text or "")


def visual_injection_applied(narrative_context: dict[str, Any] | None, sent_prompt_text: str | None) -> bool:
    """Même principe que ``script_injection_applied``, pour UNE scène : vrai seulement si le bloc de
    continuité visuelle (voir ``_VISUAL_HEADER``/``enrich_visual_prompts``) apparaît dans le prompt de cette
    scène déjà enregistré dans ``production.trace["scenes"][i]["prompt_sent"]``. Une scène dont la narration
    ne mentionne aucun personnage sélectionné, et sans lieu sélectionné, n'a jamais ce bloc — c'est le
    comportement normal de #37, pas une anomalie."""
    return bool(narrative_context) and _VISUAL_HEADER in (sent_prompt_text or "")


# -- voix MVP mono-voix (#70, prérequis technique de #39) -----------------------------------------------------------

def resolve_voice(project_voice: VoiceSpec, narrative_context: dict[str, Any] | None) -> tuple[VoiceSpec, dict[str, Any]]:
    """(voix effective, informations d'origine — jamais un secret, pour l'aperçu et le diagnostic).

    Choisit AU MAXIMUM un personnage de référence parmi les personnages SÉLECTIONNÉS (jamais tous les
    personnages du projet, jamais un lieu) :

    1. le personnage sélectionné marqué ``is_primary`` — seulement s'il y en a EXACTEMENT un (plusieurs
       personnages principaux sélectionnés à la fois : aucune ambiguïté tolérée, voix du projet conservée) ;
    2. sinon, si un seul personnage est sélectionné, celui-ci ;
    3. sinon (aucun personnage sélectionné, ou plusieurs sans principal unique), la voix du projet est
       conservée telle quelle.

    Si le personnage de référence n'a pas ``voice_provider`` ET ``external_voice_id`` renseignés, ou que
    ``voice_provider`` n'est pas une valeur reconnue de ``catalog.VOICE_PROVIDERS``, repli sûr et TOUJOURS
    consigné (``fallback: True``) vers la voix du projet — jamais un échec, jamais silencieux.

    N'utilise QUE les champs déjà copiés dans ``narrative_context`` (voir ``_CHARACTER_FIELDS`` ci-dessus) :
    jamais un appel à ``CharacterRepository``, donc jamais un risque de relire la fiche personnage actuelle
    après le lancement — le contrat d'immuabilité du snapshot s'applique ici exactement comme ailleurs.
    """
    characters = (narrative_context or {}).get("characters") or []
    if not characters:
        return project_voice, {"source": "project", "fallback": False, "character_name": "",
                               "reason": "aucun personnage sélectionné"}

    primaries = [character for character in characters if character.get("is_primary")]
    if len(primaries) == 1:
        reference, why = primaries[0], "personnage principal sélectionné"
    elif len(characters) == 1:
        reference, why = characters[0], "seul personnage sélectionné"
    else:
        return project_voice, {"source": "project", "fallback": False, "character_name": "",
                               "reason": "plusieurs personnages sélectionnés, aucun principal unique"}

    name = str(reference.get("name") or "")
    provider_raw = str(reference.get("voice_provider") or "")
    voice_id = str(reference.get("external_voice_id") or "")
    if not provider_raw or not voice_id:
        return project_voice, {"source": "project", "fallback": True, "character_name": name,
                               "reason": f"{name} ({why}) : voix incomplète (fournisseur ou identifiant "
                                         "manquant) — repli sur la voix du projet"}
    provider = catalog.normalize(catalog.VOICE_PROVIDERS, provider_raw)
    if provider is None:
        return project_voice, {"source": "project", "fallback": True, "character_name": name,
                               "reason": f"{name} ({why}) : fournisseur de voix non reconnu — repli sur la "
                                         "voix du projet"}

    character_voice = VoiceSpec(provider=provider, voice_id=voice_id,
                                name=str(reference.get("voice_name") or ""), model=project_voice.model)
    return character_voice, {"source": "character", "fallback": False, "character_name": name,
                             "reason": f"voix de {name} ({why})"}
