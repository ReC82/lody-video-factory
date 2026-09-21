"""Kit de publication d'une production terminée : métadonnées, miniature, sous-titres, contrôles, archive ZIP.

Tout est local et gratuit SAUF la (re)génération d'un fond de miniature dédié, qui n'a lieu qu'après estimation puis
confirmation explicite (jamais automatiquement). Aucune publication, aucun compte externe.
"""

from __future__ import annotations

import copy
import io
import json
import re
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image

from lody import brief as brief_lib
from lody.generation import kit_files, media, publication, subtitles, thumbnail
from lody.generation.costing import PriceBook, estimate_cost, load_price_book
from lody.generation.kit_store import Kit, KitRepository
from lody.generation.models import ExternalTask, ProductionStatus, ProviderError, SceneUnits
from lody.generation.safety import sanitize
from lody.generation.service import LaunchError, ProductionService, request_of
from lody.generation.store import Production
from lody.projects import ProjectRepository
from lody.secrets_guard import _SECRET_VALUE_PATTERNS
from lody.theme import PALETTES

_INTERNAL = re.compile(r"/Money" r"PrinterTurbo|/srv/|/data/|storage/tasks|tasks/[0-9a-f-]{8,}|\bprd_[0-9a-f]{12}\b|\bprj_[0-9a-f]{12}\b|\bkit_[0-9a-f]{12}\b|"
                       r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|config\.toml|\.sqlite", re.IGNORECASE)
HISTORY_VALUE_MAX = 1500


@dataclass(frozen=True)
class Check:
    """Un point du contrôle avant publication : ``ok`` vrai/faux, ou ``None`` pour un simple avertissement."""

    key: str
    title: str
    ok: bool | None
    detail: str = ""


@dataclass(frozen=True)
class SubtitleBundle:
    cues: list[subtitles.Cue]
    srt: str
    vtt: str
    check: subtitles.SubtitleCheck | None
    available: bool


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def contains_secret(text: str) -> bool:
    """Motifs de clés connus (sk-…, ghp_…, Bearer, clé privée…). Volontairement SANS la règle « jeton opaque » : dans un texte de
    publication, un slug de nom de fichier (``lodycrypto-v1-…``) ressemblerait à tort à un jeton."""
    return any(pattern.search(text) for pattern in _SECRET_VALUE_PATTERNS[:-1])


def assert_public(text: str) -> str:
    """Refuse (lève ``ValueError``) tout texte destiné à sortir de Lody qui contiendrait un secret ou une donnée interne."""
    if contains_secret(text) or _INTERNAL.search(text):
        raise ValueError("donnée interne ou secret détecté dans un contenu à exporter")
    return text


class KitService:
    def __init__(self, kits: KitRepository, productions: ProductionService, projects: ProjectRepository, executor: Any,
                 price_book: Callable[[], PriceBook] = load_price_book, clock: Callable[[], str] = _now):
        self.kits = kits
        self.productions = productions
        self.projects = projects
        self._executor = executor
        self._price_book = price_book
        self._clock = clock

    # -- accès et création --------------------------------------------------------------------------------------------
    def _production(self, production_id: str) -> Production:
        production = self.productions.repo.get(production_id)
        if production.status is not ProductionStatus.TERMINEE:
            raise LaunchError("Le kit de publication n’existe que pour une production terminée.")
        return production

    def production_of(self, kit: Kit) -> Production:
        production = self._production(kit.production_id)
        if production.project_id != kit.project_id:
            raise LaunchError("Ce kit n’appartient pas au projet de sa production.")
        return production

    def _project_profile(self, production: Production):
        project = self.projects.get(production.project_id)   # explicitement le projet de LA production, jamais un autre
        return project, brief_lib.brief_settings(project.settings)["publication"]

    def _accent(self, project) -> tuple[int, int, int]:
        index = project.settings.get("accent")
        colour = PALETTES[index][0] if isinstance(index, int) and 0 <= index < len(PALETTES) else thumbnail.ACCENT
        if isinstance(colour, str):
            colour = tuple(int(colour.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
        return colour  # type: ignore[return-value]

    def _task(self, production: Production) -> ExternalTask | None:
        return ExternalTask(production.provider, production.external_task_id) if production.external_task_id else None

    def ensure_kit(self, production_id: str) -> Kit:
        """Retourne le kit de cette production, en le créant (gratuitement, en local) s'il n'existe pas encore."""
        production = self._production(production_id)
        existing = self.kits.find_by_production(production.id)
        if existing:
            return existing
        project, profile = self._project_profile(production)
        request = request_of(production)
        duration = production.video_duration
        metadata = publication.generate_metadata(publication.PublicationInput(
            project_name=project.name, subject=production.subject, script=production.script, language=request.language,
            content_type=project.content_type, description=project.description, version_label=production.label,
            duration_seconds=duration, profile=profile))
        backgrounds = self.background_options(production)
        thumb = {"text": publication.thumbnail_text_from(metadata, production.subject, production.script), "variant": "centre",
                 "background": ({"kind": backgrounds[0]["kind"], "ref": backgrounds[0]["ref"]} if backgrounds else None),
                 "accent": list(self._accent(project)), "generated_refs": [b["ref"] for b in backgrounds if b["kind"] == "generated"]}
        return self.kits.create(production_id=production.id, project_id=production.project_id, generated_with="local",
                                metadata=metadata, initial_metadata=copy.deepcopy(metadata), thumbnail=thumb,
                                history=[{"at": self._clock(), "action": "création (propositions locales, gratuites)", "fields": []}])

    # -- métadonnées ---------------------------------------------------------------------------------------------------------
    def _history(self, kit: Kit, action: str, fields: list[str], before: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        entry: dict[str, Any] = {"at": self._clock(), "action": action, "fields": fields}
        if before:
            entry["before"] = {k: (v[:HISTORY_VALUE_MAX] if isinstance(v, str) else v) for k, v in before.items()}
        return [*kit.history, entry]

    def save_metadata(self, kit_id: str, updates: dict[str, Any]) -> Kit:
        """Enregistre une modification manuelle (gratuit). Refuse les valeurs invalides ; l'historique garde l'ancienne valeur."""
        kit = self.kits.get(kit_id)
        clean, errors = publication.validate_metadata(updates, kit.metadata.get("video_language", "fr"))
        if errors:
            raise LaunchError("Modification refusée : " + " ".join(errors.values()))
        changed = {k: v for k, v in clean.items() if kit.metadata.get(k) != v}
        if not changed:
            return kit
        return self.kits.update(kit.id, metadata={**kit.metadata, **changed},
                                history=self._history(kit, "modification manuelle", sorted(changed), {k: kit.metadata.get(k) for k in changed}))

    def restore_initial(self, kit_id: str) -> Kit:
        kit = self.kits.get(kit_id)
        if kit.metadata == kit.initial_metadata:
            return kit
        return self.kits.update(kit.id, metadata=copy.deepcopy(kit.initial_metadata),
                                history=self._history(kit, "restauration des propositions initiales", ["*"], kit.metadata))

    def regenerate_metadata(self, kit_id: str) -> Kit:
        """Nouvelles propositions locales depuis le script et le profil ACTUEL du projet : gratuit, sans toucher à la vidéo."""
        kit = self.kits.get(kit_id)
        production = self.production_of(kit)
        project, profile = self._project_profile(production)
        request = request_of(production)
        fresh = publication.generate_metadata(publication.PublicationInput(
            project_name=project.name, subject=production.subject, script=production.script, language=request.language,
            content_type=project.content_type, description=project.description, version_label=production.label,
            duration_seconds=production.video_duration, profile=profile))
        return self.kits.update(kit.id, metadata=fresh, initial_metadata=copy.deepcopy(fresh),
                                history=self._history(kit, "nouvelles propositions locales (gratuit)", ["*"], kit.metadata))

    # -- miniature -------------------------------------------------------------------------------------------------------------------
    def background_options(self, production: Production) -> list[dict[str, str]]:
        """Fonds disponibles : fond dédié généré (le cas échéant) puis images de scène déjà produites (gratuites)."""
        options: list[dict[str, str]] = []
        kit = self.kits.find_by_production(production.id)
        refs = [a["ref"] for a in production.assets if a.get("kind") == "thumbnail_background"]
        refs += [r for r in (kit.thumbnail.get("generated_refs", []) if kit else []) if r not in refs]
        for number, ref in enumerate(refs, 1):
            try:
                kit_files.resolve_background(ref, production.id)
            except ValueError:
                continue
            options.append({"kind": "generated", "ref": ref, "label": f"Fond généré {number}"})
        task = self._task(production)
        if task:
            provider = self.productions.provider(production.provider)
            for number, image in enumerate(provider.list_scene_images(task), 1):
                options.append({"kind": "scene", "ref": image["ref"], "label": f"Image de scène {number}"})
        return options

    def background_warnings(self, production: Production) -> list[str]:
        """Pourquoi une image de scène peut ne PAS convenir comme fond (gabarit global du moteur, visages)."""
        notes = []
        template = (production.trace or {}).get("image_template")
        if not production.trace:
            notes.append("Production antérieure à la traçabilité : ses images ont pu recevoir le gabarit d’images global du moteur "
                         "(univers d’un autre projet). Vérifie qu’aucun élément étranger au sujet n’apparaît avant de choisir un fond.")
        elif template and template.get("applied"):
            notes.append("Le moteur a ajouté son gabarit d’images global à ces images : elles peuvent montrer l’univers d’un autre projet.")
        notes.append("Vérifie que le fond choisi ne montre ni visage identifiable, ni logo, ni texte.")
        return notes

    def _load_background(self, kit: Kit, production: Production) -> Image.Image:
        background = kit.thumbnail.get("background")
        if not background:
            raise LaunchError("Aucun fond de miniature n’est disponible pour cette production.")
        if background["kind"] == "generated":
            path = kit_files.resolve_background(background["ref"], production.id)
        else:
            task = self._task(production)
            if task is None:
                raise LaunchError("Fond de miniature introuvable.")
            path = self.productions.provider(production.provider).resolve_asset(task, background["ref"], suffixes=(".png",))
        with Image.open(path) as image:
            return image.convert("RGB")

    def background_file(self, production: Production, option: dict[str, str]) -> Path:
        """Fichier local (déjà validé, dans le dossier autorisé) d'un fond proposé : pour l'aperçu dans l'interface."""
        if option["kind"] == "generated":
            return kit_files.resolve_background(option["ref"], production.id)
        task = self._task(production)
        if task is None:
            raise ValueError("fond introuvable")
        return self.productions.provider(production.provider).resolve_asset(task, option["ref"], suffixes=(".png",))

    def set_thumbnail(self, kit_id: str, *, text: str | None = None, variant: str | None = None,
                      background_ref: str | None = None) -> Kit:
        """Texte, variante et fond de la miniature (gratuit : la composition est locale)."""
        kit = self.kits.get(kit_id)
        production = self.production_of(kit)
        thumb = copy.deepcopy(kit.thumbnail)
        changed = []
        if text is not None:
            cleaned = thumbnail.clean_text(text)
            if not cleaned:
                raise LaunchError("Le texte de la miniature ne peut pas être vide.")
            if contains_secret(cleaned):
                raise LaunchError("Ce texte ressemble à une clé secrète : il ne peut pas figurer sur une miniature.")
            if cleaned != thumb.get("text"):
                thumb["text"] = cleaned
                changed.append("texte")
        if variant is not None:
            if variant not in thumbnail.VARIANTS:
                raise LaunchError("Variante de miniature inconnue.")
            if variant != thumb.get("variant"):
                thumb["variant"] = variant
                changed.append("variante")
        if background_ref is not None:
            option = next((o for o in self.background_options(production) if o["ref"] == background_ref), None)
            if option is None:
                raise LaunchError("Ce fond n’appartient pas à cette production.")
            if (thumb.get("background") or {}).get("ref") != background_ref:
                thumb["background"] = {"kind": option["kind"], "ref": option["ref"]}
                changed.append("fond")
        if not changed:
            return kit
        return self.kits.update(kit.id, thumbnail=thumb, history=self._history(kit, "miniature", changed, {"thumbnail": kit.thumbnail.get("text", "")}))

    def size_of(self, production: Production) -> tuple[int, int]:
        return thumbnail.SIZES.get(request_of(production).aspect, thumbnail.SIZES["9:16"])

    def render_all(self, kit: Kit, production: Production) -> dict[str, tuple[Image.Image, thumbnail.Layout]]:
        """Les trois compositions depuis le fond choisi : LOCAL et GRATUIT, aucun fournisseur."""
        accent = tuple(kit.thumbnail.get("accent") or thumbnail.ACCENT)
        return thumbnail.compose_all(self._load_background(kit, production), kit.thumbnail.get("text", ""), self.size_of(production), accent)  # type: ignore[arg-type]

    def render_selected(self, kit: Kit, production: Production) -> tuple[Image.Image, thumbnail.Layout]:
        accent = tuple(kit.thumbnail.get("accent") or thumbnail.ACCENT)
        return thumbnail.compose(self._load_background(kit, production), kit.thumbnail.get("text", ""),
                                 kit.thumbnail.get("variant", "centre"), self.size_of(production), accent)  # type: ignore[arg-type]

    def thumbnail_bytes(self, kit: Kit, production: Production, fmt: str = "png") -> bytes:
        image, _ = self.render_selected(kit, production)
        return thumbnail.to_jpeg(image) if fmt == "jpeg" else thumbnail.to_png(image)

    # -- sous-titres --------------------------------------------------------------------------------------------------------------------
    def video_path(self, production: Production) -> tuple[Path, bool]:
        """(fichier vidéo à publier, rendu corrigé ?) : le rendu corrigé (réparation technique) est préféré s'il existe."""
        repaired = next((a for a in production.assets if a.get("kind") == "repaired_video"), None)
        if repaired:
            try:
                return self.productions.resolve_asset(production, repaired["ref"]), True
            except ValueError:
                pass
        return self.productions.resolve_video(production), False

    def duration_ms(self, production: Production) -> int | None:
        try:
            path, _ = self.video_path(production)
        except (ValueError, LaunchError):
            path = None
        measured = media.probe_duration_ms(path) if path else None
        if measured:
            return measured
        return round(production.video_duration * 1000) if production.video_duration else None

    def subtitles(self, production: Production) -> SubtitleBundle:
        """SRT/VTT depuis les sous-titres DÉJÀ produits (aucun appel) : français corrigé, minutages vérifiés."""
        task = self._task(production)
        raw = self.productions.provider(production.provider).read_subtitles(task) if task else None
        if not raw:
            return SubtitleBundle([], "", "", None, False)
        cues, check = subtitles.build(raw, request_of(production).language, self.duration_ms(production))
        return SubtitleBundle(cues, subtitles.to_srt(cues), subtitles.to_vtt(cues), check, bool(cues))

    # -- contrôle avant publication --------------------------------------------------------------------------------------------------------
    def checks(self, kit: Kit, production: Production) -> list[Check]:
        request = request_of(production)
        _, profile = self._project_profile(production)
        bundle = self.subtitles(production)
        items: list[Check] = []
        seconds = (self.duration_ms(production) or 0) / 1000
        items.append(Check("duration", "Durée", seconds > 0, f"{seconds:.0f} secondes" if seconds else "durée inconnue"))
        items.append(Check("format", "Format vertical", request.aspect == "9:16",
                           "9:16 : adapté à Shorts, TikTok et Reels" if request.aspect == "9:16" else f"format {request.aspect} : non vertical"))
        thumb_ok, thumb_detail = False, "aucun fond disponible"
        try:
            _, layout = self.render_selected(kit, production)
            report = thumbnail.readability(layout)
            thumb_ok, thumb_detail = report.ok, ("miniature lisible sur téléphone" if report.ok else " ; ".join(report.messages))
        except (LaunchError, ValueError, OSError):
            pass
        items.append(Check("thumbnail", "Miniature choisie", thumb_ok, thumb_detail))
        if bundle.available and bundle.check:
            items.append(Check("subtitles", "Sous-titres présents et synchronisés", bundle.check.ok,
                               f"{len(bundle.cues)} sous-titres, fin à {bundle.check.last_end_ms / 1000:.1f} s" if bundle.check.ok
                               else " ; ".join(bundle.check.issues)))
        else:
            items.append(Check("subtitles", "Sous-titres présents et synchronisés", False, "sous-titres du moteur introuvables : la vidéo garde ses sous-titres incrustés"))
        texts = publication.all_text_fields(kit.metadata) + [kit.thumbnail.get("text", "")]
        claims = publication.financial_claims("\n".join(texts))
        strict = bool(profile.get("no_financial_claims"))
        items.append(Check("financial", "Absence de conseil financier", not claims,
                           "aucune formulation à risque" if not claims else "à vérifier : " + " ; ".join(claims[:3])) if strict or claims
                     else Check("financial", "Absence de conseil financier", True, "aucune formulation à risque"))
        internal = _INTERNAL.search("\n".join(texts)) or contains_secret("\n".join(texts))
        items.append(Check("internal", "Absence de secret ou de donnée interne", not internal,
                           "rien de tel dans les textes" if not internal else "un texte contient un secret ou une donnée interne"))
        warnings = [w for w in [kit.metadata.get("warning", ""), *production.warnings] if w]
        items.append(Check("warnings", "Avertissements", None if warnings else True, " | ".join(warnings) if warnings else "aucun"))
        return items

    # -- archive ------------------------------------------------------------------------------------------------------------------------------
    def public_metadata(self, kit: Kit, production: Production, bundle: SubtitleBundle, duration_ms: int | None) -> dict[str, Any]:
        project, _ = self._project_profile(production)
        request = request_of(production)
        return {
            "kit_format": 1, "project": project.name, "version": production.label, "updated_at": kit.updated_at,
            "video": {"file": "video.mp4", "duration_seconds": round(duration_ms / 1000, 1) if duration_ms else None,
                      "aspect": request.aspect, "language": publication.primary_language(request.language)},
            "publication": kit.metadata,
            "thumbnail": {"file": "thumbnail.png", "text": kit.thumbnail.get("text", ""), "variant": kit.thumbnail.get("variant", "centre")},
            "subtitles": {"language": "fr" if request.language.startswith("fr") else publication.primary_language(request.language),
                          "files": (["subtitles-fr.srt", "subtitles-fr.vtt"] if bundle.available else []), "cues": len(bundle.cues),
                          "burned_in_video": True},
        }

    def build_zip(self, kit: Kit, production: Production) -> bytes:
        """Archive du kit : vidéo, miniature, SRT, VTT, textes YouTube et TikTok, metadata.json. Rien d'interne n'y entre."""
        video, _ = self.video_path(production)
        bundle = self.subtitles(production)
        duration = self.duration_ms(production)
        texts = {
            "publication-youtube.txt": publication.youtube_text(kit.metadata),
            "publication-tiktok.txt": publication.tiktok_text(kit.metadata),
            "metadata.json": json.dumps(self.public_metadata(kit, production, bundle, duration), ensure_ascii=False, indent=2) + "\n",
        }
        if bundle.available:
            texts["subtitles-fr.srt"], texts["subtitles-fr.vtt"] = bundle.srt, bundle.vtt
        for name, content in texts.items():
            assert_public(content)                                   # défense : aucune donnée interne ne sort de Lody
        stamp = datetime.fromisoformat(kit.updated_at).timetuple()[:6]
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            def add(name: str, data: bytes | Path, method: int) -> None:
                info = zipfile.ZipInfo(name, date_time=stamp)
                info.compress_type = method
                info.external_attr = 0o644 << 16
                if isinstance(data, Path):
                    with data.open("rb") as source, archive.open(info, "w") as target:
                        while chunk := source.read(1 << 20):
                            target.write(chunk)
                else:
                    archive.writestr(info, data)
            add("video.mp4", video, zipfile.ZIP_STORED)
            add("thumbnail.png", self.thumbnail_bytes(kit, production, "png"), zipfile.ZIP_STORED)
            for name, content in texts.items():
                add(name, content.encode("utf-8"), zipfile.ZIP_DEFLATED)
        return buffer.getvalue()

    # -- fond de miniature dédié : PAYANT, jamais sans estimation puis confirmation -----------------------------------------------------------
    def _cost_of_one_background(self, production: Production) -> dict[str, Any]:
        request = request_of(production)
        units = SceneUnits(text_calls=0, scenes=(0, 0), images=(0, 0), characters=(0, 0), music_tracks=0, audio_seconds=(0, 0), thumbnails=1)
        estimate = estimate_cost(request, units, self._price_book())
        line = next(item for item in estimate.lines if item.component == "thumbnail")
        return {"currency": estimate.currency, "low": None if line.low is None else str(line.low), "high": None if line.high is None else str(line.high),
                "partial": line.status == "unpriced", "line": line.to_dict()}

    def prepare_background(self, kit_id: str) -> Kit:
        """Estime le coût d'un nouveau fond et attend la confirmation. N'appelle AUCUN fournisseur."""
        kit = self.kits.get(kit_id)
        production = self.production_of(kit)
        provider = self.productions.provider(production.provider)
        if not provider.supports_thumbnail_background:
            raise LaunchError("Le moteur actuel ne sait pas générer une image de fond seule : choisis une image de scène (gratuit).")
        request = request_of(production)
        topic = kit.metadata.get("title", "") or production.subject
        prompt = thumbnail.background_prompt(topic=topic, visual_style=request.visual_style, visual_rules=request.visual_rules,
                                             visual_avoid=request.visual_avoid, aspect=request.aspect)
        job = {"state": "awaiting_confirmation", "estimate": self._cost_of_one_background(production), "prompt": prompt,
               "prepared_at": self._clock(), "demo": provider.is_demo}
        if not self.kits.transition_job(kit.id, ("idle", "awaiting_confirmation", "failed"), job):
            raise LaunchError("Une génération de fond est déjà en cours pour ce kit.")
        return self.kits.get(kit.id)

    def cancel_background(self, kit_id: str) -> Kit:
        self.kits.transition_job(kit_id, ("awaiting_confirmation", "failed"), {"state": "idle"})
        return self.kits.get(kit_id)

    def confirm_background(self, kit_id: str, *, accept_partial: bool = False) -> Kit:
        """Confirmation explicite : lance UNE génération payante. Idempotent (double clic = un seul appel)."""
        kit = self.kits.get(kit_id)
        production = self.production_of(kit)
        job = kit.background_job
        if job.get("state") != "awaiting_confirmation":
            return kit
        provider = self.productions.provider(production.provider)
        if job.get("estimate", {}).get("partial") and not accept_partial and not provider.is_demo:
            raise LaunchError("Le tarif n’est pas configuré : confirme en acceptant explicitement cet avertissement.")
        blocking = provider.preflight(request_of(production)).blocking
        if blocking:
            raise LaunchError("La génération ne peut pas partir : " + " ".join(item.message for item in blocking[:2]))
        started = {**job, "state": "generating", "confirmed_at": self._clock()}
        if not self.kits.transition_job(kit.id, ("awaiting_confirmation",), started):
            return self.kits.get(kit.id)                                   # confirmé dans l'intervalle par une autre session
        self.kits.update(kit.id, history=self._history(kit, "fond régénéré : confirmation explicite du coût (payant)", ["fond"]))
        self._executor.submit(self._run_background, kit.id)
        return self.kits.get(kit.id)

    def _run_background(self, kit_id: str) -> None:
        kit = self.kits.get(kit_id)
        production = self.production_of(kit)
        provider = self.productions.provider(production.provider)
        job = kit.background_job
        try:
            png = provider.generate_thumbnail_background(request_of(production), job["prompt"])
            ref = kit_files.save_background(production.id, png)
        except (ProviderError, ValueError, OSError) as error:
            message = error.message if isinstance(error, ProviderError) else "image de fond invalide"
            self.kits.update(kit.id, background_job={"state": "failed", "message": sanitize(message, 200), "failed_at": self._clock()})
            return
        thumb = copy.deepcopy(kit.thumbnail)
        thumb["background"] = {"kind": "generated", "ref": ref}
        thumb["generated_refs"] = [*thumb.get("generated_refs", []), ref]
        self.kits.update(kit.id, thumbnail=thumb, background_job={"state": "idle", "last": "done", "at": self._clock()},
                         history=self._history(self.kits.get(kit.id), "nouveau fond généré", ["fond"]))
