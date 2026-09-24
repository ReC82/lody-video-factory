"""Catalogue de voix ElevenLabs (#55) : client HTTP simulé (aucun appel réseau réel), pagination, recherche,
résolution du rapport assaini (valide/absent/périmé/invalide/vide), rechargement après remplacement
atomique, erreurs (401/403/429/5xx/timeout/réseau), absence de fuite de la clé, génération du rapport côté
hôte. Le conteneur Lody ne doit JAMAIS ouvrir config.toml pour cette fonctionnalité (voir les tests dédiés)."""

from __future__ import annotations

import inspect
import json
import os
import tomllib
import urllib.error
from pathlib import Path

import pytest

from lody.generation import elevenlabs_voices as ev

SECRET_KEY = "sk-real-secret-should-never-leak-anywhere-0123456789"


def _json_transport(pages):
    """Transport simulé : consomme ``pages`` (status, body_dict, response_headers) dans l'ordre, une par appel."""
    calls: list[dict] = []

    def transport(url, params, headers, timeout):
        calls.append({"url": url, "params": dict(params), "headers": dict(headers), "timeout": timeout})
        status, body, response_headers = pages.pop(0)
        return status, json.dumps(body).encode("utf-8"), response_headers

    transport.calls = calls
    return transport


def _voice(voice_id="v1", name="Rachel", **extra):
    return {"voice_id": voice_id, "name": name, **extra}


# -- récupération réussie et pagination ------------------------------------------------------------------------
def test_fetch_all_follows_pagination_until_has_more_is_false():
    transport = _json_transport([
        (200, {"voices": [_voice("v1", "Rachel")], "has_more": True, "next_page_token": "p2"}, {}),
        (200, {"voices": [_voice("v2", "Adam")], "has_more": True, "next_page_token": "p3"}, {}),
        (200, {"voices": [_voice("v3", "Bella")], "has_more": False}, {}),
    ])
    voices = ev.fetch_all(SECRET_KEY, transport=transport)
    assert [v.name for v in voices] == ["Rachel", "Adam", "Bella"]
    assert transport.calls[1]["params"]["next_page_token"] == "p2"
    assert transport.calls[2]["params"]["next_page_token"] == "p3"


def test_fetch_all_stops_at_max_pages_safety_cap():
    pages = [(200, {"voices": [_voice(f"v{i}", f"Voix {i}")], "has_more": True, "next_page_token": f"p{i}"}, {})
             for i in range(10)]
    transport = _json_transport(list(pages))
    voices = ev.fetch_all(SECRET_KEY, max_pages=3, transport=transport)
    assert len(voices) == 3 and len(transport.calls) == 3


def test_voice_object_exposes_category_labels_and_preview_url():
    transport = _json_transport([
        (200, {"voices": [_voice("v1", "Rachel", category="premade",
                                 labels={"gender": "female", "accent": "american"},
                                 preview_url="https://cdn.elevenlabs.io/rachel.mp3")],
              "has_more": False}, {}),
    ])
    voices = ev.fetch_all(SECRET_KEY, transport=transport)
    assert voices[0].category == "premade"
    assert voices[0].labels == {"gender": "female", "accent": "american"}
    assert voices[0].preview_url == "https://cdn.elevenlabs.io/rachel.mp3"


@pytest.mark.parametrize("unsafe", ["javascript:alert(1)", "http://insecure.example/x.mp3",
                                    "data:audio/mp3;base64,AAAA", "not-a-url-at-all", ""])
def test_unsafe_or_non_https_preview_urls_are_stripped_not_forwarded(unsafe):
    transport = _json_transport([(200, {"voices": [_voice("v1", "Rachel", preview_url=unsafe)],
                                        "has_more": False}, {})])
    voices = ev.fetch_all(SECRET_KEY, transport=transport)
    assert voices[0].preview_url == ""


def test_https_preview_url_is_kept_as_is():
    transport = _json_transport([(200, {"voices": [_voice("v1", "Rachel",
                                                           preview_url="https://cdn.elevenlabs.io/a.mp3")],
                                        "has_more": False}, {})])
    voices = ev.fetch_all(SECRET_KEY, transport=transport)
    assert voices[0].preview_url == "https://cdn.elevenlabs.io/a.mp3"


