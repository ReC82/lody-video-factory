"""Kit de publication d'une production terminée : fichiers, YouTube, TikTok / Shorts, miniature, sous-titres, contrôle.

Rien n'est publié et aucun compte n'est connecté : tout est prévu pour une publication MANUELLE. Modifier un texte, composer la
miniature, exporter SRT/VTT et créer le ZIP sont locaux et gratuits ; seule la génération d'un NOUVEAU fond d'image peut coûter,
et uniquement après estimation puis confirmation explicite.
"""

from __future__ import annotations

import html
import json
from decimal import Decimal

import streamlit as st

from lody.components import format_datetime
from lody.generation import publication, thumbnail
from lody.generation.costing import format_range
from lody.generation.kit_service import KitService
from lody.generation.kit_store import Kit
from lody.generation.service import LaunchError
from lody.generation.store import Production
from lody.projects import Project
from lody.theme import esc

_COPY_STYLE = (
    "body{margin:0;background:transparent}button{font:600 13px system-ui,sans-serif;color:#22D3EE;background:rgba(34,211,238,.08);"
    "border:1px solid rgba(34,211,238,.45);border-radius:10px;padding:7px 14px;cursor:pointer;min-height:34px}"
    "button:hover{background:rgba(34,211,238,.18)}button:focus-visible{outline:2px solid #22D3EE;outline-offset:2px}"
)


def copy_button_html(text: str, label: str = "Copier") -> str:
    """Bouton « Copier » autonome (presse-papiers du navigateur, avec repli execCommand). Le texte est sérialisé en JSON
    (``</`` neutralisé) : aucune injection possible via le contenu copié."""
    payload = json.dumps(text).replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    done = json.dumps("Copié ✓")
    shown = json.dumps(label)
    return (
        f"<style>{_COPY_STYLE}</style><button id=\"b\" type=\"button\">{html.escape(label)}</button>"
        f"<script>const T={payload};const b=document.getElementById('b');"
        "b.addEventListener('click',async()=>{try{await navigator.clipboard.writeText(T);}catch(e){const a=document.createElement('textarea');"
        "a.value=T;a.style.position='fixed';a.style.opacity='0';document.body.appendChild(a);a.select();document.execCommand('copy');a.remove();}"
        f"b.textContent={done};setTimeout(()=>{{b.textContent={shown}}},1600);}});</script>"
    )


def copy_button(text: str, label: str = "Copier", *, height: int = 46) -> None:
    st.iframe(copy_button_html(text, label), height=height)


def _size(number: int) -> str:
    return f"{number / 1_000_000:.1f} Mo" if number >= 1_000_000 else f"{max(number // 1000, 1)} ko"


def _prefix(kit: Kit) -> str:
    return f"kit_{kit.id}"


# -- métadonnées : session ⇄ base ---------------------------------------------------------------------------------------------------------
_TEXT = ("title", "short_description", "description_youtube", "pinned_comment", "category", "playlist", "video_language", "text_language",
         "file_name", "warning", "tiktok_caption", "tiktok_hook", "tiktok_comment")


def _load_session(kit: Kit) -> None:
    """Recharge les champs depuis la base seulement quand les métadonnées ENREGISTRÉES ont changé (pas à chaque clic ailleurs)."""
    p, ss = _prefix(kit), st.session_state
    stamp = json.dumps(kit.metadata, sort_keys=True, ensure_ascii=False)
    if ss.get(f"{p}_loaded") == stamp:
        return
    meta = kit.metadata
    for key in _TEXT:
        ss[f"{p}_{key}"] = meta.get(key, "")
    ss[f"{p}_title_alternatives"] = "\n".join(meta.get("title_alternatives", []))
    ss[f"{p}_hashtags"] = " ".join(meta.get("hashtags", []))
    ss[f"{p}_tags"] = ", ".join(meta.get("tags", []))
    ss[f"{p}_tiktok_hashtags"] = " ".join(meta.get("tiktok_hashtags", []))
    ss[f"{p}_made_for_kids"] = bool(meta.get("made_for_kids"))
    ss[f"{p}_loaded"] = stamp


