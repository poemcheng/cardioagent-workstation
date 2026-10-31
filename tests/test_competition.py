import io
import json
import tempfile
import threading
import unittest
import wave
from unittest.mock import Mock

from cardio.app import create_app
from cardio.planner import Planner


COMPLETE = {"age": 62, "acute": False, "history_complete": True, "consent": True}


def wav_bytes():
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0\0" * 1600)
    return output.getvalue()


class CompetitionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app(self.temp.name, synchronous=True, access_control=False)
        self.client = self.app.test_client()
        self.headers = {"X-CSRF-Token": self.client.get("/api/session").json["csrf"]}
        self.planner = self.app.agent.planner

    def tearDown(self):
        self.app.pool.shutdown(wait=True)
        self.temp.cleanup()

    def prepare(self, **overrides):
        return self.client.post("/api/demo/prepare", json={"case_id": "demo-ct-positive"} | overrides, headers=self.headers)

    def run_case(self, case_id="demo-ct-positive"):
        response = self.client.post("/api/runs", json={"case_id": case_id, "mode": "demo", "planner": "rules", "center": 1, "context": COMPLETE}, headers=self.headers)
        self.assertEqual(response.status_code, 202)
        return response.json["id"]

    def transcribe(self, raw=None, **overrides):
        return self.client.post("/api/demo/transcribe", data={"case_id": "demo-ct-positive", "model": "Whisper-Test", "file": (io.BytesIO(wav_bytes() if raw is None else raw), "private-original-name.wav")} | overrides, headers=self.headers)

    def test_prepare_is_reviewable_and_does_not_save_report_or_tasks(self):
        ident = self.run_case()
        response = self.prepare(run_id=ident, dictation="示範口述，待覆核。")
        self.assertEqual(response.status_code, 200, response.json)
        result = response.json
        self.assertEqual(result["engine"]["source"], "rules")
        snapshot = self.app.store.run(ident)
        self.assertEqual(result["run_snapshot"], {k: snapshot[k] for k in ("id", "version", "status", "updated_at")})
        self.assertIn("候選標記 2 個", result["fields"]["findings"])
        self.assertIn("示範口述", result["fields"]["findings"])
        self.assertTrue(result["requires_human_review"])
        self.assertEqual(self.app.store.tasks(ident), [])
        with self.app.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM reports").fetchone()[0], 0)
        self.assertTrue(self.app.store.verify()["valid"])

    def test_zero_candidates_do_not_assert_no_disease(self):
        ident = self.run_case("demo-ct-zero")
        result = self.prepare(case_id="demo-ct-zero", run_id=ident).json
        self.assertIn("零候選不代表無鈣化", result["fields"]["findings"])

    def test_mri_is_viewing_only_and_never_sent_to_llm(self):
        self.planner.request = Mock(side_effect=AssertionError("no model call"))
        self.planner.local_model = Mock(side_effect=AssertionError("no model enumeration"))
        result = self.prepare(case_id="research-mri-01", use_llm=True, model="local-test").json
        self.assertEqual(result["engine"]["source"], "rules_fallback")
        self.assertIn("未對此影像執行", result["fields"]["findings"])
        self.assertEqual(next(c for c in result["checks"] if c["key"] == "modality")["status"], "blocked")
        self.planner.request.assert_not_called()

    def test_run_case_mismatch_fails(self):
        ident = self.run_case()
        self.assertEqual(self.prepare(case_id="demo-ct-zero", run_id=ident).status_code, 400)

    def test_source_lookup_and_retrieval_are_traceable_internal_documents(self):
        result = self.prepare(query="NPU 語音離線本機模型").json
        self.assertEqual(result["sources"][0]["id"], "local-ai")
        source = self.client.get(result["sources"][0]["url"])
        self.assertEqual(source.status_code, 200)
        self.assertIn("非醫院核准", source.json["scope"])
        self.assertEqual(result["sources"][0]["retrieval"], "local_keyword_match")
        self.assertEqual(self.client.get("/api/demo/sources/missing").status_code, 404)

    def test_llm_receives_only_synthetic_flow_facts(self):
        self.planner.local_model = Mock(return_value={"id": "local-test", "recipe": "flm"})
        self.planner.request = Mock(return_value={"id": "reply-1", "choices": [{"message": {"content": json.dumps({"summary": "合成流程待人工覆核。", "source_ids": ["human-review"]})}}]})
        result = self.prepare(run_id=self.run_case(), dictation="PRIVATE-TEXT", use_llm=True, model="local-test").json
        self.assertEqual(result["engine"]["source"], "lemonade")
        self.assertEqual(result["engine"]["recipe"], "flm")
        payload = self.planner.request.call_args.args[1]
        facts = json.loads(payload["messages"][1]["content"])
        self.assertTrue(facts["synthetic"])
        self.assertNotIn("PRIVATE-TEXT", json.dumps(payload))
        self.assertNotIn("age", facts)
        self.assertNotIn("case_id", facts)
        self.assertEqual(result["fields"]["impression"], "流程草稿，待影像覆核者依原始影像填寫判讀結論。")

    def test_invalid_model_json_or_unknown_citations_falls_back(self):
        self.planner.local_model = Mock(return_value={"recipe": "llamacpp"})
        for content in ["not JSON", json.dumps({"summary": "summary", "source_ids": ["invented-guideline"]}), json.dumps({"summary": "summary", "source_ids": [], "diagnosis": "invented"})]:
            self.planner.request = Mock(return_value={"choices": [{"message": {"content": content}}]})
            response = self.prepare(use_llm=True, model="local-test")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json["engine"]["source"], "rules_fallback")
            self.assertIn("reason", response.json["engine"])

    def test_status_filters_cloud_undownloaded_and_wrong_purpose_models(self):
        self.planner.models = Mock(return_value={"data": [
            {"id": "local-test", "recipe": "flm", "downloaded": True},
            {"id": "speech-test", "recipe": "whispercpp", "downloaded": True},
            {"id": "cloud-test", "recipe": "llamacpp", "downloaded": True, "cloud_provider": "external"},
            {"id": "missing-test", "recipe": "flm", "downloaded": False},
            {"id": "embedding-test", "recipe": "llamacpp", "downloaded": True, "labels": ["embeddings"]},
        ]})
        status = self.client.get("/api/demo/status").json
        self.assertTrue(status["service"]["available"])
        self.assertEqual([m["id"] for m in status["available_models"]], ["local-test"])
        self.assertEqual([m["id"] for m in status["speech_models"]], ["speech-test"])
        self.assertFalse(status["hardware_verified"])
        self.assertFalse(status["npu_verified"])

    def test_service_failure_is_disclosed_and_rules_remain_usable(self):
        self.planner.models = Mock(side_effect=OSError("service unavailable"))
        status = self.client.get("/api/demo/status").json
        self.assertFalse(status["service"]["available"])
        self.assertEqual(self.prepare().status_code, 200)
        self.assertEqual(self.prepare(use_llm=True, model="local-test").json["engine"]["source"], "rules_fallback")

    def test_real_transcription_response_and_audio_not_persisted(self):
        self.planner.local_model = Mock(return_value={"recipe": "whispercpp"})
        self.planner.request_bytes = Mock(return_value={"text": "這是辨識服務回傳的示範逐字稿。"})
        response = self.transcribe()
        self.assertEqual(response.status_code, 200, response.json)
        self.assertIn("辨識服務回傳", response.json["text"])
        self.assertFalse(response.json["engine"]["npu_verified"])
        args = self.planner.request_bytes.call_args.args
        self.assertEqual(args[0], "/audio/transcriptions")
        self.assertNotIn(b"private-original-name", args[1])
        self.assertIn(b'filename="demo.wav"', args[1])
        self.assertFalse(list(__import__("pathlib").Path(self.temp.name).rglob("*.wav")))

    def test_unavailable_speech_never_fabricates_transcript(self):
        self.planner.local_model = Mock(side_effect=OSError("not running"))
        response = self.transcribe()
        self.assertEqual(response.status_code, 503)
        self.assertIsNone(response.json["text"])
        self.assertFalse(response.json["available"])
        self.assertIn("未產生逐字稿", response.json["error"])

    def test_bad_audio_and_real_case_are_rejected_before_service(self):
        self.planner.local_model = Mock(side_effect=AssertionError("must not call"))
        self.assertEqual(self.transcribe(raw=b"not audio").status_code, 400)
        self.assertEqual(self.transcribe(case_id="research-mri-01").status_code, 400)
        self.planner.local_model.assert_not_called()

    def test_prepare_validates_input_and_csrf(self):
        self.assertEqual(self.prepare(dictation="a" * 6001).status_code, 400)
        self.assertEqual(self.prepare(use_llm="true").status_code, 400)
        self.assertEqual(self.prepare(run_id=["invalid-type"]).status_code, 400)
        self.assertEqual(self.client.post("/api/demo/prepare", json={"case_id": "demo-ct-positive"}).status_code, 403)

    def test_slow_optional_model_does_not_block_acute_priority_workflow(self):
        started, release, acute_finished = threading.Event(), threading.Event(), threading.Event()
        outcomes = {}
        self.planner.local_model = Mock(return_value={"recipe": "flm"})
        def slow_completion(*args, **kwargs):
            started.set()
            release.wait(5)
            return {"choices": [{"message": {"content": json.dumps({"summary": "待人工覆核。", "source_ids": ["human-review"]})}}]}
        self.planner.request = Mock(side_effect=slow_completion)
        def slow_prepare():
            client = self.app.test_client()
            token = client.get("/api/session").json["csrf"]
            outcomes["prepare"] = client.post("/api/demo/prepare", json={"case_id": "demo-ct-positive", "model": "local-test", "use_llm": True}, headers={"X-CSRF-Token": token}).status_code
        def acute_run():
            client = self.app.test_client()
            token = client.get("/api/session").json["csrf"]
            response = client.post("/api/runs", json={"case_id": "demo-ct-positive", "mode": "demo", "planner": "rules", "center": 1, "context": COMPLETE | {"acute": True}}, headers={"X-CSRF-Token": token})
            outcomes["acute"] = client.get("/api/runs/" + response.json["id"]).json["status"]
            acute_finished.set()
        draft_thread = threading.Thread(target=slow_prepare)
        acute_thread = threading.Thread(target=acute_run)
        draft_thread.start()
        try:
            self.assertTrue(started.wait(2), "draft model did not start")
            acute_thread.start()
            self.assertTrue(acute_finished.wait(2), "acute workflow waited for optional draft model")
        finally:
            release.set()
            draft_thread.join(5)
            if acute_thread.ident is not None:
                acute_thread.join(5)
        self.assertEqual(outcomes["acute"], "emergency")
        self.assertEqual(outcomes["prepare"], 200)


