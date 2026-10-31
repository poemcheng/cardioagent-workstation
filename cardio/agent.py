import copy
import time
import uuid
import numpy as np
from .imaging import quality, demo_candidates, sha256
from .planner import TOOLS
from .store import canonical, now


def validate_context(value):
    allowed = {"age", "acute", "history_complete", "consent"}
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError("背景資料只接受年齡、急性警訊、病史備齊與同意旗標。")
    out = {k: value.get(k) for k in allowed}
    if out["age"] is not None and (type(out["age"]) is not int or not 0 <= out["age"] <= 120):
        raise ValueError("年齡須為 0–120 的整數。")
    for k in allowed - {"age"}:
        if out[k] is not None and type(out[k]) is not bool:
            raise ValueError(f"{k} 須為布林值。")
    return out


class Agent:
    def __init__(self, store, root, data, detector, planner):
        self.store, self.root, self.data = store, root, data
        self.detector, self.planner = detector, planner

    def create(self, case, context, planner, mode, center):
        if planner not in {"rules", "lemonade"} or mode not in {"demo", "model"}:
            raise ValueError("規劃或推論模式無效。")
        if mode == "demo" and not case.get("synthetic"):
            raise ValueError("合成示範模式僅能使用內附合成 CT，禁止對真實影像產生假推論。")
        if type(center) is not int or not 0 <= center < len(case["slices"]):
            raise ValueError("切片索引無效。")
        run = {"id": uuid.uuid4().hex, "case_id": case["id"], "created_at": now(), "status": "queued", "version": 1, "center": center, "context": validate_context(context), "settings": {"planner": planner, "mode": mode}, "completed": [], "tool_log": [], "snapshot": {"modality": case["modality"], "synthetic": case.get("synthetic", False), "slice_sha256": [sha256(self.data/p) for p in case["slices"]]}}
        self.store.save_run(run, "run_created", {"settings": run["settings"], "image_hashes": run["snapshot"]["slice_sha256"]})
        return run

    def execute(self, ident):
        run = self.store.run(ident)
        try:
            if run["status"] != "queued":
                return
            run["status"] = "running"
            self.store.save_run(run, "run_started")
            # Acute symptoms take priority before image I/O, model loading, and LLM.
            if run["context"]["acute"] is True:
                run["status"] = "emergency"
                run["notice"] = "已標示急性警訊：停止一般會診準備，依院所急症流程處理；在臺灣有立即危險時撥打 119。"
                self.store.save_run(run, "acute_priority")
                return
            if run["context"]["acute"] is None:
                run["status"] = "pending_data"
                run["missing"] = ["acute"]
                self.store.save_run(run, "acute_status_required")
                return
            case = self.store.case(run["case_id"])
            arrays = [np.load(self.data/p, allow_pickle=False) for p in case["slices"]]
            for tool in TOOLS:
                if tool in run["completed"]:
                    continue
                _, plan = self.planner.choose(run, tool)
                start = time.perf_counter()
                if tool == "image_quality":
                    result = quality(arrays)
                elif tool == "detect_candidates":
                    if case["modality"] != "CT":
                        raise ValueError("此為 MRI／非 CT；冠狀動脈 CT 鈣化模型不可套用。可在檢視器瀏覽研究影像。")
                    if run["settings"]["mode"] == "demo":
                        result = demo_candidates(arrays[run["center"]])
                    else:
                        result = self.detector.detect(arrays, run["center"], case.get("is_hu", False))
                    run["vision"] = result
                elif tool == "check_context":
                    missing = [k for k, v in run["context"].items() if v is None]
                    if run["context"]["history_complete"] is False:
                        missing.append("history_complete")
                    if run["context"]["consent"] is False:
                        missing.append("consent")
                    result = {"missing": sorted(set(missing))}
                    if missing:
                        run["status"], run["missing"] = "pending_data", result["missing"]
                        run["tool_log"].append({"tool": tool, "plan": plan, "result": result, "elapsed_ms": round((time.perf_counter()-start)*1000)})
                        self.store.save_run(run, "context_missing", result)
                        return
                elif tool == "draft_evidence":
                    run["draft"] = {"case_id": case["id"], "center": run["center"], "modality": case["modality"], "candidate_count": len(run["vision"]["candidates"]), "engine": run["vision"]["engine"], "trained": run["vision"]["trained"], "context": copy.deepcopy(run["context"]), "text": "已整理影像候選與背景資料，待醫師檢視。候選不代表確診，零候選亦不代表無鈣化。此資料未計算 Agatston 分數。", "demo": bool(case.get("synthetic"))}
                    result = run["draft"]
                elif tool == "propose_consult":
                    run["consult_proposal"] = {"kind": "consult", "specialty": "影像科／心臟內科（人工確認）", "time": "待協調", "description": "檢視中心切片候選與證據草稿", "external_connected": False}
                    result = run["consult_proposal"]
                else:
                    run["followup_proposal"] = {"kind": "followup", "time": "由覆核醫師指定", "description": "確認覆核完成與資料交接", "external_connected": False}
                    result = run["followup_proposal"]
                run["completed"].append(tool)
                entry = {"tool": tool, "plan": plan, "result": result, "elapsed_ms": round((time.perf_counter()-start)*1000)}
                run["tool_log"].append(entry)
                self.store.save_run(run, "tool_completed", entry)
            run["status"] = "pending_review"
            run.pop("missing", None)
            self.store.save_run(run, "ready_for_review")
        except Exception as exc:
            run["status"], run["error"] = "blocked", str(exc)[:500]
            self.store.save_run(run, "run_blocked", {"error": run["error"]})

    def resume(self, ident, context, version):
        run = self.store.run(ident)
        if run["status"] != "pending_data" or run["version"] != version:
            raise ValueError("僅可續跑待補件工作，且版本須一致。")
        run["context"] = validate_context(context)
        run["version"] += 1
        run["status"] = "queued"
        run["completed"] = [t for t in run["completed"] if t in TOOLS[:2]]
        for k in ("draft", "consult_proposal", "followup_proposal", "missing"):
            run.pop(k, None)
        self.store.save_run(run, "context_updated", {"version": run["version"]})
        return run

    def review(self, ident, decision, reviewer, reason, version):
        with self.store.connect() as db:
            run = self.store.run(ident)
            if run["status"] != "pending_review" or run["version"] != version:
                raise ValueError("工作已變更或非待覆核狀態，請重新整理。")
            if decision not in {"approve", "reject"} or not reviewer.strip():
                raise ValueError("需選擇核准／退回，並填寫覆核者。")
            if decision == "reject" and not reason.strip():
                raise ValueError("退回須填寫原因。")
            if decision == "approve":
                if run["completed"] != TOOLS or run["context"]["consent"] is not True or run["context"]["acute"] is not False:
                    raise ValueError("工具、同意或急性警訊狀態不符合核准條件。")
                for proposal in (run["consult_proposal"], run["followup_proposal"]):
                    task = proposal | {"id": uuid.uuid4().hex, "run_id": ident, "case_id": run["case_id"], "status": "local_open", "demo": run["snapshot"]["synthetic"], "created_at": now()}
                    db.execute("INSERT INTO tasks VALUES (?,?,?,?)", (task["id"], ident, task["kind"], canonical(task)))
            run["status"] = "approved" if decision == "approve" else "rejected"
            run["review"] = {"decision": decision, "reviewer": reviewer.strip()[:80], "reason": reason.strip()[:500], "at": now()}
            run["version"] += 1
            self.store.save_run(run, "human_review", run["review"], db)
        return run