def _current(kit: Kit) -> dict:
    """Métadonnées telles qu'affichées (y compris modifications non enregistrées) : sert à copier et à enregistrer."""
    p, ss = _prefix(kit), st.session_state
    current = {key: ss.get(f"{p}_{key}", "") for key in _TEXT}
    current["title_alternatives"] = [line.strip() for line in str(ss.get(f"{p}_title_alternatives", "")).splitlines() if line.strip()]
    current["hashtags"] = publication.parse_hashtags(ss.get(f"{p}_hashtags", ""))
    current["tags"] = publication.parse_tags(ss.get(f"{p}_tags", ""))
    current["tiktok_hashtags"] = publication.parse_hashtags(ss.get(f"{p}_tiktok_hashtags", ""))
    current["made_for_kids"] = bool(ss.get(f"{p}_made_for_kids"))
    return current


def _flash(kind: str, message: str) -> None:
    st.session_state["_kit_flash"] = (kind, message)


def _save(kits: KitService, kit_id: str) -> None:
    kit = kits.kits.get(kit_id)
    try:
        kits.save_metadata(kit_id, _current(kit))
        _flash("success", "Modifications enregistrées (gratuit).")
    except LaunchError as error:
        _flash("error", error.message)


def _restore(kits: KitService, kit_id: str) -> None:
    kits.restore_initial(kit_id)
    _flash("success", "Propositions initiales restaurées.")


def _regenerate(kits: KitService, kit_id: str) -> None:
    try:
        kits.regenerate_metadata(kit_id)
        _flash("success", "Nouvelles propositions locales générées (gratuit) : la vidéo n’a pas été touchée.")
    except LaunchError as error:
        _flash("error", error.message)


def _field(kit: Kit, key: str, label: str, *, kind: str = "input", height: int = 100, help_text: str = "") -> str:
    """Champ modifiable + bouton « Copier » (copie ce qui est affiché, enregistré ou non)."""
    name = f"{_prefix(kit)}_{key}"
    if kind == "area":
        st.text_area(label, key=name, height=height, help=help_text or None)
    else:
        st.text_input(label, key=name, help=help_text or None)
    copy_button(str(st.session_state.get(name, "")), f"Copier — {label}")
    return name


def _dirty(kit: Kit) -> bool:
    saved = {k: kit.metadata.get(k) for k in kit.metadata}
    now = _current(kit)
    return any(now.get(k) != saved.get(k) for k in now if k in saved)


def _actions(kits: KitService, kit: Kit) -> None:
    with st.container(horizontal=True, vertical_alignment="center", key="kit_meta_actions"):
        st.button("Enregistrer les modifications", type="primary", icon=":material/save:", key=f"{_prefix(kit)}_save",
                  on_click=_save, args=(kits, kit.id))
        st.button("Restaurer les propositions initiales", icon=":material/undo:", key=f"{_prefix(kit)}_restore",
                  on_click=_restore, args=(kits, kit.id), disabled=kit.metadata == kit.initial_metadata)
        st.button("Nouvelles propositions (gratuit)", icon=":material/autorenew:", key=f"{_prefix(kit)}_regen",
                  on_click=_regenerate, args=(kits, kit.id), help="Recalcule les propositions depuis le script ; la vidéo n’est pas régénérée.")
    if _dirty(kit):
        st.markdown('<p class="muted-note kit-dirty" role="status">Modifications non enregistrées.</p>', unsafe_allow_html=True)


# -- onglets -------------------------------------------------------------------------------------------------------------------------------------------
def _tab_youtube(kit: Kit) -> None:
    _field(kit, "title", "Titre principal")
    _field(kit, "title_alternatives", "Titres alternatifs (un par ligne)", kind="area", height=90)
    _field(kit, "short_description", "Description courte")
    _field(kit, "description_youtube", "Description YouTube", kind="area", height=280)
    _field(kit, "hashtags", "Hashtags (séparés par des espaces)", help_text="Quelques hashtags ciblés valent mieux qu'une longue liste.")
    _field(kit, "tags", "Tags / mots-clés (séparés par des virgules, sans #)", kind="area", height=90)
    _field(kit, "pinned_comment", "Commentaire épinglé suggéré", kind="area", height=80)
    columns = st.columns(2)
    with columns[0]:
        _field(kit, "playlist", "Playlist suggérée")
        _field(kit, "video_language", "Langue de la vidéo")
    with columns[1]:
        _field(kit, "category", "Catégorie suggérée")
        _field(kit, "text_language", "Langue du titre et de la description")
    st.checkbox("Vidéo destinée aux enfants", key=f"{_prefix(kit)}_made_for_kids")
    st.markdown(f'<p class="muted-note">Audience déclarée : <strong>{esc(publication.audience_label(_current(kit)["made_for_kids"]))}</strong> '
                "(à déclarer soi-même à la publication).</p>", unsafe_allow_html=True)
    _field(kit, "file_name", "Nom de fichier propre")
    _field(kit, "warning", "Avertissement éventuel", kind="area", height=70)
    with st.container(horizontal=True, key=f"{_prefix(kit)}_yt_all"):
        copy_button(publication.youtube_text(_current(kit)), "Tout copier pour YouTube")