def test_incomplete_voice_entries_are_skipped_not_fatal():
    """Une voix sans voice_id ou sans name (donnée incomplète) est ignorée, jamais une erreur fatale."""
    transport = _json_transport([
        (200, {"voices": [{"voice_id": "v1"}, {"name": "Sans identifiant"}, _voice("v2", "Complète")],
              "has_more": False}, {}),
    ])
    voices = ev.fetch_all(SECRET_KEY, transport=transport)
    assert [v.name for v in voices] == ["Complète"]


# -- catalogue vide ---------------------------------------------------------------------------------------------
def test_empty_catalog_returns_an_empty_list_not_an_error():
    transport = _json_transport([(200, {"voices": [], "has_more": False}, {})])
    assert ev.fetch_all(SECRET_KEY, transport=transport) == []


# -- recherche par nom -------------------------------------------------------------------------------------------
def test_search_voices_filters_by_name_case_insensitively():
    voices = [ev.VoiceInfo("v1", "Rachel"), ev.VoiceInfo("v2", "Adam"), ev.VoiceInfo("v3", "Bella")]
    assert [v.name for v in ev.search_voices(voices, "rach")] == ["Rachel"]
    assert [v.name for v in ev.search_voices(voices, "ADAM")] == ["Adam"]
    assert ev.search_voices(voices, "") == voices
    assert ev.search_voices(voices, "zzz") == []


# -- timeout, 401, 403, 429, erreur serveur, erreur réseau ---------------------------------------------------------
def test_401_raises_auth_error_with_a_safe_message():
    transport = _json_transport([(401, {}, {})])
    with pytest.raises(ev.VoiceCatalogAuthError) as error:
        ev.fetch_all(SECRET_KEY, transport=transport)
    assert SECRET_KEY not in str(error.value)


def test_403_raises_auth_error_distinct_message():
    transport = _json_transport([(403, {}, {})])
    with pytest.raises(ev.VoiceCatalogAuthError) as error:
        ev.fetch_all(SECRET_KEY, transport=transport)
    assert "refusé" in str(error.value).lower()


def test_429_raises_rate_limited_with_retry_after_from_response_header():
    transport = _json_transport([(429, {}, {"Retry-After": "7"})])
    with pytest.raises(ev.VoiceCatalogRateLimited) as error:
        ev.fetch_all(SECRET_KEY, transport=transport)
    assert error.value.retry_after == 7.0


def test_429_without_retry_after_header_is_still_handled():
    transport = _json_transport([(429, {}, {})])
    with pytest.raises(ev.VoiceCatalogRateLimited) as error:
        ev.fetch_all(SECRET_KEY, transport=transport)
    assert error.value.retry_after is None


def test_server_error_raises_unavailable():
    transport = _json_transport([(503, {}, {})])
    with pytest.raises(ev.VoiceCatalogUnavailable):
        ev.fetch_all(SECRET_KEY, transport=transport)


def test_unexpected_status_raises_unavailable():
    transport = _json_transport([(418, {}, {})])
    with pytest.raises(ev.VoiceCatalogUnavailable):
        ev.fetch_all(SECRET_KEY, transport=transport)


def test_malformed_json_response_raises_unavailable_not_a_crash():
    def transport(url, params, headers, timeout):
        return 200, b"not json{{{", {}
    with pytest.raises(ev.VoiceCatalogUnavailable):
        ev.fetch_all(SECRET_KEY, transport=transport)


def test_timeout_raises_unavailable():
    def transport(url, params, headers, timeout):
        raise TimeoutError()
    with pytest.raises(ev.VoiceCatalogUnavailable):
        ev.fetch_all(SECRET_KEY, transport=transport)


def test_network_error_raises_unavailable():
    def transport(url, params, headers, timeout):
        raise urllib.error.URLError("no route to host")
    with pytest.raises(ev.VoiceCatalogUnavailable):
        ev.fetch_all(SECRET_KEY, transport=transport)


