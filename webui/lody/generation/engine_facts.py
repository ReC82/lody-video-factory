"""Faits sur la configuration du moteur, assainis par construction : des booléens et des noms de modèles.

``EngineFacts`` ne peut contenir aucune valeur de clé : les seuls champs sont des booléens (« la clé est-elle
renseignée ? »), des identifiants de fournisseur, des noms de modèles et des préférences d'affichage.

Deux sources, dans cet ordre :

1. ``config.toml`` lu directement, si le conteneur peut le lire ;
2. sinon le **rapport de capacités** produit par le propriétaire du fichier (aucun secret n'y figure) ;
   il n'est cru que si ``config.toml`` n'a pas changé depuis (taille + date, obtenues par ``stat`` — ce
   qui ne demande pas le droit de lecture). Un rapport périmé est refusé, jamais deviné.

Génération du rapport (sur l'hôte, en tant que propriétaire de config.toml) :

    PYTHONPATH=webui python3 -m lody.generation.engine_facts --config config.toml --out engine-report/engine-capabilities.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import tomllib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lody import settings

REPORT_VERSION = 2  # v2 : ajoute le gabarit d'images global du moteur (image_template)
# Fournisseurs de texte du moteur qui n'exigent aucune clé (leurs champs *_api_key peuvent être vides).
KEYLESS_LLM = frozenset({"ollama", "claude_code", "litellm", "pollinations"})

PROBLEM_LABELS = {
    "config_missing": "config.toml introuvable à l'emplacement attendu",
    "config_unreadable": "config.toml présent mais illisible pour ce conteneur (droits)",
    "config_invalid": "config.toml illisible (TOML invalide)",
    "report_missing": "aucun rapport de capacités disponible",
    "report_invalid": "rapport de capacités invalide",
    "report_stale": "rapport de capacités périmé (config.toml a changé depuis sa génération)",
}
_UI_KEYS = ("font_name", "font_size", "stroke_color", "stroke_width", "text_fore_color", "subtitle_position",
            "subtitle_animation", "rounded_subtitle_background")


@dataclass(frozen=True)
class EngineFacts:
    source: str = "none"  # "config" | "report" | "none"
    problems: tuple[str, ...] = ()  # codes de PROBLEM_LABELS quand source == "none"
    generated_at: str = ""
    llm_provider: str = ""
    llm_key: dict[str, bool] = field(default_factory=dict)      # fournisseur → clé renseignée
    # 4 derniers caractères de la clé, UNIQUEMENT pour l'affichage de « Paramètres système » (jamais journalisés :
    # ce champ ne passe par aucun appel logger/print, ici ni chez l'appelant). Absent (chaîne vide) si non renseignée.
    llm_key_last4: dict[str, str] = field(default_factory=dict)
    llm_model: dict[str, str] = field(default_factory=dict)     # fournisseur → nom de modèle configuré
    image_endpoint: bool = False
    image_model: str = ""
    image_key: bool = False
    image_key_last4: str = ""
    image_public_openai: bool = False
    # Gabarit d'images GLOBAL du moteur (texte de prompt, pas un secret) : appliqué à chaque image de chaque projet.
    image_template: str = ""
    eleven_key: bool = False
    eleven_key_last4: str = ""
    eleven_model: str = ""
    ui: dict[str, Any] = field(default_factory=dict)

    @property
    def known(self) -> bool:
        return self.source != "none"

    def key_present(self, provider: str) -> bool | None:
        """Vrai/faux si le champ existe, ``None`` s'il n'existe pas pour ce fournisseur."""
        if provider in KEYLESS_LLM:
            return True
        return self.llm_key.get(provider)

    @property
    def image_template_neutral(self) -> bool:
        """Vrai si le moteur n'ajoute rien au prompt d'image d'un projet : aucun gabarit, juste ``{term}``, ou un texte
        sans ``{term}`` (le moteur l'ignore alors et garde le prompt du projet tel quel)."""
        compact = re.sub(r"\s+", "", self.image_template)
        return compact in ("", "{term}") or "{term}" not in self.image_template

    def to_report(self) -> dict[str, Any]:
        data = asdict(self)
        for name in ("source", "problems"):
            data.pop(name)
        return data


def _filled(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    return isinstance(value, list) and any(isinstance(item, str) and item.strip() for item in value)


def _name(value: Any) -> str:
    return value.strip()[:80] if isinstance(value, str) else ""


def _last4(value: Any) -> str:
    """4 derniers caractères, pour l'affichage seul (voir le commentaire sur ``llm_key_last4``). Chaîne vide si la
    valeur est absente ou trop courte pour qu'un indice de 4 caractères ait un sens."""
    if isinstance(value, list):
        value = next((item for item in value if isinstance(item, str) and item.strip()), "")
    if not isinstance(value, str):
        return ""
    stripped = value.strip()
    return stripped[-4:] if len(stripped) >= 4 else ""


def facts_from_config(raw: dict[str, Any], source: str = "config") -> EngineFacts:
    """Extrait les faits. Aucune valeur de clé n'est conservée (seulement « renseignée ou non »)."""
    app = raw.get("app") if isinstance(raw.get("app"), dict) else {}
    eleven = raw.get("elevenlabs") if isinstance(raw.get("elevenlabs"), dict) else {}
    ui = raw.get("ui") if isinstance(raw.get("ui"), dict) else {}
    llm_key = {key[: -len("_api_key")]: _filled(value) for key, value in app.items() if key.endswith("_api_key")}
    llm_key_last4 = {key[: -len("_api_key")]: _last4(value) for key, value in app.items() if key.endswith("_api_key")}
    llm_model = {key[: -len("_model_name")]: _name(value) for key, value in app.items()
                 if key.endswith("_model_name") and _name(value)}
    base = _name(app.get("openai_image_base_url"))
    prefs = {key: ui[key] for key in _UI_KEYS if isinstance(ui.get(key), (str, int, float, bool))}
    if ui.get("subtitle_background_enabled") is True and _name(ui.get("subtitle_background_color")):
        prefs["text_background_color"] = _name(ui.get("subtitle_background_color"))
    return EngineFacts(
        source=source, llm_provider=_name(app.get("llm_provider")).lower(), llm_key=llm_key,
        llm_key_last4={k: v for k, v in llm_key_last4.items() if v}, llm_model=llm_model,
        image_endpoint=bool(base and _name(app.get("openai_image_model"))), image_model=_name(app.get("openai_image_model")),
        image_key=_filled(app.get("openai_image_api_keys")), image_key_last4=_last4(app.get("openai_image_api_keys")),
        image_public_openai="openai.com" in base,
        image_template=(app.get("openai_image_prompt_template") or "").strip()[:2000]
        if isinstance(app.get("openai_image_prompt_template"), str) else "",
        eleven_key=_filled(eleven.get("api_key")), eleven_key_last4=_last4(eleven.get("api_key")),
        eleven_model=_name(eleven.get("model_id")), ui=prefs,
    )


def _facts_from_report(data: dict[str, Any]) -> EngineFacts:
    facts = data["facts"]
    return EngineFacts(
        source="report", generated_at=str(data.get("generated_at", "")),
        llm_provider=str(facts.get("llm_provider", "")),
        llm_key={str(k): bool(v) for k, v in dict(facts.get("llm_key", {})).items()},
        llm_key_last4={str(k): str(v)[:4] for k, v in dict(facts.get("llm_key_last4", {})).items()},
        llm_model={str(k): _name(v) for k, v in dict(facts.get("llm_model", {})).items()},
        image_endpoint=bool(facts.get("image_endpoint")), image_model=_name(facts.get("image_model")),
        image_key=bool(facts.get("image_key")), image_key_last4=str(facts.get("image_key_last4", ""))[:4],
        image_public_openai=bool(facts.get("image_public_openai")),
        image_template=str(facts.get("image_template", ""))[:2000],
        eleven_key=bool(facts.get("eleven_key")), eleven_key_last4=str(facts.get("eleven_key_last4", ""))[:4],
        eleven_model=_name(facts.get("eleven_model")),
        ui={k: v for k, v in dict(facts.get("ui", {})).items() if isinstance(v, (str, int, float, bool))},
    )


def build_report(config_path: Path) -> dict[str, Any]:
    """Rapport de capacités de ``config_path`` (à exécuter par quelqu'un qui peut lire le fichier)."""
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)
    stat = config_path.stat()
    return {
        "version": REPORT_VERSION, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "config_mtime_ns": stat.st_mtime_ns, "config_size": stat.st_size,
        "facts": facts_from_config(raw, "report").to_report(),
    }


def resolve(config_path: Path | None = None, report_path: Path | None = None) -> EngineFacts:
    """Meilleure connaissance disponible de la configuration du moteur (lecture directe, sinon rapport)."""
    config = config_path or settings.config_path()
    problems: list[str] = []
    try:
        with config.open("rb") as handle:
            return facts_from_config(tomllib.load(handle))
    except FileNotFoundError:
        return EngineFacts(problems=("config_missing",))  # monté ailleurs : on ne croit aucun rapport
    except PermissionError:
        problems.append("config_unreadable")
    except (OSError, tomllib.TOMLDecodeError) as error:
        problems.append("config_invalid" if isinstance(error, tomllib.TOMLDecodeError) else "config_unreadable")
    report = report_path or settings.engine_report_path()
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
        if data.get("version") != REPORT_VERSION or not isinstance(data.get("facts"), dict):
            raise ValueError("format")
    except FileNotFoundError:
        return EngineFacts(problems=(*problems, "report_missing"))
    except (OSError, ValueError, KeyError, TypeError):
        return EngineFacts(problems=(*problems, "report_invalid"))
    try:
        stat = config.stat()  # stat n'exige pas le droit de lecture du fichier
        fresh = data.get("config_mtime_ns") == stat.st_mtime_ns and data.get("config_size") == stat.st_size
    except OSError:
        fresh = False
    if not fresh:
        return EngineFacts(problems=(*problems, "report_stale"))
    return _facts_from_report(data)


def _write_atomic(target: Path, payload: dict[str, Any]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, delete=False, suffix=".tmp") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temp = Path(handle.name)
    os.chmod(temp, 0o644)  # lisible par le conteneur : le rapport ne contient aucun secret
    os.replace(temp, target)


def write_report(config_path: Path, out_path: Path) -> dict[str, Any]:
    """Génère ET écrit le rapport (atomique). Réutilisé par ``apply_secrets`` juste après avoir changé
    ``config.toml``, pour éviter un aller-retour par un sous-processus séparé."""
    report = build_report(config_path)
    _write_atomic(out_path, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Génère le rapport de capacités du moteur (sans aucun secret).")
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    parser.add_argument("--out", type=Path, default=Path("engine-report/engine-capabilities.json"))
    args = parser.parse_args(argv)
    try:
        report = build_report(args.config)
    except (OSError, tomllib.TOMLDecodeError) as error:
        print(f"Impossible de lire {args.config} : {type(error).__name__}")
        return 1
    _write_atomic(args.out, report)
    facts = report["facts"]
    print(f"Rapport écrit : {args.out}")
    print(f"  fournisseur de texte du moteur : {facts['llm_provider'] or '(non défini)'}")
    print("  clés renseignées :", ", ".join(sorted(name for name, filled in facts["llm_key"].items() if filled)) or "(aucune)",
          "| images:", "oui" if facts["image_key"] else "non", "| voix:", "oui" if facts["eleven_key"] else "non")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