def _tab_tiktok(kit: Kit) -> None:
    _field(kit, "tiktok_caption", "Légende courte", kind="area", height=90)
    _field(kit, "tiktok_hook", "Texte d’accroche (à afficher à l’écran)")
    _field(kit, "tiktok_hashtags", "Hashtags adaptés (séparés par des espaces)")
    _field(kit, "tiktok_comment", "Commentaire suggéré", kind="area", height=80)
    copy_button(publication.tiktok_text(_current(kit)), "Tout copier pour TikTok")


# -- miniature ------------------------------------------------------------------------------------------------------------------------------------------
@st.cache_data(show_spinner=False, max_entries=24)
def _previews(_kits: KitService, kit_id: str, revision: str) -> dict[str, dict]:
    """Trois compositions + leur aperçu téléphone, calculés UNE fois par état de la miniature (local, gratuit)."""
    kit = _kits.kits.get(kit_id)
    production = _kits.production_of(kit)
    out = {}
    for variant, (image, layout) in _kits.render_all(kit, production).items():
        report = thumbnail.readability(layout)
        out[variant] = {"png": thumbnail.to_png(thumbnail.phone_preview(image, 240)), "ok": report.ok, "glyph": report.phone_glyph_px,
                        "contrast": report.contrast_ratio, "messages": list(report.messages), "text": layout.text}
    return out


def _apply_thumbnail(kits: KitService, kit_id: str) -> None:
    ss, p = st.session_state, f"kit_{kit_id}"
    try:
        kits.set_thumbnail(kit_id, text=ss.get(f"{p}_thumb_text"), variant=ss.get(f"{p}_thumb_variant"))
    except LaunchError as error:
        _flash("error", error.message)


def _choose_background(kits: KitService, kit_id: str, ref: str) -> None:
    try:
        kits.set_thumbnail(kit_id, background_ref=ref)
        _flash("success", "Fond choisi : la composition est refaite gratuitement.")
    except LaunchError as error:
        _flash("error", error.message)


def _prepare_bg(kits: KitService, kit_id: str) -> None:
    try:
        kits.prepare_background(kit_id)
    except LaunchError as error:
        _flash("error", error.message)


def _confirm_bg(kits: KitService, kit_id: str) -> None:
    try:
        kits.confirm_background(kit_id, accept_partial=bool(st.session_state.get(f"kit_{kit_id}_accept_bg")))
    except LaunchError as error:
        _flash("error", error.message)


def _cancel_bg(kits: KitService, kit_id: str) -> None:
    kits.cancel_background(kit_id)