def test_second_page_failure_does_not_return_a_partial_catalog():
    """Un échec en cours de pagination ne renvoie jamais une liste partielle silencieuse."""
    calls = {"n": 0}

    def transport(url, params, headers, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            return 200, json.dumps({"voices": [_voice("v1", "Rachel")], "has_more": True,
                                    "next_page_token": "p2"}).encode(), {}
        return 500, b"{}", {}

    with pytest.raises(ev.VoiceCatalogUnavailable):
        ev.fetch_all(SECRET_KEY, transport=transport)


# -- résolution : LIT UNIQUEMENT le rapport assaini, jamais config.toml, jamais la clé ------------------------------
def test_resolve_catalog_reads_a_valid_report(tmp_path):
    now = ev._now_iso()
    report = tmp_path / "voices.json"
    report.write_text(json.dumps({"version": 1, "generated_at": now,
                                  "voices": [_voice("v1", "Rachel")]}), encoding="utf-8")
    result = ev.resolve_catalog(report_path=report)
    assert result.source == "report" and result.available and not result.stale and not result.empty
    assert [v.name for v in result.voices] == ["Rachel"]
    assert result.generated_at == now


def test_resolve_catalog_with_no_report_gives_a_clear_manual_fallback_message(tmp_path):
    result = ev.resolve_catalog(report_path=tmp_path / "absent.json")
    assert not result.available and result.error and "manuelle" in result.error.lower()


def test_resolve_catalog_report_with_wrong_version_is_treated_as_invalid(tmp_path):
    report = tmp_path / "voices.json"
    report.write_text(json.dumps({"version": 99, "voices": []}), encoding="utf-8")
    result = ev.resolve_catalog(report_path=report)
    assert not result.available and result.error


def test_resolve_catalog_report_with_malformed_json_is_treated_as_invalid(tmp_path):
    report = tmp_path / "voices.json"
    report.write_text("not json at all {{{", encoding="utf-8")
    result = ev.resolve_catalog(report_path=report)
    assert not result.available and result.error


def test_resolve_catalog_report_with_voices_not_a_list_is_treated_as_invalid(tmp_path):
    report = tmp_path / "voices.json"
    report.write_text(json.dumps({"version": 1, "voices": "not-a-list"}), encoding="utf-8")
    result = ev.resolve_catalog(report_path=report)
    assert not result.available and result.error


def test_resolve_catalog_empty_report_is_distinct_from_a_missing_report(tmp_path):
    """Un compte sans aucune voix n'est pas une erreur : message différent d'un rapport absent/cassé."""
    report = tmp_path / "voices.json"
    report.write_text(json.dumps({"version": 1, "generated_at": "2026-01-01T00:00:00+00:00", "voices": []}),
                      encoding="utf-8")
    result = ev.resolve_catalog(report_path=report)
    assert result.empty is True and not result.available
    assert result.error is None  # PAS un message d'erreur : un catalogue valide, juste vide


def test_resolve_catalog_marks_an_old_report_as_stale_but_still_shows_it(tmp_path):
    import datetime as dt

    old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=40)).isoformat(timespec="seconds")
    report = tmp_path / "voices.json"
    report.write_text(json.dumps({"version": 1, "generated_at": old, "voices": [_voice("v1", "Rachel")]}),
                      encoding="utf-8")
    result = ev.resolve_catalog(report_path=report)
    assert result.available and result.stale is True


def test_resolve_catalog_recent_report_is_not_stale(tmp_path):
    report = tmp_path / "voices.json"
    report.write_text(json.dumps({"version": 1, "generated_at": ev._now_iso(),
                                  "voices": [_voice("v1", "Rachel")]}), encoding="utf-8")
    result = ev.resolve_catalog(report_path=report)
    assert result.stale is False


def test_resolve_catalog_with_missing_generated_at_is_treated_as_stale(tmp_path):
    report = tmp_path / "voices.json"
    report.write_text(json.dumps({"version": 1, "voices": [_voice("v1", "Rachel")]}), encoding="utf-8")
    result = ev.resolve_catalog(report_path=report)
    assert result.stale is True  # date absente : jamais présumée fraîche


