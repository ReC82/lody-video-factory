"""Lody #162 : la voix lit ``video_script_spoken``, les sous-titres gardent ``video_script``.

Aucun appel TTS réel : ``voice.tts`` et la mesure de durée sont simulés.
"""

import shutil
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.models.schema import VideoParams  # noqa: E402
from app.services import task as tm  # noqa: E402
from app.utils import utils  # noqa: E402


class _FakeSubMaker:
    cues = []
    subs = ["èss-dé-i"]
    offset = [(0, 10)]


class TestSpokenScript(unittest.TestCase):
    def _run(self, params, script):
        task_id = "test-lody-162-spoken"
        try:
            with (
                patch.object(tm.voice, "tts", return_value=_FakeSubMaker()) as tts,
                patch.object(tm.voice, "get_audio_duration", return_value=6.0),
            ):
                result = tm.generate_audio(task_id, params, script)
        finally:
            shutil.rmtree(utils.task_dir(task_id), ignore_errors=True)
        return tts, result

    def test_voice_reads_spoken_text_and_subtitles_keep_written_text(self):
        params = VideoParams(video_subject="SDI", video_script="Le câble SDI.",
                             video_script_spoken="Le câble èss-dé-i.", voice_name="test-voice")
        tts, (_, duration, sub_maker) = self._run(params, "Le câble SDI.")
        self.assertEqual(tts.call_args.kwargs["text"], "Le câble èss-dé-i.")
        self.assertEqual(duration, 6)
        self.assertNotIsInstance(sub_maker, _FakeSubMaker)
        self.assertIn("SDI", "".join(sub_maker.subs))
        self.assertNotIn("èss", "".join(sub_maker.subs))

    def test_without_spoken_text_behaviour_is_unchanged(self):
        params = VideoParams(video_subject="SDI", video_script="Le câble SDI.", voice_name="test-voice")
        tts, (_, _, sub_maker) = self._run(params, "Le câble SDI.")
        self.assertEqual(tts.call_args.kwargs["text"], "Le câble SDI.")
        self.assertIsInstance(sub_maker, _FakeSubMaker)


if __name__ == "__main__":
    unittest.main()