def _paid_background(kits: KitService, kit: Kit, production: Production) -> None:
    provider = kits.productions.provider(production.provider)
    with st.expander("Nouveau fond d’image (payant, après estimation)"):
        if not provider.supports_thumbnail_background:
            st.markdown('<div class="banner banner-info" role="note">Le moteur actuel ne sait pas générer une image de fond seule : '
                        "la miniature utilise une image de scène déjà produite avec la vidéo (<strong>gratuit</strong>). "
                        "Ce bouton apparaîtra avec un moteur qui le permet.</div>", unsafe_allow_html=True)
            return
        job = kit.background_job or {"state": "idle"}
        state = job.get("state", "idle")
        st.markdown('<p class="muted-note">Le <strong>texte</strong> est toujours ajouté localement, gratuitement. Seule l’image de fond '
                    "est payante, et jamais sans ton accord.</p>", unsafe_allow_html=True)
        if state in ("idle", "failed"):
            if state == "failed":
                st.markdown(f'<div class="banner banner-error" role="alert">Le dernier fond a échoué : {esc(job.get("message", ""))} '
                            "Rien n’est perdu : ton fond actuel est conservé.</div>", unsafe_allow_html=True)
            st.button("Estimer un nouveau fond", icon=":material/calculate:", key=f"kit_{kit.id}_prep_bg", on_click=_prepare_bg, args=(kits, kit.id))
        elif state == "awaiting_confirmation":
            estimate = job.get("estimate", {})
            low, high = (Decimal(estimate["low"]) if estimate.get("low") else None), (Decimal(estimate["high"]) if estimate.get("high") else None)
            cost = format_range(low, high, estimate.get("currency", "EUR")) if high is not None else "tarif non configuré"
            note = "Simulation : aucun coût réel." if job.get("demo") else "Montant indicatif."
            st.markdown(f'<div class="banner banner-info" role="note"><strong>Estimation : {esc(cost)}</strong> pour 1 image de fond. {esc(note)} '
                        "Rien n’est lancé tant que tu n’as pas confirmé.</div>", unsafe_allow_html=True)
            with st.expander("Prompt envoyé au fournisseur d’images"):
                st.code(job.get("prompt", ""), language=None)
            if estimate.get("partial") and not job.get("demo"):
                st.checkbox("Je comprends que le tarif n’est pas configuré : le coût est inconnu.", key=f"kit_{kit.id}_accept_bg")
            with st.container(horizontal=True, key=f"kit_{kit.id}_bg_confirm"):
                st.button("Confirmer et générer le fond", type="primary", icon=":material/rocket_launch:", key=f"kit_{kit.id}_conf_bg",
                          on_click=_confirm_bg, args=(kits, kit.id))
                st.button("Annuler", key=f"kit_{kit.id}_cancel_bg", on_click=_cancel_bg, args=(kits, kit.id))
        else:
            st.markdown('<div class="banner banner-info" role="status">Génération du fond en cours…</div>', unsafe_allow_html=True)
            st.button("Actualiser", icon=":material/refresh:", key=f"kit_{kit.id}_refresh_bg")


def _tab_thumbnail(kits: KitService, kit: Kit, production: Production) -> None:
    p, ss = _prefix(kit), st.session_state
    ss.setdefault(f"{p}_thumb_text", kit.thumbnail.get("text", ""))
    ss.setdefault(f"{p}_thumb_variant", kit.thumbnail.get("variant", "centre"))
    options = kits.background_options(production)
    if not options:
        st.markdown('<div class="banner banner-error" role="alert">Aucun fond disponible pour cette production : les images de scène '
                    "sont introuvables.</div>", unsafe_allow_html=True)
        return
    st.text_input("Texte de la miniature (2 à 5 mots, court et lisible)", key=f"{p}_thumb_text", max_chars=thumbnail.TEXT_MAX_CHARS,
                  on_change=_apply_thumbnail, args=(kits, kit.id))
    words = thumbnail.word_count(ss.get(f"{p}_thumb_text", ""))
    st.markdown(f'<p class="muted-note">{words} mot{"s" if words > 1 else ""} — le texte est dessiné localement, jamais par le modèle d’image. '
                "Le rendu se refait gratuitement à chaque modification.</p>", unsafe_allow_html=True)
    revision = json.dumps(kit.thumbnail, sort_keys=True, ensure_ascii=False)
    try:
        previews = _previews(kits, kit.id, revision)
    except (LaunchError, ValueError, OSError):
        st.markdown('<div class="banner banner-error" role="alert">Le fond choisi est illisible : choisis-en un autre ci-dessous.</div>',
                    unsafe_allow_html=True)
        previews = {}
    if previews:
        st.radio("Composition", list(thumbnail.VARIANTS), key=f"{p}_thumb_variant", horizontal=True,
                 format_func=lambda variant: thumbnail.VARIANT_LABELS[variant], on_change=_apply_thumbnail, args=(kits, kit.id))
        columns = st.columns(3)
        for column, variant in zip(columns, thumbnail.VARIANTS, strict=True):
            data = previews[variant]
            with column:
                st.image(data["png"], caption=("✓ " if variant == kit.thumbnail.get("variant") else "") + thumbnail.VARIANT_LABELS[variant],
                         width="stretch")
        chosen = previews[kit.thumbnail.get("variant", "centre")]
        tone = "ok" if chosen["ok"] else "fail"
        st.markdown(
            f'<div class="kit-phone kit-phone-{tone}" role="status"><strong>Aperçu téléphone (360 px)</strong> : texte de '
            f'{chosen["glyph"]:.0f} px, contraste {chosen["contrast"]:.1f}:1 — {"lisible" if chosen["ok"] else "à améliorer"}'
            + ("".join(f"<br>{esc(m)}" for m in chosen["messages"])) + "</div>", unsafe_allow_html=True)
        with st.container(horizontal=True, key=f"{p}_thumb_dl"):
            st.download_button("Télécharger en PNG", data=lambda: kits.thumbnail_bytes(kits.kits.get(kit.id), production, "png"),
                               file_name="miniature.png", mime="image/png", icon=":material/download:", key=f"{p}_dl_png", type="primary")
            st.download_button("Télécharger en JPEG", data=lambda: kits.thumbnail_bytes(kits.kits.get(kit.id), production, "jpeg"),
                               file_name="miniature.jpg", mime="image/jpeg", icon=":material/download:", key=f"{p}_dl_jpg")
    st.markdown('<p class="card-eyebrow">Fond de la miniature</p>', unsafe_allow_html=True)
    for note in kits.background_warnings(production):
        st.markdown(f'<p class="muted-note">{esc(note)}</p>', unsafe_allow_html=True)
    current_ref = (kit.thumbnail.get("background") or {}).get("ref")
    grid = st.columns(4)
    for index, option in enumerate(options[:8]):
        with grid[index % 4]:
            try:
                st.image(str(kits.background_file(production, option)), caption=option["label"], width="stretch")
            except (ValueError, OSError):
                st.caption(option["label"])
            st.button("Fond choisi ✓" if option["ref"] == current_ref else "Choisir ce fond", key=f"{p}_bg_{index}",
                      disabled=option["ref"] == current_ref, on_click=_choose_background, args=(kits, kit.id, option["ref"]))
    _paid_background(kits, kit, production)


