import argparse
import importlib.util
import sys
import tempfile
import types
import unittest
import json
import socket
import subprocess
import time
import urllib.request
from pathlib import Path
from unittest.mock import Mock, patch

import run_demo


class DemoLauncherTests(unittest.TestCase):
    def test_default_data_is_separate_from_workstation_data(self):
        args = run_demo.build_parser().parse_args([])
        self.assertEqual(args.data_dir, run_demo.ROOT / "integrated_demo_data")
        for unsafe in (run_demo.ROOT, run_demo.ROOT / "data"):
            with self.assertRaises(argparse.ArgumentTypeError):
                run_demo.demo_directory(unsafe)

    def test_port_and_file_paths_are_validated(self):
        for value in ("0", "65536", "1.2", "external.example"):
            with self.assertRaises(argparse.ArgumentTypeError):
                run_demo.port_number(value)
        self.assertEqual(run_demo.port_number("8766"), 8766)
        with tempfile.NamedTemporaryFile() as file:
            with self.assertRaises(argparse.ArgumentTypeError):
                run_demo.demo_directory(file.name)

    def test_server_is_loopback_and_created_before_browser(self):
        calls = []
        server = Mock()
        server.run.side_effect = lambda: calls.append("run")
        app = Mock()
        def create_server(*args, **kwargs):
            calls.append("server")
            self.assertEqual(kwargs["host"], "127.0.0.1")
            self.assertEqual(kwargs["port"], 9876)
            return server
        fake_waitress = types.ModuleType("waitress")
        fake_waitress.create_server = create_server
        fake_app = types.ModuleType("cardio.app")
        fake_app.create_app = Mock(return_value=app)
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, {"waitress": fake_waitress, "cardio.app": fake_app}), patch("webbrowser.open", side_effect=lambda url: calls.append("browser") or True):
            result = run_demo.main(["--data-dir", directory, "--port", "9876", "--open-browser"])
        self.assertEqual(result, 0)
        self.assertEqual(calls, ["server", "browser", "run"])
        app.pool.shutdown.assert_called_once_with(wait=True)
        server.close.assert_called_once()

    def test_bind_failure_does_not_open_browser_or_skip_cleanup(self):
        fake_waitress = types.ModuleType("waitress")
        fake_waitress.create_server = Mock(side_effect=OSError("Address already in use"))
        app = Mock()
        fake_app = types.ModuleType("cardio.app")
        fake_app.create_app = Mock(return_value=app)
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, {"waitress": fake_waitress, "cardio.app": fake_app}), patch("webbrowser.open") as browser:
            self.assertEqual(run_demo.main(["--data-dir", directory, "--open-browser"]), 1)
        browser.assert_not_called()
        app.pool.shutdown.assert_called_once_with(wait=True)

    def test_preflight_never_claims_npu_execution_from_provider_listing(self):
        path = run_demo.ROOT / "scripts" / "demo_preflight.py"
        spec = importlib.util.spec_from_file_location("demo_preflight", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        fake_ort = types.ModuleType("onnxruntime")
        fake_ort.get_available_providers = lambda: ["CPUExecutionProvider", "VitisAIExecutionProvider"]
        with patch.dict(sys.modules, {"onnxruntime": fake_ort}):
            report = module.inspect(probe_service=False)
        self.assertIn("VitisAIExecutionProvider", report["available_onnx_providers"])
        self.assertFalse(report["npu_execution_verified"])
        self.assertFalse(report["amd_hardware_verified"])
        self.assertFalse(report["lemonade"]["probed"])
        self.assertFalse(report["downloads_performed"])

    def test_real_server_starts_with_empty_authenticated_demo_directory(self):
        # Exercise the entry point itself, not Flask's test-client adapter.
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with tempfile.TemporaryDirectory() as directory:
            process = subprocess.Popen([sys.executable, str(run_demo.ROOT / "run_demo.py"),
                                        "--port", str(port), "--data-dir", directory],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                deadline = time.monotonic() + 10
                while True:
                    try:
                        with opener.open(f"http://127.0.0.1:{port}/api/auth/status", timeout=1) as response:
                            status = json.load(response)
                        break
                    except OSError:
                        if process.poll() is not None or time.monotonic() > deadline:
                            process.terminate()
                            _, error_text = process.communicate(timeout=10)
                            self.fail("Demo entry point did not start: " + error_text)
                        time.sleep(0.05)
                self.assertTrue(status["setup_required"])
                self.assertIsNone(status["user"])
                self.assertTrue((Path(directory) / "cardio.sqlite3").is_file())
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    opener.open(f"http://127.0.0.1:{port}/api/cases", timeout=1)
                self.assertEqual(denied.exception.code, 401)
            finally:
                process.terminate()
                process.communicate(timeout=10)


if __name__ == "__main__":
    unittest.main()
