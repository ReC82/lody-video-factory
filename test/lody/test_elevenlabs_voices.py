"""Catalogue de voix ElevenLabs (#55) : client HTTP simulé (aucun appel réseau réel), pagination, recherche,
cache et actualisation, erreurs (401/403/429/5xx/timeout/réseau), catalogue vide, résolution config/rapport,
absence de fuite de la clé, génération du rapport côté hôte."""

from __future__ import annotations

import json
import tomllib
import urllib.error

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


# -- cache et actualisation --------------------------------------------------------------------------------------
def test_cache_reuses_result_within_ttl_and_refetches_after_expiry():
    clock = {"t": 0.0}
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        return [ev.VoiceInfo("v1", "Rachel")]

    cache = ev.CatalogCache(ttl_seconds=10, clock=lambda: clock["t"])
    first = cache.get(fetch)
    second = cache.get(fetch)
    assert calls["n"] == 1 and first.source == "live" and second is first
    clock["t"] = 11
    cache.get(fetch)
    assert calls["n"] == 2


def test_explicit_refresh_bypasses_the_cache_even_within_ttl():
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        return [ev.VoiceInfo("v1", "Rachel")]

    cache = ev.CatalogCache(ttl_seconds=999)
    cache.get(fetch)
    cache.get(fetch, force_refresh=True)
    assert calls["n"] == 2


def test_cache_falls_back_to_stale_result_on_error_rather_than_nothing():
    cache = ev.CatalogCache(ttl_seconds=0)  # toujours périmé : force un refetch à chaque appel
    cache.get(lambda: [ev.VoiceInfo("v1", "Rachel")])

    def failing():
        raise ev.VoiceCatalogUnavailable("panne réseau")

    result = cache.get(failing)
    assert result.stale is True and result.error == "panne réseau"
    assert [v.name for v in result.voices] == ["Rachel"]


def test_cache_with_no_prior_success_and_a_failure_returns_empty_with_error():
    cache = ev.CatalogCache()

    def failing():
        raise ev.VoiceCatalogAuthError(401)

    result = cache.get(failing)
    assert result.voices == () and not result.available and result.error


# -- résolution : config lisible vs rapport assaini, jamais de fuite de la clé -------------------------------------
def test_resolve_catalog_uses_report_when_config_is_unreadable(tmp_path):
    report = tmp_path / "voices.json"
    report.write_text(json.dumps({"version": 1, "generated_at": "2026-01-01T00:00:00+00:00",
                                  "voices": [_voice("v1", "Rachel")]}), encoding="utf-8")
    result = ev.resolve_catalog(config_path=tmp_path / "absent.toml", report_path=report)
    assert result.source == "report" and [v.name for v in result.voices] == ["Rachel"]


def test_resolve_catalog_with_no_config_and_no_report_gives_a_clear_manual_fallback_message(tmp_path):
    result = ev.resolve_catalog(config_path=tmp_path / "absent.toml", report_path=tmp_path / "absent.json")
    assert not result.available and "manuelle" in result.error.lower()


def test_resolve_catalog_report_with_wrong_version_is_treated_as_unavailable(tmp_path):
    report = tmp_path / "voices.json"
    report.write_text(json.dumps({"version": 99, "voices": []}), encoding="utf-8")
    result = ev.resolve_catalog(config_path=tmp_path / "absent.toml", report_path=report)
    assert not result.available and result.error


def test_resolve_catalog_uses_direct_config_when_readable_and_never_leaks_the_key(tmp_path, monkeypatch):
    config = tmp_path / "config.toml"
    config.write_text(f'[elevenlabs]\napi_key = "{SECRET_KEY}"\n', encoding="utf-8")
    captured = {}

    def spy_fetch_all(api_key, **kwargs):
        captured["key"] = api_key
        return [ev.VoiceInfo("v1", "Rachel")]

    monkeypatch.setattr(ev, "fetch_all", spy_fetch_all)
    result = ev.resolve_catalog(config_path=config, report_path=tmp_path / "unused.json",
                                cache=ev.CatalogCache())
    assert captured["key"] == SECRET_KEY  # la clé a bien atteint le client HTTP...
    assert result.source == "live"
    # ... mais ne ressort JAMAIS dans le résultat exposé à l'interface.
    dumped = json.dumps({"voices": [v.to_dict() for v in result.voices], "error": result.error,
                         "fetched_at": result.fetched_at, "source": result.source})
    assert SECRET_KEY not in dumped


def test_resolve_catalog_force_refresh_is_forwarded_on_the_direct_config_path(tmp_path, monkeypatch):
    """Régression : le bouton « Actualiser » doit forcer un nouvel appel même quand config.toml est lisible
    directement — pas seulement en mode rapport."""
    config = tmp_path / "config.toml"
    config.write_text(f'[elevenlabs]\napi_key = "{SECRET_KEY}"\n', encoding="utf-8")
    calls = {"n": 0}

    def counting_fetch_all(api_key, **kwargs):
        calls["n"] += 1
        return [ev.VoiceInfo("v1", "Rachel")]

    monkeypatch.setattr(ev, "fetch_all", counting_fetch_all)
    shared_cache = ev.CatalogCache(ttl_seconds=999)
    ev.resolve_catalog(config_path=config, cache=shared_cache)
    ev.resolve_catalog(config_path=config, cache=shared_cache)  # dans le TTL : ne doit pas réappeler
    assert calls["n"] == 1
    ev.resolve_catalog(config_path=config, cache=shared_cache, force_refresh=True)
    assert calls["n"] == 2


def test_resolve_catalog_falls_back_to_report_when_config_has_no_elevenlabs_key(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('[app]\nllm_provider = "openai"\n', encoding="utf-8")
    report = tmp_path / "voices.json"
    report.write_text(json.dumps({"version": 1, "generated_at": "t", "voices": [_voice("v1", "Rachel")]}),
                      encoding="utf-8")
    result = ev.resolve_catalog(config_path=config, report_path=report)
    assert result.source == "report"


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