# -- sous-titres, fichiers, contrôle -------------------------------------------------------------------------------------------------------------------------
def _tab_subtitles(kits: KitService, production: Production) -> None:
    bundle = kits.subtitles(production)
    st.markdown('<div class="banner banner-info" role="note"><strong>Les sous-titres sont déjà incrustés dans le MP4.</strong> Ces fichiers '
                "(langue : français) sont fournis à part pour YouTube et TikTok ; leurs minutages sont ceux de la vidéo finale. Gratuit.</div>",
                unsafe_allow_html=True)
    if not bundle.available:
        st.markdown('<div class="banner banner-error" role="alert">Les sous-titres du moteur sont introuvables : la vidéo garde ses '
                    "sous-titres incrustés, mais aucun fichier séparé ne peut être fourni.</div>", unsafe_allow_html=True)
        return
    check = bundle.check
    tone = "ok" if check and check.ok else "fail"
    detail = f"{len(bundle.cues)} sous-titres, dernier terminé à {check.last_end_ms / 1000:.1f} s" + (
        f" (durée de la vidéo : {check.duration_ms / 1000:.1f} s)" if check.duration_ms else "")
    st.markdown(f'<p class="kit-check kit-check-{tone}">{esc(detail)}</p>', unsafe_allow_html=True)
    for fix in (check.fixes if check else ()):
        st.markdown(f'<p class="muted-note">Correction automatique : {esc(fix)}.</p>', unsafe_allow_html=True)
    st.code(bundle.srt[:6000], language=None)
    with st.container(horizontal=True, key="kit_sub_dl"):
        st.download_button("Télécharger le SRT", data=bundle.srt.encode("utf-8"), file_name="subtitles-fr.srt", mime="application/x-subrip",
                           icon=":material/download:", key="kit_dl_srt", type="primary")
        st.download_button("Télécharger le VTT", data=bundle.vtt.encode("utf-8"), file_name="subtitles-fr.vtt", mime="text/vtt",
                           icon=":material/download:", key="kit_dl_vtt")