def test_resolve_catalog_never_opens_config_toml_even_if_present_next_to_the_report(tmp_path, monkeypatch):
    """Le conteneur Lody ne doit JAMAIS tenter de lire config.toml pour cette fonctionnalité — même si un
    fichier de ce nom existe par ailleurs. On le prouve en faisant échouer toute tentative d'ouverture."""
    (tmp_path / "config.toml").write_text(f'[elevenlabs]\napi_key = "{SECRET_KEY}"\n', encoding="utf-8")
    report = tmp_path / "voices.json"
    report.write_text(json.dumps({"version": 1, "generated_at": ev._now_iso(),
                                  "voices": [_voice("v1", "Rachel")]}), encoding="utf-8")

    real_open = Path.open

    def guarded_open(self, *args, **kwargs):
        if self.name == "config.toml":
            raise AssertionError("resolve_catalog a ouvert config.toml : violation de l'architecture #55")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    result = ev.resolve_catalog(report_path=report)
    assert result.available  # le rapport, lui, est bien lu normalement


def test_resolve_catalog_function_source_never_references_config_path_or_tomllib():
    """Preuve statique complémentaire (même style que test_characters.py) : le CODE de la fonction (hors
    docstring, qui peut légitimement expliquer ce qu'elle ne fait pas) ne contient aucun appel à tomllib, aucune
    ouverture de config.toml, aucune variable de clé — pas seulement « ça marche » avec les données du test,
    mais « ça ne PEUT PAS » structurellement."""
    full_source = inspect.getsource(ev.resolve_catalog)
    body_only = full_source.split('"""', 2)[-1]  # retire le docstring (délimité par les deux premiers """)
    for forbidden in ("config_path", "tomllib", "api_key", ".open(", "elevenlabs.io"):
        assert forbidden not in body_only, forbidden


def test_resolve_catalog_reload_reflects_an_atomic_file_replacement(tmp_path):
    """Le fichier peut être remplacé atomiquement (os.replace, comme write_report) par le script hôte à tout
    moment : le prochain appel doit refléter le nouveau contenu sans redémarrage ni cache à invalider."""
    report = tmp_path / "voices.json"
    report.write_text(json.dumps({"version": 1, "generated_at": ev._now_iso(),
                                  "voices": [_voice("v1", "Ancienne Voix")]}), encoding="utf-8")
    before = ev.resolve_catalog(report_path=report)
    assert [v.name for v in before.voices] == ["Ancienne Voix"]

    replacement = tmp_path / "voices.json.tmp"
    replacement.write_text(json.dumps({"version": 1, "generated_at": ev._now_iso(),
                                       "voices": [_voice("v2", "Nouvelle Voix")]}), encoding="utf-8")
    os.replace(replacement, report)  # même mécanisme que ev._write_atomic

    after = ev.resolve_catalog(report_path=report)
    assert [v.name for v in after.voices] == ["Nouvelle Voix"]


# -- génération du rapport côté hôte (aucun secret dans le fichier écrit) -------------------------------------------
def test_build_report_never_writes_the_key_only_public_voice_fields(tmp_path, monkeypatch):
    config = tmp_path / "config.toml"
    config.write_text(f'[elevenlabs]\napi_key = "{SECRET_KEY}"\n', encoding="utf-8")

    def fake_fetch_all(api_key, **kwargs):
        assert api_key == SECRET_KEY
        return [ev.VoiceInfo("v1", "Rachel", category="premade", labels={"gender": "female"},
                             preview_url="https://cdn/rachel.mp3")]

    monkeypatch.setattr(ev, "fetch_all", fake_fetch_all)
    report = ev.build_report(config)
    dumped = json.dumps(report, ensure_ascii=False)
    assert SECRET_KEY not in dumped
    assert report["voices"] == [{"voice_id": "v1", "name": "Rachel", "category": "premade",
                                 "labels": {"gender": "female"}, "preview_url": "https://cdn/rachel.mp3"}]