class CompetitionAuthTests(unittest.TestCase):
    def test_coordinator_can_read_status_but_cannot_prepare_or_transcribe(self):
        with tempfile.TemporaryDirectory() as data:
            app = create_app(data, synchronous=True)
            try:
                app.auth.add("demo-admin", "Demo-test-password-123", "admin", initial=True)
                app.auth.add("demo-coordinator", "Demo-test-password-456", "coordinator")
                client = app.test_client()
                csrf = client.get("/api/session").json["csrf"]
                self.assertEqual(client.post("/api/auth/login", json={"username": "demo-coordinator", "password": "Demo-test-password-456"}, headers={"X-CSRF-Token": csrf}).status_code, 200)
                csrf = client.get("/api/session").json["csrf"]
                headers = {"X-CSRF-Token": csrf}
                app.agent.planner.models = Mock(return_value={"data": []})
                self.assertEqual(client.get("/api/demo/status").status_code, 200)
                self.assertEqual(client.post("/api/demo/prepare", json={"case_id": "demo-ct-positive"}, headers=headers).status_code, 403)
                self.assertEqual(client.post("/api/demo/transcribe", data={}, headers=headers).status_code, 403)
            finally:
                app.pool.shutdown(wait=True)


class RecipeTests(unittest.TestCase):
    def test_explicit_local_chat_recipe_allowlist(self):
        planner = Planner({})
        for recipe in ["llamacpp", "flm", "ryzenai-llm", "oga-hybrid"]:
            planner.models = Mock(return_value={"data": [{"id": "test", "recipe": recipe, "downloaded": True}]})
            self.assertEqual(planner.local_model("test")["recipe"], recipe)
        for recipe in ["cloud", "unknown", "whispercpp", "collection.omni"]:
            planner.models = Mock(return_value={"data": [{"id": "test", "recipe": recipe, "downloaded": True}]})
            with self.assertRaises(ValueError):
                planner.local_model("test")

    def test_speech_backend_must_match_purpose(self):
        self.assertTrue(Planner.is_local({"recipe": "whispercpp", "downloaded": True}, "speech"))
        self.assertTrue(Planner.is_local({"recipe": "flm", "downloaded": True, "labels": ["transcription"]}, "speech"))
        self.assertFalse(Planner.is_local({"recipe": "flm", "downloaded": True}, "speech"))
        self.assertFalse(Planner.is_local({"recipe": "whispercpp", "downloaded": True, "cloud_provider": "external"}, "speech"))


if __name__ == "__main__":
    unittest.main()