def _tab_files(kits: KitService, kit: Kit, production: Production) -> None:
    try:
        video, repaired = kits.video_path(production)
        video_line = f"video.mp4 — {_size(video.stat().st_size)}" + (" (rendu corrigé)" if repaired else "")
    except (ValueError, OSError, LaunchError):
        video, video_line = None, "vidéo introuvable"
    bundle = kits.subtitles(production)
    rows = [("Vidéo", video_line), ("Miniature", f"thumbnail.png / .jpg — texte « {kit.thumbnail.get('text', '')} »"),
            ("Sous-titres", "subtitles-fr.srt et .vtt" if bundle.available else "non disponibles"),
            ("Textes", "publication-youtube.txt, publication-tiktok.txt, metadata.json")]
    st.markdown('<dl class="kv">' + "".join(f"<div><dt>{esc(a)}</dt><dd>{esc(b)}</dd></div>" for a, b in rows) + "</dl>", unsafe_allow_html=True)
    if video is not None:
        st.download_button("Télécharger le kit (ZIP)", data=lambda: kits.build_zip(kits.kits.get(kit.id), production), file_name=f"{kit.metadata.get('file_name', 'kit.mp4').removesuffix('.mp4')}-kit.zip",
                           mime="application/zip", icon=":material/folder_zip:", key=f"kit_{kit.id}_zip", type="primary")
    st.markdown('<p class="muted-note">Le ZIP contient la vidéo, la miniature, les sous-titres SRT/VTT, les textes YouTube et TikTok et un '
                "metadata.json — aucun chemin serveur, secret ou identifiant interne. Sa création est gratuite.</p>", unsafe_allow_html=True)


def _tab_checks(kits: KitService, kit: Kit, production: Production) -> None:
    ok_all = True
    for check in kits.checks(kit, production):
        icon, tone = ("✓", "ok") if check.ok else (("!", "warn") if check.ok is None else ("✗", "fail"))
        ok_all = ok_all and check.ok is not False
        st.markdown(f'<div class="kit-check kit-check-{tone}"><strong>{icon} {esc(check.title)}</strong><span>{esc(check.detail)}</span></div>',
                    unsafe_allow_html=True)
    st.markdown('<p class="muted-note">Ce contrôle est indicatif : relis toujours les textes avant de publier.</p>', unsafe_allow_html=True)


# -- point d'entrée ---------------------------------------------------------------------------------------------------------------------------------------------------
def render(kits: KitService, project: Project, production: Production) -> None:
    try:
        kit = kits.ensure_kit(production.id)
    except (LaunchError, ValueError, OSError) as error:
        st.markdown(f'<div class="banner banner-error" role="alert">Kit de publication indisponible : {esc(getattr(error, "message", "erreur"))}</div>',
                    unsafe_allow_html=True)
        return
    _load_session(kit)
    with st.container(key="kit"):
        st.markdown('<p class="card-eyebrow">Publication manuelle</p><h2 class="brief-title">Kit de publication</h2>'
                    '<p class="muted-note">Rien n’est publié automatiquement et aucun compte n’est connecté. <strong>Gratuit :</strong> '
                    "modifier les textes, composer la miniature, exporter SRT/VTT, créer le ZIP. <strong>Payant seulement</strong> si tu demandes "
                    "un nouveau fond d’image — après estimation et confirmation.</p>", unsafe_allow_html=True)
        flash = st.session_state.pop("_kit_flash", None)
        if flash:
            st.markdown(f'<div class="banner banner-{esc(flash[0])}" role="status">{esc(flash[1])}</div>', unsafe_allow_html=True)
        _actions(kits, kit)
        tabs = st.tabs(["Fichiers", "YouTube", "TikTok / Shorts", "Miniature", "Sous-titres", "Contrôle"])
        with tabs[0]:
            _tab_files(kits, kit, production)
        with tabs[1]:
            _tab_youtube(kit)
        with tabs[2]:
            _tab_tiktok(kit)
        with tabs[3]:
            _tab_thumbnail(kits, kit, production)
        with tabs[4]:
            _tab_subtitles(kits, production)
        with tabs[5]:
            _tab_checks(kits, kit, production)
        with st.expander("Historique des modifications"):
            for entry in reversed(kit.history):
                fields = ", ".join(entry.get("fields", [])) or "—"
                st.markdown(f"- **{esc(format_datetime(entry['at']))}** — {esc(entry['action'])} ({esc(fields)})")
