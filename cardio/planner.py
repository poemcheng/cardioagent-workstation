import json
import time
import urllib.request
from urllib.parse import urlparse

TOOLS = ["image_quality", "detect_candidates", "check_context", "draft_evidence", "propose_consult", "propose_followup"]
LOCAL_CHAT_RECIPES = frozenset({"llamacpp", "flm", "ryzenai-llm", "oga-hybrid"})
LOCAL_SPEECH_RECIPES = frozenset({"whispercpp", "flm"})


class Planner:
    def __init__(self, config):
        from .resources import ModelGate
        self.gate = ModelGate()
        self.config = config
        parsed = urlparse(config.get("lemonade_url", "http://127.0.0.1:13305/v1"))
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path.rstrip("/") not in {"/api/v1", "/v1"}:
            raise ValueError("Lemonade 端點僅允許 http://127.0.0.1:<port>/v1 或 /api/v1。")
        self.base = config.get("lemonade_url", "http://127.0.0.1:13305/v1").rstrip("/")
        # Do not route local patient workflows through an inherited HTTP proxy.
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPRedirectHandler())

    def request_bytes(self, endpoint, raw=None, content_type="application/json", timeout=5):
        if endpoint in {"/chat/completions", "/audio/transcriptions"}:
            with self.gate.slot(endpoint, timeout=timeout) as trace:
                result = self._request_bytes(endpoint, raw, content_type, timeout)
            result['_cardio_request'] = trace.copy()
            return result
        return self._request_bytes(endpoint, raw, content_type, timeout)

    def _request_bytes(self, endpoint, raw=None, content_type="application/json", timeout=5):
        if endpoint not in {"/models", "/chat/completions", "/audio/transcriptions"}:
            raise ValueError("不允許的本機模型端點。")
        req = urllib.request.Request(self.base + endpoint, data=raw, headers={"Content-Type": content_type})
        # Disable redirects: a loopback endpoint may not redirect sensitive state.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(req, timeout=timeout) as response:
            content = response.read(1_048_577)
            if len(content) > 1_048_576:
                raise ValueError("Lemonade response too large")
            return json.loads(content)

    def request(self, endpoint, payload=None, timeout=5):
        raw = None if payload is None else json.dumps(payload).encode()
        return self.request_bytes(endpoint, raw, timeout=timeout)

    def models(self):
        return self.request("/models")

    @staticmethod
    def is_local(model, purpose="chat"):
        if not isinstance(model, dict) or model.get("downloaded") is not True or model.get("cloud_provider"):
            return False
        recipe = model.get("recipe")
        labels = model.get("labels", [])
        if not isinstance(labels, list):
            return False
        if purpose == "speech":
            return recipe in LOCAL_SPEECH_RECIPES and (recipe == "whispercpp" or "transcription" in labels)
        incompatible = {"embeddings", "embedding", "reranking", "transcription", "image", "tts", "classification"}
        return recipe in LOCAL_CHAT_RECIPES and not incompatible.intersection(labels)

    def local_model(self, ident, purpose="chat"):
        entries = self.models().get("data", [])
        model = next((m for m in entries if m.get("id") == ident), None)
        if not self.is_local(model, purpose):
            raise ValueError("僅允許已下載且符合用途的本機模型；禁止雲端路由與未下載模型。")
        return model

    def choose(self, run, expected):
        if run["settings"]["planner"] == "rules":
            return expected, {"source": "rules", "reason": "explicit rule mode", "elapsed_ms": 0}
        start = time.perf_counter()
        state = {"completed_tools": list(run["completed"]), "candidate_count": len(run.get("vision", {}).get("candidates", [])), "field_present": {k: run["context"].get(k) is not None for k in ("age", "acute", "history_complete", "consent")}, "allowed_next": [expected]}
        payload = {"model": self.config.get("planner_model", ""), "messages": [{"role": "system", "content": 'Choose one allowed_next tool. Return only JSON {"tool":"name"}. No medical interpretation.'}, {"role": "user", "content": json.dumps(state)}], "temperature": 0, "max_tokens": 80, "stream": False}
        evidence = {"requested_model": payload['model']}
        try:
            if not payload["model"]:
                raise ValueError("尚未設定 Lemonade model ID")
            selected = self.local_model(payload["model"])
            evidence['recipe'] = selected.get('recipe')
            result = self.request("/chat/completions", payload, timeout=self.config.get("planner_timeout_s", 45))
            evidence.update(response_id=result.get('id'), model=result.get('model'),
                            usage=result.get('usage'), request_trace=result.get('_cardio_request'))
            if result.get("model") != payload["model"]:
                raise ValueError("回應模型與指定本機模型不符")
            if not isinstance(result.get("id"), str) or not result["id"].strip():
                raise ValueError("模型回應缺少 response ID")
            content = result["choices"][0]["message"]["content"]
            choice = json.loads(content)
            if set(choice) != {"tool"} or choice["tool"] != expected or choice["tool"] not in TOOLS:
                raise ValueError("工具計畫不符合允許清單或依賴")
            return expected, {**evidence, "source": "lemonade",
                "elapsed_ms": round((time.perf_counter()-start)*1000)}
        except Exception as exc:
            return expected, {**evidence, "source": "rules_fallback", "reason": str(exc)[:300], "elapsed_ms": round((time.perf_counter()-start)*1000)}
