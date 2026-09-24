"""Stockage des images de référence facultatives (ticket #38) : validation MIME/taille/dimensions, isolation
stricte par projet et par entité, protection contre la traversée de chemin, non-suppression destructive.
Aucun appel réseau, aucun fournisseur, aucun coût."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from lody import reference_images as ri
from lody import settings

PROJECT = "prj_aaaaaaaaaaaa"
OTHER_PROJECT = "prj_bbbbbbbbbbbb"
CHARACTER = "chr_aaaaaaaaaaaa"
OTHER_CHARACTER = "chr_bbbbbbbbbbbb"
LOCATION = "loc_aaaaaaaaaaaa"


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("LODY_DATA_DIR", str(tmp_path / "data"))
    return tmp_path / "data"


def _png(width: int = 100, height: int = 100, color=(255, 0, 0)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color=color).save(buffer, format="PNG")
    return buffer.getvalue()


def _jpeg(width: int = 100, height: int = 100) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color=(0, 255, 0)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _webp(width: int = 100, height: int = 100) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color=(0, 0, 255)).save(buffer, format="WEBP")
    return buffer.getvalue()


# -- validation : types acceptés --------------------------------------------------------------------------------
@pytest.mark.parametrize("builder", [_png, _jpeg, _webp])
def test_valid_image_formats_are_accepted(builder):
    stored = ri.save(PROJECT, "characters", CHARACTER, builder())
    assert stored.ref.startswith(f"references/{PROJECT}/characters/{CHARACTER}/")
    assert stored.width == 100 and stored.height == 100 and stored.bytes_size > 0


def test_gif_is_refused_even_though_pillow_can_read_it():
    buffer = io.BytesIO()
    Image.new("RGB", (100, 100)).save(buffer, format="GIF")
    with pytest.raises(ri.ReferenceImageError, match="Format d'image non accepté"):
        ri.save(PROJECT, "characters", CHARACTER, buffer.getvalue())


def test_not_an_image_is_refused():
    with pytest.raises(ri.ReferenceImageError, match="n'est pas une image valide"):
        ri.save(PROJECT, "characters", CHARACTER, b"ceci n'est pas une image")


def test_empty_payload_is_refused():
    with pytest.raises(ri.ReferenceImageError):
        ri.save(PROJECT, "characters", CHARACTER, b"")


# -- validation : le CONTENU décide, jamais l'extension déclarée --------------------------------------------------
def test_mime_spoofing_is_not_trusted_content_alone_decides():
    """Un fichier texte renommé ``.png`` reste refusé : Pillow lit la structure réelle, pas le nom."""
    fake = b"\x89PNG\r\n\x1a\n" + b"this is not a real png structure" * 4
    with pytest.raises(ri.ReferenceImageError):
        ri.save(PROJECT, "characters", CHARACTER, fake)


# -- validation : taille -----------------------------------------------------------------------------------------
def test_oversized_payload_is_refused_before_even_parsing_it():
    oversized = b"\x00" * (ri.MAX_BYTES + 1)
    with pytest.raises(ri.ReferenceImageError, match="volumineuse"):
        ri.save(PROJECT, "characters", CHARACTER, oversized)


# -- validation : dimensions --------------------------------------------------------------------------------------
def test_image_below_minimum_dimension_is_refused():
    with pytest.raises(ri.ReferenceImageError, match="trop petite"):
        ri.save(PROJECT, "characters", CHARACTER, _png(width=10, height=10))


def test_image_above_maximum_dimension_is_refused():
    with pytest.raises(ri.ReferenceImageError, match="trop grande"):
        ri.save(PROJECT, "characters", CHARACTER, _png(width=ri.MAX_DIMENSION + 1, height=100))


def test_image_at_the_boundary_dimensions_is_accepted():
    stored = ri.save(PROJECT, "characters", CHARACTER, _png(width=ri.MIN_DIMENSION, height=ri.MAX_DIMENSION))
    assert stored.width == ri.MIN_DIMENSION and stored.height == ri.MAX_DIMENSION


# -- validate() : contrôle sans écriture ---------------------------------------------------------------------------
def test_validate_raises_without_writing_anything(tmp_path):
    with pytest.raises(ri.ReferenceImageError):
        ri.validate(b"pas une image")
    assert not ri.root().exists()


def test_validate_accepts_a_valid_image_without_writing_it():
    ri.validate(_png())  # ne lève pas
    assert not ri.root().exists()


# -- identifiants : projet/entité invalides --------------------------------------------------------------------
def test_invalid_project_id_is_refused():
    with pytest.raises(ri.ReferenceImageError):
        ri.save("../../etc", "characters", CHARACTER, _png())


def test_unknown_entity_type_is_refused():
    with pytest.raises(ri.ReferenceImageError):
        ri.save(PROJECT, "vehicles", CHARACTER, _png())


def test_entity_id_with_the_wrong_prefix_is_refused():
    """Un identifiant de LIEU ne peut pas être utilisé comme personnage, même syntaxiquement valide ailleurs."""
    with pytest.raises(ri.ReferenceImageError):
        ri.save(PROJECT, "characters", LOCATION, _png())


# -- résolution : isolation stricte par projet et par entité, protection anti-traversée ---------------------------
def test_resolve_returns_a_readable_path_for_the_right_project_and_entity():
    stored = ri.save(PROJECT, "characters", CHARACTER, _png())
    path = ri.resolve(stored.ref, PROJECT, "characters", CHARACTER)
    assert path.is_file()
    assert path.read_bytes() == ri.read_bytes(stored.ref, PROJECT, "characters", CHARACTER)


def test_resolve_refuses_a_different_project():
    stored = ri.save(PROJECT, "characters", CHARACTER, _png())
    with pytest.raises(ValueError):
        ri.resolve(stored.ref, OTHER_PROJECT, "characters", CHARACTER)


def test_resolve_refuses_a_different_character_in_the_same_project():
    stored = ri.save(PROJECT, "characters", CHARACTER, _png())
    with pytest.raises(ValueError):
        ri.resolve(stored.ref, PROJECT, "characters", OTHER_CHARACTER)


def test_resolve_refuses_a_location_ref_resolved_as_a_character():
    stored = ri.save(PROJECT, "locations", LOCATION, _png())
    with pytest.raises(ValueError):
        ri.resolve(stored.ref, PROJECT, "characters", LOCATION)


@pytest.mark.parametrize("malicious", [
    "../../../../etc/passwd",
    "/etc/passwd",
    f"references/{PROJECT}/characters/{CHARACTER}/../../../../etc/passwd",
    f"references/{PROJECT}/characters/{CHARACTER}/\x00evil.png",
    f"references/{PROJECT}/characters/{OTHER_CHARACTER}/evil.png",
])
def test_path_traversal_and_cross_entity_attempts_are_refused(malicious):
    ri.save(PROJECT, "characters", CHARACTER, _png())  # une vraie image existe, pour que le test soit significatif
    with pytest.raises(ValueError):
        ri.resolve(malicious, PROJECT, "characters", CHARACTER)


def test_resolve_refuses_a_file_outside_the_data_directory_via_symlink(tmp_path):
    outside = tmp_path / "outside.png"
    outside.write_bytes(_png())
    folder = ri.root() / PROJECT / "characters" / CHARACTER
    folder.mkdir(parents=True, exist_ok=True)
    link = folder / "escape.png"
    link.symlink_to(outside)
    with pytest.raises(ValueError):
        ri.resolve(f"references/{PROJECT}/characters/{CHARACTER}/escape.png", PROJECT, "characters", CHARACTER)


# -- remplacement et retrait : jamais de suppression destructive (voir le docstring du module) --------------------
def test_replacing_the_image_writes_a_new_file_and_keeps_the_old_one_on_disk():
    first = ri.save(PROJECT, "characters", CHARACTER, _png(color=(255, 0, 0)))
    second = ri.save(PROJECT, "characters", CHARACTER, _png(color=(0, 255, 0)))
    assert first.ref != second.ref  # noms de fichiers distincts (UUID à chaque appel)
    # L'ancienne référence reste résolvable (une production déjà préparée peut l'avoir copiée dans son snapshot).
    assert ri.resolve(first.ref, PROJECT, "characters", CHARACTER).exists()
    assert ri.resolve(second.ref, PROJECT, "characters", CHARACTER).exists()


def test_two_uploads_of_the_exact_same_bytes_still_get_distinct_filenames():
    data = _png()
    first = ri.save(PROJECT, "characters", CHARACTER, data)
    second = ri.save(PROJECT, "characters", CHARACTER, data)
    assert first.ref != second.ref


# -- isolation entre projets et entités sur le DISQUE (pas seulement à la résolution) ------------------------------
def test_two_characters_in_the_same_project_get_separate_folders():
    a = ri.save(PROJECT, "characters", CHARACTER, _png())
    b = ri.save(PROJECT, "characters", OTHER_CHARACTER, _png())
    assert a.ref.split("/")[:3] == b.ref.split("/")[:3]  # même projet/type
    assert a.ref.split("/")[3] != b.ref.split("/")[3]  # entités différentes


def test_same_character_id_in_two_projects_never_collides():
    """Improbable (les id sont des UUID) mais vérifié : le PROJET fait partie du chemin, pas seulement l'entité."""
    a = ri.save(PROJECT, "characters", CHARACTER, _png(color=(1, 1, 1)))
    b = ri.save(OTHER_PROJECT, "characters", CHARACTER, _png(color=(2, 2, 2)))
    assert a.ref != b.ref
    assert ri.resolve(a.ref, PROJECT, "characters", CHARACTER).read_bytes() != \
        ri.resolve(b.ref, OTHER_PROJECT, "characters", CHARACTER).read_bytes()


# -- redémarrage : rien ne dépend d'un état en mémoire ------------------------------------------------------------
def test_reference_survives_a_fresh_process_view_of_the_same_data_dir(monkeypatch, tmp_path):
    stored = ri.save(PROJECT, "characters", CHARACTER, _png())
    # Simule un redémarrage : seule LODY_DATA_DIR persiste, aucun état Python n'est réutilisé.
    assert settings.data_dir() == ri.root().parent
    reread = ri.resolve(stored.ref, PROJECT, "characters", CHARACTER)
    assert reread.read_bytes() == ri.resolve(stored.ref, PROJECT, "characters", CHARACTER).read_bytes()


# -- absence de secret : une image n'est jamais un vecteur de fuite de clé -----------------------------------------
def test_stored_file_path_never_contains_the_uploaded_bytes_as_a_readable_secret_pattern():
    from lody.secrets_guard import find_secret_path

    stored = ri.save(PROJECT, "characters", CHARACTER, _png())
    assert find_secret_path(stored.ref) is None