def test_build_report_with_no_key_configured_returns_empty_voices(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('[app]\nllm_provider = "openai"\n', encoding="utf-8")
    report = ev.build_report(config)
    assert report["voices"] == []


def test_write_report_produces_a_world_readable_file_containing_no_secret(tmp_path, monkeypatch):
    config = tmp_path / "config.toml"
    config.write_text(f'[elevenlabs]\napi_key = "{SECRET_KEY}"\n', encoding="utf-8")
    out = tmp_path / "out" / "elevenlabs-voices.json"
    monkeypatch.setattr(ev, "fetch_all", lambda api_key, **kwargs: [ev.VoiceInfo("v1", "Rachel")])
    ev.write_report(config, out)
    content = out.read_text(encoding="utf-8")
    assert SECRET_KEY not in content
    data = json.loads(content)
    assert data["voices"] == [{"voice_id": "v1", "name": "Rachel", "category": "", "labels": {}, "preview_url": ""}]


def test_write_report_permissions_allow_read_only_not_write_by_group_or_others(tmp_path, monkeypatch):
    """Requirement #55 : « permissions permettant uniquement la lecture du rapport par Lody »."""
    import stat

    config = tmp_path / "config.toml"
    config.write_text(f'[elevenlabs]\napi_key = "{SECRET_KEY}"\n', encoding="utf-8")
    out = tmp_path / "elevenlabs-voices.json"
    monkeypatch.setattr(ev, "fetch_all", lambda api_key, **kwargs: [ev.VoiceInfo("v1", "Rachel")])
    ev.write_report(config, out)
    mode = stat.S_IMODE(out.stat().st_mode)
    assert mode == 0o644
    assert not (mode & stat.S_IWGRP) and not (mode & stat.S_IWOTH)  # jamais inscriptible par le groupe/le reste


def test_write_report_strips_an_unsafe_preview_url(tmp_path, monkeypatch):
    config = tmp_path / "config.toml"
    config.write_text(f'[elevenlabs]\napi_key = "{SECRET_KEY}"\n', encoding="utf-8")
    out = tmp_path / "elevenlabs-voices.json"
    monkeypatch.setattr(ev, "fetch_all",
                        lambda api_key, **kwargs: [ev.VoiceInfo("v1", "Rachel", preview_url="javascript:alert(1)")])
    ev.write_report(config, out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["voices"][0]["preview_url"] == ""


def test_main_cli_writes_report_and_prints_count(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config.toml"
    config.write_text(f'[elevenlabs]\napi_key = "{SECRET_KEY}"\n', encoding="utf-8")
    out = tmp_path / "elevenlabs-voices.json"
    monkeypatch.setattr(ev, "fetch_all", lambda api_key, **kwargs: [ev.VoiceInfo("v1", "Rachel")])
    exit_code = ev.main(["--config", str(config), "--out", str(out)])
    assert exit_code == 0
    printed = capsys.readouterr().out
    assert SECRET_KEY not in printed
    assert "1 voix" in printed


def test_main_cli_handles_an_unreadable_config_gracefully(tmp_path, capsys):
    exit_code = ev.main(["--config", str(tmp_path / "absent.toml"), "--out", str(tmp_path / "out.json")])
    assert exit_code == 1


# -- transport réel (urllib) : jamais la clé dans l'URL, toujours dans l'en-tête -------------------------------------
def test_urllib_transport_puts_the_key_only_in_the_header_never_in_the_url():
    """Vérifie la CONSTRUCTION de la requête (sans réseau) : ``api_key`` ne doit apparaître dans aucune URL."""
    import urllib.request as real_urllib_request

    captured = {}
    original_urlopen = real_urllib_request.urlopen

    class _FakeResponse:
        status = 200
        headers = {}

        def read(self, _n):
            return b'{"voices": [], "has_more": false}'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout=None):
        captured["full_url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        return _FakeResponse()

    real_urllib_request.urlopen = fake_urlopen
    try:
        ev.fetch_page(SECRET_KEY, transport=ev.urllib_transport)
    finally:
        real_urllib_request.urlopen = original_urlopen
    assert SECRET_KEY not in captured["full_url"]
    assert captured["headers"].get("Xi-api-key") == SECRET_KEY or captured["headers"].get("X-api-key") == SECRET_KEY \
        or SECRET_KEY in str(captured["headers"])  # l'en-tête HTTP porte bien la clé (jamais l'URL)


# -- rapport TOML : garantit que ``tomllib`` seul suffit à parser un config.toml minimal --------------------------
def test_config_toml_without_elevenlabs_section_parses_fine(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('[app]\nllm_provider = "openai"\n', encoding="utf-8")
    with config.open("rb") as handle:
        raw = tomllib.load(handle)
    assert raw.get("elevenlabs") is None  # confirme l'hypothèse utilisée par resolve_catalog/build_report
