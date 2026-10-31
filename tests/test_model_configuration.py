import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cardio.planner import Planner

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("configure_lemonade", ROOT / "scripts" / "configure_lemonade.py")
CONFIGURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONFIGURE)
MODELS = [
    {"id": "chat-local", "recipe": "llamacpp", "downloaded": True, "extra_secret": "do-not-print"},
    {"id": "npu-local", "recipe": "flm", "downloaded": True},
    {"id": "whisper-local", "recipe": "whispercpp", "downloaded": True},
    {"id": "npu-speech", "recipe": "flm", "downloaded": True, "labels": ["transcription"]},
    {"id": "cloud-route", "recipe": "cloud", "downloaded": True, "cloud_provider": "example"},
    {"id": "cloud-chat", "recipe": "llamacpp", "downloaded": True, "cloud_provider": "example"},
    {"id": "undownloaded", "recipe": "llamacpp", "downloaded": False},
    {"id": "embedding-local", "recipe": "llamacpp", "downloaded": True, "labels": ["embeddings"]},
]


class LocalFixturePlanner(Planner):
    def models(self):
        return {"data": MODELS}


class ModelConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.path = self.root / "config.json"
        self.initial = {"lemonade_url": "http://127.0.0.1:13305/v1", "planner_model": "", "confidence": 0.25}
        self.path.write_text(json.dumps(self.initial), encoding="utf-8")
        self.root_patch = patch.object(CONFIGURE, "ROOT", self.root)
        self.planner_patch = patch.object(CONFIGURE, "Planner", LocalFixturePlanner)
        self.root_patch.start()
        self.planner_patch.start()

    def tearDown(self):
        self.root_patch.stop()
        self.planner_patch.stop()
        self.directory.cleanup()

    def test_listing_filters_cloud_undownloaded_and_wrong_purpose_models(self):
        output = io.StringIO()
        before = self.path.read_bytes()
        with contextlib.redirect_stdout(output):
            self.assertEqual(CONFIGURE.main(["--list"]), 0)
        result = json.loads(output.getvalue())
        self.assertEqual({m["id"] for m in result["chat_models"]}, {"chat-local", "npu-local"})
        self.assertEqual({m["id"] for m in result["speech_models"]}, {"whisper-local", "npu-speech"})
        for model in result["chat_models"] + result["speech_models"]:
            self.assertEqual(set(model), {"id", "recipe", "downloaded"})
        self.assertNotIn("do-not-print", output.getvalue())
        self.assertFalse(result["downloads_performed"])
        self.assertEqual(before, self.path.read_bytes())

    def test_configures_both_purposes_preserving_other_settings(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(CONFIGURE.main(["--model", "npu-local", "--speech-model", "whisper-local"]), 0)
        result = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(result["planner_model"], "npu-local")
        self.assertEqual(result["dictation_model"], "whisper-local")
        self.assertEqual(result["confidence"], self.initial["confidence"])
        self.assertIn("Restart CardioAgent", output.getvalue())
        self.assertEqual(list(self.root.glob(".config-*.tmp")), [])

    def test_wrong_purpose_cloud_or_missing_model_never_changes_config(self):
        before = self.path.read_bytes()
        selections = [("--model", "whisper-local"), ("--speech-model", "chat-local"),
                      ("--model", "cloud-chat"), ("--model", "undownloaded"), ("--model", "missing")]
        for flag, model in selections:
            with self.subTest(flag=flag, model=model), contextlib.redirect_stderr(io.StringIO()) as error:
                with self.assertRaises(SystemExit) as exit_result:
                    CONFIGURE.main([flag, model])
                self.assertEqual(exit_result.exception.code, 2)
                self.assertNotIn("Traceback", error.getvalue())
                self.assertEqual(self.path.read_bytes(), before)

    def test_invalid_second_assignment_does_not_partially_save_first(self):
        before = self.path.read_bytes()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            CONFIGURE.main(["--model", "chat-local", "--speech-model", "cloud-route"])
        self.assertEqual(before, self.path.read_bytes())

    def test_unavailable_service_is_concise_and_keeps_configuration(self):
        before = self.path.read_bytes()
        with patch.object(LocalFixturePlanner, "models", side_effect=OSError("Connection refused")), contextlib.redirect_stderr(io.StringIO()) as error:
            with self.assertRaises(SystemExit) as exit_result:
                CONFIGURE.main(["--list"])
        self.assertEqual(exit_result.exception.code, 2)
        self.assertIn("Connection refused", error.getvalue())
        self.assertNotIn("Traceback", error.getvalue())
        self.assertEqual(before, self.path.read_bytes())


if __name__ == "__main__":
    unittest.main()
