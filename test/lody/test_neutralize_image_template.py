"""Outil de neutralisation du gabarit d'images global : sûr, réversible, sans afficher de valeur."""

from __future__ import annotations

import importlib.util
import stat
import tomllib
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "lody-neutralize-image-template.py"
spec = importlib.util.spec_from_file_location("neutralize", SCRIPT)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

CONFIG = """# commentaire conservé
[app]
llm_provider = "openai"
openai_api_key = "FAKE-CONFIG-VALUE-NOT-A-KEY"
openai_image_prompt_template = "professional broadcast television visual explaining {term}, control room"  # gabarit
video_source = "pexels"
[ui]
font_name = "BeVietnamPro-Bold.ttf"
"""


def _config(tmp_path, content=CONFIG):
    path = tmp_path / "config.toml"
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)
    return path


def test_neutralize_replaces_only_the_template_and_keeps_everything_else(tmp_path, capsys):
    path = _config(tmp_path)
    before = tomllib.loads(path.read_text(encoding="utf-8"))
    assert tool.main(["--config", str(path)]) == 0
    after = tomllib.loads(path.read_text(encoding="utf-8"))
    assert after["app"]["openai_image_prompt_template"] == ""
    assert {**after, "app": {**after["app"], "openai_image_prompt_template": before["app"]["openai_image_prompt_template"]}} == before
    text = path.read_text(encoding="utf-8")
    assert "# commentaire conservé" in text and "# gabarit" in text                     # commentaires intacts
    out = capsys.readouterr().out
    assert "FAKE-CONFIG" not in out and "broadcast" not in out                          # aucune valeur affichée
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    backups = list(tmp_path.glob("config.toml.bak-*"))
    assert len(backups) == 1 and stat.S_IMODE(backups[0].stat().st_mode) == 0o600
    assert backups[0].read_text(encoding="utf-8") == CONFIG                             # sauvegarde identique à l'original


def test_dry_run_and_already_neutral_write_nothing(tmp_path, capsys):
    path = _config(tmp_path)
    assert tool.main(["--config", str(path), "--dry-run"]) == 0
    assert path.read_text(encoding="utf-8") == CONFIG and not list(tmp_path.glob("*.bak*"))
    tool.main(["--config", str(path)])
    assert tool.main(["--config", str(path)]) == 0 and "déjà vide" in capsys.readouterr().out
    assert len(list(tmp_path.glob("config.toml.bak-*"))) == 1                           # pas de deuxième sauvegarde inutile
    bare = _config(tmp_path / "..", CONFIG.replace('openai_image_prompt_template = "professional broadcast television visual explaining {term}, control room"  # gabarit\n', ""))
    assert tool.main(["--config", str(bare)]) == 0 and "aucun gabarit" in capsys.readouterr().out


def test_unreadable_or_invalid_configs_are_refused_without_writing(tmp_path):
    assert tool.main(["--config", str(tmp_path / "absent.toml")]) == 1
    broken = _config(tmp_path, "[app\nnope")
    assert tool.main(["--config", str(broken)]) == 1 and broken.read_text(encoding="utf-8") == "[app\nnope"


@pytest.mark.parametrize("line", ["openai_image_prompt_template = 'un gabarit {term} guillemets simples'",
                                  'openai_image_prompt_template   =   "un gabarit avec \\"guillemets\\" {term}"'])
def test_quote_styles_are_handled(tmp_path, line):
    path = _config(tmp_path, f"[app]\nx = 1\n{line}\ny = 2\n")
    assert tool.main(["--config", str(path)]) == 0
    assert tomllib.loads(path.read_text(encoding="utf-8"))["app"] == {"x": 1, "openai_image_prompt_template": "", "y": 2}
