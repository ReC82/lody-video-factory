# -*- coding: utf-8 -*-
"""POST /api/v1/image_preview : génération d'une image isolée pour prévisualiser une référence de
personnage avant validation (#92) — jamais de tâche vidéo, jamais persistée dans storage/tasks."""

import base64
import io
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from app import asgi
from app.config import config


def _png_bytes(width=256, height=256):
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (9, 9, 9)).save(buffer, format="PNG")
    return buffer.getvalue()


class TestImagePreviewHTTP(unittest.TestCase):
    def setUp(self):
        self.original_app_config = dict(config.app)
        config.app["api_key"] = ""
        config.app["openai_image_base_url"] = "https://img.example.com/v1"
        config.app["openai_image_model"] = "test-image-model"
        self.client = TestClient(asgi.app)

    def tearDown(self):
        config.app.clear()
        config.app.update(self.original_app_config)

    def test_generates_and_returns_base64_without_creating_a_task(self):
        response_payload = {"data": [{"b64_json": base64.b64encode(_png_bytes()).decode("ascii")}]}
        with patch(
            "app.services.material.requests.post",
            return_value=type("R", (), {"json": lambda self: response_payload, "status_code": 200})(),
        ) as post:
            response = self.client.post(
                "/api/v1/image_preview", json={"prompt": "Eli, jeune héros voxel, yeux bleus"}
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(post.call_args.args[0], "https://img.example.com/v1/images/generations")
        image_base64 = response.json()["data"]["image_base64"]
        decoded = base64.b64decode(image_base64)
        with Image.open(io.BytesIO(decoded)) as image:
            self.assertEqual(image.size, (256, 256))

    def test_missing_provider_configuration_is_rejected_with_400(self):
        config.app.pop("openai_image_base_url", None)
        response = self.client.post("/api/v1/image_preview", json={"prompt": "Eli"})
        self.assertEqual(response.status_code, 400)

    def test_empty_prompt_is_rejected(self):
        response = self.client.post("/api/v1/image_preview", json={"prompt": ""})
        self.assertEqual(response.status_code, 400)

    def test_provider_failure_is_reported_as_502_not_silently_empty(self):
        with patch(
            "app.services.material.requests.post",
            return_value=type("R", (), {"json": lambda self: {"error": "rejected"}, "status_code": 400,
                                        "text": "rejected"})(),
        ):
            response = self.client.post("/api/v1/image_preview", json={"prompt": "Eli"})
        self.assertEqual(response.status_code, 502)


if __name__ == "__main__":
    unittest.main()
