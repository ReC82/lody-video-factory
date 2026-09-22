"""Connecteur du moteur historique : contrat HTTP, statuts, erreurs, chemins. Aucun fournisseur."""

from __future__ import annotations

import json
import socket
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from lody.generation import mpt_connector as mpt
from lody.generation.engine_facts import EngineFacts, resolve
from lody.generation.models import (
    ErrorKind,
    ExternalTask,
    ProviderError,
    RemoteState,
    VoiceSpec,
)
from lody.generation.safety import classify_engine_error, resolve_within, sanitize
from lody.generation.service import build_request
from lody.projects import ProjectRepository
from lody.seeds import SEED_PROJECTS

TASK = "6f1c2d3e-aaaa-bbbb-cccc-1234567890ab"


class Recorder:
    """Transport factice : renvoie des réponses préparées et garde chaque requête."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[tuple[str, str, dict | None, dict]] = []

    def __call__(self, method, url, body, headers, timeout):
        self.requests.append((method, url, json.loads(body) if body else None, headers))
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        status, payload = item
        return status, payload if isinstance(payload, bytes) else json.dumps(payload).encode()


def _connector(tmp_path, *responses, config: str = "", **kwargs):
    config_path = tmp_path / "config.toml"
    config_path.write_text(config, encoding="utf-8")
    transport = Recorder(*responses)
    (tmp_path / "storage").mkdir(exist_ok=True)
    connector = mpt.MoneyPrinterTurboConnector("http://engine:8080", tmp_path / "storage", config_path,
                                               report_path=tmp_path / "no-report.json", transport=transport, **kwargs)
    return connector, transport


def _crypto_request(**changes):
    project = next(p for p in _repo_projects() if p.name == "LodyCrypto")
    return build_request(project, "Explique la blockchain aux débutants").with_updates(**changes)


_PROJECTS = []


def _repo_projects():
    if not _PROJECTS:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as folder:
            repo = ProjectRepository(Path(folder) / "p.sqlite3")
            repo.seed_defaults(SEED_PROJECTS)
            _PROJECTS.extend(repo.list_projects())
    return _PROJECTS


CONFIG = """
[app]
llm_provider = "openai"
openai_api_key = "FAKE-CONFIG-VALUE-NOT-A-KEY"
openai_image_base_url = "https://api.openai.com/v1"
openai_image_model = "gpt-image"
openai_image_api_keys = ["FAKE-IMAGE-VALUE-NOT-A-KEY"]
[elevenlabs]
api_key = "FAKE-ELEVEN-VALUE-NOT-A-KEY"
model_id = "eleven_multilingual_v2"
[ui]
font_name = "MicrosoftYaHeiBold.ttc"
font_size = 58
stroke_width = 1.8
subtitle_background_enabled = true
subtitle_background_color = "#000000"
"""


# -- payload ---------------------------------------------------------------------------
def test_lodycrypto_payload_carries_the_project_parameters(tmp_path):
    request = _crypto_request(script="Une phrase. Une autre phrase.", visual_prompts=("scène un", "scène deux"))
    payload = mpt.build_payload(request, resolve(_write(tmp_path, CONFIG), tmp_path / 'none.json'))
    assert payload["video_language"] == "fr-FR" and payload["video_aspect"] == "9:16"
    assert payload["voice_name"] == "elevenlabs:jGpnMdbhtKgQbVrYezOx:Kev - Young, Dynamic and Bright"
    assert payload["subtitle_display_mode"] == "sentence" and payload["subtitle_enabled"] is True
    assert payload["video_source"] == "openai_image"
    assert payload["video_terms"] == ["scène un", "scène deux"] and payload["video_script"].startswith("Une phrase")
    assert payload["video_concat_mode"] == "sequential" and payload["match_materials_to_script"] is True
    assert payload["bgm_type"] == "elevenlabs" and payload["bgm_volume"] > 0
    # la police CJK de la config (apostrophe ’ pleine largeur) n'est PAS transmise pour un projet français
    assert payload["font_name"] == "BeVietnamPro-Bold.ttf" and payload["font_size"] == 58
    assert payload["text_background_color"] == "#000000"
    # aucune valeur de config.toml (clé ou autre) ne sort du connecteur
    assert "FAKE-" not in json.dumps(payload)


def _write(tmp_path, content):
    path = tmp_path / "cfg.toml"
    path.write_text(content, encoding="utf-8")
    return path


def test_script_prompt_respects_the_engine_limit_and_keeps_the_rules(tmp_path):
    request = _crypto_request(instructions="Ne fais aucune promesse de gain. " * 200)
    prompt = mpt.script_prompt(request)
    assert len(prompt) <= 2000
    assert "45 à 60 secondes" in prompt and "Structure, dans cet ordre" in prompt and "Accroche" in prompt
    assert "Ne fais aucune promesse de gain" in prompt


def test_subtitle_font_never_lets_a_cjk_apostrophe_font_reach_a_latin_video(tmp_path):
    facts = resolve(_write(tmp_path, CONFIG), tmp_path / "none.json")
    assert facts.ui["font_name"] == "MicrosoftYaHeiBold.ttc"                     # la config demande une police CJK…
    request = _crypto_request(script="Une phrase.", visual_prompts=("a",))
    assert mpt.build_payload(request, facts)["font_name"] == "BeVietnamPro-Bold.ttf"   # …le connecteur ne la transmet pas
    latin = resolve(_write(tmp_path, CONFIG.replace("MicrosoftYaHeiBold.ttc", "BeVietnamPro-Medium.ttf")), tmp_path / "none.json")
    assert mpt.build_payload(request, latin)["font_name"] == "BeVietnamPro-Medium.ttf"  # une bonne police reste choisie
    chinese = mpt.build_payload(request.with_updates(language="zh-CN"), facts)
    assert chinese["font_name"] == "MicrosoftYaHeiBold.ttc"                        # le chinois garde sa police
    assert mpt.build_payload(request, EngineFacts())["font_name"] == "BeVietnamPro-Bold.ttf"  # configuration inconnue : repli latin


def test_effective_params_record_the_font_actually_sent(tmp_path):
    connector, _ = _connector(tmp_path, config=CONFIG)
    assert connector.describe_params(_crypto_request())["font_name"] == "BeVietnamPro-Bold.ttf"


def test_music_and_voice_variants():
    flags = EngineFacts()
    edge = _crypto_request(voice=VoiceSpec("edge", "", "fr-FR-DeniseNeural", ""), music_provider="none")
    payload = mpt.build_payload(edge, flags)
    assert payload["voice_name"] == "fr-FR-DeniseNeural" and payload["bgm_type"] == "" and payload["bgm_volume"] == 0.0
    assert mpt.build_payload(_crypto_request(music_provider="library"), flags)["bgm_type"] == "random"


# -- création, statuts ---------------------------------------------------------------------
def test_submit_posts_to_the_real_endpoint_and_records_the_task_id(tmp_path):
    connector, transport = _connector(tmp_path, (200, {"status": 200, "data": {"task_id": TASK, "request_id": "r"}}), config=CONFIG)
    task = connector.submit(_crypto_request(script="Une phrase.", visual_prompts=("a",)), "key-1")
    assert task == ExternalTask(mpt.PROVIDER_ID, TASK)
    method, url, body, headers = transport.requests[0]
    assert (method, url) == ("POST", "http://engine:8080/api/v1/videos")
    assert body["video_subject"] and "x-api-key" not in headers


def test_submit_rejects_a_response_without_a_usable_task_id(tmp_path):
    connector, _ = _connector(tmp_path, (200, {"status": 200, "data": {"task_id": "../../etc"}}), config=CONFIG)
    with pytest.raises(ProviderError) as error:
        connector.submit(_crypto_request(script="x", visual_prompts=("a",)), "k")
    assert error.value.kind is ErrorKind.INVALID_RESPONSE


def test_engine_api_key_is_sent_only_as_header_when_configured(tmp_path):
    connector, transport = _connector(tmp_path, (200, {"status": 200, "data": {"task_id": TASK}}), config=CONFIG,
                                      api_key="ENGINE-HEADER-VALUE")
    connector.submit(_crypto_request(script="x", visual_prompts=("a",)), "k")
    assert transport.requests[0][3]["x-api-key"] == "ENGINE-HEADER-VALUE"
    assert "ENGINE-HEADER-VALUE" not in json.dumps(transport.requests[0][2])


@pytest.mark.parametrize("progress,state,step", [
    (0, RemoteState.QUEUED, "En attente dans la file du moteur"),
    (5, RemoteState.RUNNING, "Préparation"),
    (25, RemoteState.RUNNING, "Génération de la voix"),
    (35, RemoteState.RUNNING, "Création des sous-titres"),
    (45, RemoteState.RUNNING, "Génération des images"),
    (70, RemoteState.RUNNING, "Montage de la vidéo"),
])
def test_engine_progress_maps_to_internal_states_and_real_steps(tmp_path, progress, state, step):
    connector, _ = _connector(tmp_path, (200, {"status": 200, "data": {"task_id": TASK, "state": 4, "progress": progress}}))
    snapshot = connector.poll(ExternalTask(mpt.PROVIDER_ID, TASK))
    assert snapshot.state is state and snapshot.progress == progress and snapshot.step == step


def test_completed_task_returns_video_duration_and_validated_assets(tmp_path):
    folder = tmp_path / "storage" / "tasks" / TASK
    folder.mkdir(parents=True)
    (folder / "final-1.mp4").write_bytes(b"v")
    (folder / "audio.mp3").write_bytes(b"a")
    data = {"task_id": TASK, "state": 1, "progress": 100, "videos": [f"/tasks/{TASK}/final-1.mp4"],
            "script": "Le script.", "audio_duration": 52.4, "audio_file": f"/MoneyPrinterTurbo/storage/tasks/{TASK}/audio.mp3",
            "materials": [f"/MoneyPrinterTurbo/storage/tasks/{TASK}/../other/x.mp4", "/etc/passwd"],
            "warnings": ["elevenlabs_bgm_failed"]}
    connector, _ = _connector(tmp_path, (200, {"status": 200, "data": data}))
    snapshot = connector.poll(ExternalTask(mpt.PROVIDER_ID, TASK))
    assert snapshot.state is RemoteState.DONE and snapshot.result.video_ref == f"tasks/{TASK}/final-1.mp4"
    assert snapshot.result.duration_seconds == 52.4 and snapshot.result.warnings == ("elevenlabs_bgm_failed",)
    assert [asset["kind"] for asset in snapshot.result.assets] == ["audio"]  # traversée et chemin étranger écartés


def test_done_without_a_video_is_an_error(tmp_path):
    connector, _ = _connector(tmp_path, (200, {"status": 200, "data": {"task_id": TASK, "state": 1, "videos": []}}))
    with pytest.raises(ProviderError) as error:
        connector.poll(ExternalTask(mpt.PROVIDER_ID, TASK))
    assert error.value.kind is ErrorKind.VIDEO_MISSING


def test_unknown_task_is_reported_not_raised(tmp_path):
    connector, _ = _connector(tmp_path, (404, {"status": 404, "message": "x: task not found"}))
    assert connector.poll(ExternalTask(mpt.PROVIDER_ID, TASK)).state is RemoteState.UNKNOWN


# -- erreurs -----------------------------------------------------------------------------------
@pytest.mark.parametrize("raw,kind", [
    ("openai: api_key is not set, please set it in the config.toml file.", ErrorKind.KEY_MISSING),
    ("Volcano Engine Seedance requires an Ark API key", ErrorKind.KEY_MISSING),
    ("You exceeded your current quota, please check your plan and billing details", ErrorKind.QUOTA),
    ("Error code: 429 - rate limit reached", ErrorKind.QUOTA),
    ("Incorrect API key provided: sk-abcdefghijklmnopqrstuvwxyz123456", ErrorKind.AUTH),
    ("Request timed out.", ErrorKind.TIMEOUT),
    ("something exploded", ErrorKind.PROVIDER),
])
def test_engine_errors_are_classified_and_never_echoed(raw, kind):
    found, message = classify_engine_error("materials", raw)
    assert found is kind
    assert "sk-" not in message and raw not in message and "images" in message


def test_sk_proj_key_format_observed_in_production_is_fully_masked():
    """Non-régression : le format réel rencontré (`sk-proj-...`, clés OpenAI par projet) diffère du format
    `sk-` classique testé ci-dessus (préfixe supplémentaire, tirets). Valeur ici fictive, jamais la vraie."""
    raw = "Incorrect API key provided: sk-proj-HCsAbndKvW3xzFAKE1234567890abcdefFAKE. You can find your API key at..."
    kind, message = classify_engine_error("script", raw)
    assert kind is ErrorKind.AUTH
    assert "sk-proj" not in message and "HCsAbndKvW3xzFAKE" not in message
    assert message == "Le fournisseur refuse la clé d’API configurée. (échec pendant l’écriture du script)."


def test_failed_task_keeps_the_stage_and_hides_the_raw_error(tmp_path):
    data = {"task_id": TASK, "state": -1, "progress": 40, "failed_stage": "materials",
            "error": "Incorrect API key provided: sk-abcdefghijklmnopqrstuvwxyz123456"}
    connector, _ = _connector(tmp_path, (200, {"status": 200, "data": data}))
    snapshot = connector.poll(ExternalTask(mpt.PROVIDER_ID, TASK))
    assert snapshot.state is RemoteState.FAILED and snapshot.error.kind is ErrorKind.AUTH
    assert "sk-" not in snapshot.error.message and snapshot.progress == 40


@pytest.mark.parametrize("failure,kind", [
    (TimeoutError(), ErrorKind.TIMEOUT),
    (urllib.error.URLError(socket.timeout()), ErrorKind.TIMEOUT),
    (urllib.error.URLError(ConnectionRefusedError()), ErrorKind.UNAVAILABLE),
    (ConnectionResetError(), ErrorKind.UNAVAILABLE),
])
def test_network_failures_are_typed(tmp_path, failure, kind):
    connector, _ = _connector(tmp_path, failure)
    with pytest.raises(ProviderError) as error:
        connector.poll(ExternalTask(mpt.PROVIDER_ID, TASK))
    assert error.value.kind is kind


@pytest.mark.parametrize("response,kind", [
    ((200, b"<html>not json</html>"), ErrorKind.INVALID_RESPONSE),
    ((200, {"status": 200, "data": {"no_state": 1}}), ErrorKind.INVALID_RESPONSE),
    ((200, {"status": 200, "data": {"state": 99}}), ErrorKind.INVALID_RESPONSE),
    ((401, {"status": 401, "message": "invalid API key"}), ErrorKind.AUTH),
    ((429, {"status": 429, "message": "queue full"}), ErrorKind.QUEUE_FULL),
    ((400, {"status": 400, "message": "field required"}), ErrorKind.REJECTED),
    ((500, b"boom"), ErrorKind.PROVIDER),
])
def test_bad_responses_are_typed(tmp_path, response, kind):
    connector, _ = _connector(tmp_path, response)
    with pytest.raises(ProviderError) as error:
        connector.poll(ExternalTask(mpt.PROVIDER_ID, TASK))
    assert error.value.kind is kind


# -- script ---------------------------------------------------------------------------------------------
def test_write_script_uses_the_scripts_endpoint(tmp_path):
    connector, transport = _connector(tmp_path, (200, {"status": 200, "data": {"video_script": "Voici le script."}}))
    assert connector.write_script(_crypto_request()) == "Voici le script."
    assert transport.requests[0][1].endswith("/api/v1/scripts")
    assert transport.requests[0][2]["paragraph_number"] == 1


@pytest.mark.parametrize("script", ["", "Error: openai: api_key is not set, please set it in the config.toml file."])
def test_write_script_failures_are_errors(tmp_path, script):
    connector, _ = _connector(tmp_path, (200, {"status": 200, "data": {"video_script": script}}))
    with pytest.raises(ProviderError):
        connector.write_script(_crypto_request())


# -- chemins ------------------------------------------------------------------------------------------------
def test_path_validation_only_allows_files_of_the_production(tmp_path):
    root = tmp_path / "storage"
    (root / "tasks" / TASK).mkdir(parents=True)
    (root / "tasks" / "other-task-123456").mkdir(parents=True)
    (root / "tasks" / TASK / "final-1.mp4").write_bytes(b"ok")
    (root / "tasks" / "other-task-123456" / "final-1.mp4").write_bytes(b"x")
    (tmp_path / "secret.mp4").write_bytes(b"s")
    (root / "tasks" / TASK / "link.mp4").symlink_to(tmp_path / "secret.mp4")
    connector, _ = _connector(tmp_path)
    task = ExternalTask(mpt.PROVIDER_ID, TASK)
    assert connector.resolve_asset(task, f"tasks/{TASK}/final-1.mp4").read_bytes() == b"ok"
    for bad in (f"tasks/{TASK}/../other-task-123456/final-1.mp4", "tasks/other-task-123456/final-1.mp4",
                f"/{root}/tasks/{TASK}/final-1.mp4", "../secret.mp4", f"tasks/{TASK}/link.mp4",
                f"tasks/{TASK}\\final-1.mp4", f"tasks/{TASK}/missing.mp4", f"tasks/{TASK}/final-1.txt", "", "tasks/x\x00"):
        with pytest.raises(ValueError):
            connector.resolve_asset(task, bad)
    with pytest.raises(ValueError):
        connector.resolve_asset(ExternalTask(mpt.PROVIDER_ID, "../.."), "tasks/../../x.mp4")
    with pytest.raises(ValueError):
        resolve_within(root, "tasks", prefix="")  # un dossier n'est pas un fichier


def test_recover_finds_a_video_left_on_disk_after_an_engine_restart(tmp_path):
    folder = tmp_path / "storage" / "tasks" / TASK
    folder.mkdir(parents=True)
    connector, _ = _connector(tmp_path)
    assert connector.recover(ExternalTask(mpt.PROVIDER_ID, TASK)) is None
    (folder / "final-1.mp4").write_bytes(b"v")
    recovered = connector.recover(ExternalTask(mpt.PROVIDER_ID, TASK))
    assert recovered.video_ref == f"tasks/{TASK}/final-1.mp4"


def test_sanitize_masks_secrets_and_bounds_length():
    text = sanitize("Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789 sk-abcdefghijklmnopqrstuvwxyz123456 " + "x" * 500)
    assert "abcdefghijklmnopqrstuvwxyz" not in text and "sk-" not in text and len(text) <= 300


# -- vrai HTTP local (127.0.0.1), sans fournisseur -----------------------------------------------------------------
def test_real_http_round_trip_against_a_local_stub(tmp_path):
    seen: list[tuple[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, status, payload):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            seen.append(("GET", self.path))
            if self.path == "/ping":
                self._reply(200, "pong")
            else:
                self._reply(200, {"status": 200, "data": {"task_id": TASK, "state": 4, "progress": 20}})

        def do_POST(self):
            seen.append(("POST", self.path))
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self._reply(200, {"status": 200, "data": {"task_id": TASK}})

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config = _write(tmp_path, CONFIG)
        connector = mpt.MoneyPrinterTurboConnector(f"http://127.0.0.1:{server.server_port}", tmp_path, config)
        assert connector.check_ready(_crypto_request()) == []
        task = connector.submit(_crypto_request(script="x", visual_prompts=("a",)), "k")
        assert connector.poll(task).state is RemoteState.RUNNING
    finally:
        server.shutdown()
    assert seen == [("GET", "/ping"), ("POST", "/api/v1/videos"), ("GET", f"/api/v1/tasks/{TASK}")]


def test_connector_module_is_the_only_place_naming_the_legacy_engine():
    from pathlib import Path

    root = Path(mpt.__file__).resolve().parents[1]
    offenders = [path.name for path in root.rglob("*.py") if "moneyprinter" in path.read_text(encoding="utf-8").lower()
                 and path.name not in {"mpt_connector.py", "components.py"}]
    assert offenders == [], offenders
