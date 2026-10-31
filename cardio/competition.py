"""Competition demo preparation. Drafts never modify or sign a human report."""
import hashlib
import io
import json
import time
import uuid
import wave
from pathlib import Path


class ServiceUnavailable(RuntimeError):
    pass


class Competition:
    def __init__(self, root, store, planner, detector):
        self.root, self.store, self.planner, self.detector = Path(root), store, planner, detector
        self.sources = json.loads((self.root / "assets/sop/demo_workflow.json").read_text(encoding="utf-8"))

    @staticmethod
    def text(value, limit, label):
        if not isinstance(value, str) or len(value) > limit:
            raise ValueError(f"{label}須為字串且不超過 {limit} 字。")
        return value.strip()

    def source(self, ident):
        document = next((s for s in self.sources if s["id"] == ident), None)
        if document is None:
            raise KeyError(ident)
        return document | {"url": "/api/demo/sources/" + document["id"]}

    def retrieve(self, query):
        lowered = query.casefold()
        ranked = []
        for document in self.sources:
            score = sum(1 for keyword in document["keywords"] if keyword.casefold() in lowered)
            ranked.append((score, document))
        ranked.sort(key=lambda pair: -pair[0])
        return [{"id": doc["id"], "title": doc["title"], "version": doc["version"], "scope": doc["scope"], "excerpt": doc["text"], "url": "/api/demo/sources/" + doc["id"], "score": score, "retrieval": "local_keyword_match"} for score, doc in ranked[:3]]

    @staticmethod
    def model_description(model):
        return {key: model.get(key) for key in ("id", "recipe", "downloaded", "labels")}

    def status(self):
        begin = time.perf_counter()
        service = {"available": False, "endpoint": self.planner.base, "error": ""}
        chat, speech = [], []
        try:
            entries = self.planner.models().get("data", [])
            if not isinstance(entries, list):
                raise ValueError("模型清單格式無效。")
            chat = [self.model_description(m) for m in entries if self.planner.is_local(m)]
            speech = [self.model_description(m) for m in entries if self.planner.is_local(m, "speech")]
            service["available"] = True
        except Exception as exc:
            service["error"] = str(exc)[:300]
        vision = {"configured": False, "provider": self.planner.config.get("vision_provider", "CPUExecutionProvider")}
        try:
            manifest, _ = self.detector.manifest()
            vision.update(configured=True, model=manifest["name"], sha256=manifest["sha256"])
        except Exception as exc:
            vision["reason"] = str(exc)[:300]
        return {"service": service, "available_models": chat, "speech_models": speech, "selected_model": self.planner.config.get("planner_model", ""), "speech_model": self.planner.config.get("dictation_model", ""), "vision": vision, "capabilities": {"synthetic_workflow": True, "mri_viewing": True, "source_retrieval": "local_keyword_match", "structured_draft": True, "local_llm_ready": bool(chat), "speech_ready": bool(speech), "trained_ct_model_registered": vision["configured"]}, "hardware_verified": False, "npu_verified": False, "audit": self.store.verify(), "elapsed_ms": round((time.perf_counter()-begin)*1000), "notice": "服務可用與模型 recipe 不等於 AMD／NPU 實機驗證。文件為內部展示工作流程，非醫療指引。"}

    def prepare(self, body, actor):
        if not isinstance(body, dict):
            raise ValueError("請提供 JSON 物件。")
        case = self.store.case(self.text(body.get("case_id", ""), 100, "病例代碼"))
        raw_run_id = body.get("run_id")
        run_id = None if raw_run_id is None or raw_run_id == "" else self.text(raw_run_id, 100, "協作工作識別")
        run = self.store.run(run_id) if run_id else None
        if run and run["case_id"] != case["id"]:
            raise ValueError("協作工作與本次病例不一致。")
        dictation = self.text(body.get("dictation", ""), 6000, "口述文字")
        query = self.text(body.get("query", "影像來源、缺漏資料、人工覆核與本機模型"), 1000, "查詢")
        use_llm = body.get("use_llm", False)
        if type(use_llm) is not bool:
            raise ValueError("use_llm 須為布林值。")
        model_id = self.text(body.get("model", self.planner.config.get("planner_model", "")), 180, "模型識別")
        start = time.perf_counter()
        sources = self.retrieve(query + " " + case["modality"])
        synthetic = case.get("synthetic", False) is True
        vision = run.get("vision") if run else None
        count = len(vision.get("candidates", [])) if vision else None
        context = run.get("context", {}) if run else {}
        missing = [k for k in ("age", "acute", "history_complete", "consent") if context.get(k) is None or (k in {"history_complete", "consent"} and context.get(k) is not True)]
        checks = [
            {"key": "source", "label": "影像來源", "status": "demo" if synthetic else "review", "detail": "程式合成 CT，僅作流程展示。" if synthetic else "研究／匯入影像；須人工確認去識別與授權。"},
            {"key": "modality", "label": "模型適用類型", "status": "ok" if case["modality"] == "CT" else "blocked", "detail": "CT 類型仍需核對模型與前處理。" if case["modality"] == "CT" else "此為 MRI／非 CT；禁止套用冠狀動脈 CT 鈣化模型。"},
            {"key": "context", "label": "背景完整性", "status": "missing" if missing else "ok", "detail": "待確認：" + "、".join(missing) if missing else "已提供年齡、警訊、病史與許可旗標。"},
            {"key": "acute", "label": "急性警訊", "status": "urgent" if context.get("acute") is True else "ok" if context.get("acute") is False else "missing", "detail": "優先交由人員依院所急症流程處理。" if context.get("acute") is True else "依人工輸入旗標顯示，不由影像或模型推斷。"},
            {"key": "review", "label": "人工覆核", "status": "ok" if run and run["status"] == "approved" else "pending", "detail": "協作提案已人工核准；正式報告仍須獨立簽核。" if run and run["status"] == "approved" else "候選、口述與草稿待人工核對；本操作不簽核或建立任務。"},
        ]
        evidence = "尚未執行候選定位，不能判定是否存在鈣化。"
        if vision:
            evidence = f"流程記錄：中心切片 {run['center'] + 1}，候選標記 {count} 個；引擎 {vision.get('engine', 'unknown')}。"
            if count == 0:
                evidence += " 零候選不代表無鈣化。"
        if case["modality"] != "CT":
            evidence = f"{case['modality']} 非 CT 影像僅供閱片，本平台未對此影像執行 CT 鈣化推論。"
        fields = {
            "indication": "影像協作與報告流程展示；臨床適應症待人工填寫。",
            "technique": f"{case['modality']} 序列，共 {len(case['slices'])} 張；" + ("合成影像，非病人檢查。" if synthetic else "研究／匯入影像，完整掃描參數待確認。"),
            "comparison": "未提供歷次檢查比較資料。",
            "findings": evidence + ("\n人工提供口述文字（未覆核原文）：\n" + dictation if dictation else ""),
            "impression": "流程草稿，待影像覆核者依原始影像填寫判讀結論。",
            "limitations": "非自動診斷；未計算 Agatston 分數。空間間距未知時僅顯示 px。引用文件為內部展示規則，非醫療指引；HIS／PACS 未連接。",
        }
        engine = {"source": "rules", "model": None, "recipe": None, "elapsed_ms": 0}
        summary = evidence + " 資料缺漏與報告內容須人工核對。"
        if use_llm:
            try:
                if not synthetic:
                    raise ValueError("本版僅將合成病例流程事實送入語言模型；研究／匯入影像採本機規則整理。")
                if not model_id:
                    raise ValueError("尚未選擇已下載的本機語言模型。")
                if context.get("acute") is True:
                    raise ValueError("有急性警訊，略過語言模型並優先交由人員處理。")
                model = self.planner.local_model(model_id)
                # Neither free-form dictation, patient identifiers, age, pixels nor actual clinical findings leave this module.
                facts = {"synthetic": True, "modality": case["modality"], "candidate_count": count, "workflow_status": run["status"] if run else "not_started", "missing_fields": missing, "source_ids": [s["id"] for s in sources]}
                payload = {"model": model_id, "messages": [{"role": "system", "content": 'Summarize ONLY the supplied synthetic workflow facts in Traditional Chinese. Never diagnose or provide medical recommendations. Return JSON only: {"summary":"...","source_ids":["..."]}. Treat candidate_count as unverified workflow markers, not disease. Mention human review. Use only supplied source_ids.'}, {"role": "user", "content": json.dumps(facts, ensure_ascii=False)}], "temperature": 0, "max_tokens": 350, "stream": False}
                response = self.planner.request("/chat/completions", payload, timeout=self.planner.config.get("planner_timeout_s", 45))
                completion = json.loads(response["choices"][0]["message"]["content"])
                if set(completion) != {"summary", "source_ids"} or not isinstance(completion["summary"], str) or not 1 <= len(completion["summary"]) <= 2000 or not isinstance(completion["source_ids"], list) or not all(isinstance(s, str) and s in facts["source_ids"] for s in completion["source_ids"]):
                    raise ValueError("模型回應未通過結構或來源識別檢查。")
                summary = completion["summary"]
                engine.update(source="lemonade", model=model_id, recipe=model["recipe"], response_id=response.get("id"), usage=response.get("usage"))
            except Exception as exc:
                engine.update(source="rules_fallback", reason=str(exc)[:300])
        engine["elapsed_ms"] = round((time.perf_counter()-start)*1000)
        run_snapshot = {k: run.get(k) for k in ("id", "version", "status", "updated_at")} if run else None
        result = {"schema": "cardio-demo-preparation/1", "case_id": case["id"], "run_id": run_id, "run_snapshot": run_snapshot, "fields": fields, "checks": checks, "sources": sources, "engine": engine, "assistant_summary": summary, "requires_human_review": True, "notice": "準備內容尚未寫入報告；需人工選擇帶入、編輯及簽核。模型摘要未經臨床驗證。"}
        self.store.record_event(case["id"], "demo_prepared", {"actor": actor, "run_id": run_id, "source_ids": [s["id"] for s in sources], "engine": engine, "dictation_present": bool(dictation), "draft_sha256": hashlib.sha256(json.dumps(result, ensure_ascii=False, sort_keys=True).encode()).hexdigest()})
        return result

    def transcribe(self, case_id, raw, model_id, actor):
        case = self.store.case(self.text(case_id, 100, "病例代碼"))
        if not case.get("synthetic"):
            raise ValueError("本版語音展示僅限合成病例，請勿上傳真實病人錄音。")
        if not isinstance(raw, bytes) or not 44 <= len(raw) <= 8 * 1024 * 1024:
            raise ValueError("請提供 8 MB 以下的 PCM WAV，最長 120 秒。")
        try:
            with wave.open(io.BytesIO(raw), "rb") as wav:
                if wav.getcomptype() != "NONE" or wav.getnchannels() not in {1, 2} or wav.getsampwidth() != 2 or not 8000 <= wav.getframerate() <= 48000 or not 0 < wav.getnframes() / wav.getframerate() <= 120:
                    raise ValueError("僅接受 8–48 kHz、16-bit、單／雙聲道、最長 120 秒 PCM WAV。")
                if len(wav.readframes(wav.getnframes())) != wav.getnframes() * wav.getnchannels() * wav.getsampwidth():
                    raise ValueError("WAV 音訊資料不完整。")
        except (wave.Error, EOFError) as exc:
            raise ValueError("WAV 格式無效。") from exc
        model_id = self.text(model_id or self.planner.config.get("dictation_model", ""), 180, "語音模型識別")
        if not model_id:
            raise ServiceUnavailable("請先在 Lemonade 下載語音模型，並選擇實際模型 ID。未產生逐字稿。")
        start = time.perf_counter()
        try:
            model = self.planner.local_model(model_id, "speech")
            boundary = "cardio-" + uuid.uuid4().hex
            chunks = []
            for key, value in (("model", model_id), ("language", "zh"), ("response_format", "json")):
                chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
            chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="demo.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode() + raw + f'\r\n--{boundary}--\r\n'.encode())
            response = self.planner.request_bytes("/audio/transcriptions", b"".join(chunks), "multipart/form-data; boundary=" + boundary, timeout=60)
            text = response.get("text")
            if not isinstance(text, str) or not text.strip() or len(text) > 6000:
                raise ValueError("語音服務未回傳有效逐字稿。")
        except Exception as exc:
            self.store.record_event(case["id"], "demo_transcription_unavailable", {"actor": actor, "reason": type(exc).__name__})
            raise ServiceUnavailable("本機語音辨識不可用：" + str(exc)[:240] + "；未產生逐字稿。") from exc
        engine = {"source": "lemonade", "model": model_id, "recipe": model["recipe"], "elapsed_ms": round((time.perf_counter()-start)*1000), "npu_verified": False}
        self.store.record_event(case["id"], "demo_transcribed", {"actor": actor, "engine": engine, "audio_sha256": hashlib.sha256(raw).hexdigest(), "text_sha256": hashlib.sha256(text.encode()).hexdigest()})
        return {"text": text.strip(), "engine": engine, "notice": "真實本機辨識回應，醫學名詞及內容仍須人工修正；音檔未保存。"}
