"""Measured, source-bound analysis pipeline; no automatic diagnosis or treatment."""
import copy
import hashlib
import json
import math
import os
import re
import threading
import time
import uuid
from pathlib import Path

import numpy as np

from .imaging import demo_candidates, quality, sha256
from .store import canonical, now
from . import __version__


IDENTIFIER = re.compile(r"[0-9a-f]{32}\Z")
METRIC_SCOPE = "synthetic_fixture_only_not_clinical_accuracy"
SOURCE_IDS = ("quality", "recognition", "segmentation", "comparison", "reconstruction")


def box_iou(left, right):
    a, b = np.asarray(left, dtype=float), np.asarray(right, dtype=float)
    if a.shape != (4,) or b.shape != (4,) or not np.isfinite(a).all() or not np.isfinite(b).all():
        raise ValueError("參考框須為四個有限座標。")
    if np.any(a[2:] <= a[:2]) or np.any(b[2:] <= b[:2]):
        raise ValueError("參考框面積須大於零。")
    intersection = float(np.prod(np.maximum(np.minimum(a[2:], b[2:]) - np.maximum(a[:2], b[:2]), 0)))
    union = float(np.prod(a[2:] - a[:2]) + np.prod(b[2:] - b[:2]) - intersection)
    return intersection / union


def compare_boxes(candidates, reference, threshold=.5):
    """Deterministic one-to-one maximum-IoU greedy matching on one labeled slice."""
    if len(candidates) > 300 or len(reference) > 300:
        raise ValueError("候選與參考框各不得超過 300 個。")
    possible = []
    for i, candidate in enumerate(candidates):
        for j, truth in enumerate(reference):
            value = box_iou(candidate["xyxy"], truth)
            if value >= threshold:
                possible.append((value, i, j))
    possible.sort(key=lambda row: (-row[0], row[1], row[2]))
    used_candidates, used_truth, matches = set(), set(), []
    for value, i, j in possible:
        if i not in used_candidates and j not in used_truth:
            used_candidates.add(i)
            used_truth.add(j)
            matches.append({"candidate_index": i, "reference_index": j, "iou": round(value, 6)})
    tp, fp, fn = len(matches), len(candidates) - len(matches), len(reference) - len(matches)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None
    return {"available": True, "tp": tp, "fp": fp, "fn": fn,
            "precision": precision, "recall": recall, "f1": f1,
            "mean_iou": sum(m["iou"] for m in matches) / tp if tp else None,
            "iou_threshold": threshold, "matching": "one_to_one_greedy_descending_iou",
            "matches": matches, "scope": METRIC_SCOPE,
            "notice": "僅此合成切片的已知框對照，不代表病人層級或臨床效能。"}


def compare_masks(prediction, reference):
    prediction, reference = np.asarray(prediction), np.asarray(reference)
    if prediction.shape != reference.shape or prediction.ndim != 3:
        raise ValueError("分割參考必須為相同尺寸的三維遮罩。")
    if not np.isin(reference, (0, 1)).all():
        raise ValueError("分割參考只接受二元 0／1。")
    p, r = prediction.astype(bool, copy=False), reference.astype(bool, copy=False)
    intersection = int(np.count_nonzero(p & r))
    p_count, r_count = int(np.count_nonzero(p)), int(np.count_nonzero(r))
    union = p_count + r_count - intersection
    denominator = p_count + r_count
    return {"available": True, "dice": 2 * intersection / denominator if denominator else 1.,
            "iou": intersection / union if union else 1., "intersection_voxels": intersection,
            "predicted_voxels": p_count, "reference_voxels": r_count,
            "both_empty": denominator == 0, "scope": METRIC_SCOPE,
            "reference_use": "evaluation_only_not_used_for_threshold_or_mesh",
            "notice": "閾值結果對照獨立合成遮罩；不是已訓練分割模型的準確率。"}


class Analysis:
    def __init__(self, root, data, store, detector, planner):
        self.root, self.data = Path(root).resolve(), Path(data).resolve()
        self.store, self.detector, self.planner = store, detector, planner
        self.folder = self.data / "analyses"
        self.folder.mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.segmenter = None

    @staticmethod
    def _text(value, limit, label, empty=False):
        if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
            raise ValueError(f"{label}須為有效字串且不超過 {limit} 字。")
        return value.strip()

    def _path(self, ident):
        if not isinstance(ident, str) or not IDENTIFIER.fullmatch(ident):
            raise ValueError("分析識別碼格式無效。")
        return self.folder / (ident + ".json")

    def _source_path(self, relative):
        if not isinstance(relative, str):
            raise ValueError("影像來源路徑無效。")
        path = (self.data / relative).resolve()
        if not path.is_relative_to(self.data) or not path.is_file():
            raise ValueError("影像來源必須位於本機資料目錄。")
        return path

    def _save(self, result, kind, actor):
        path = self._path(result["id"])
        raw = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        temporary = path.with_suffix(".tmp")
        with self.lock:
            if path.exists():
                previous_raw = path.read_text(encoding="utf-8")
                previous = json.loads(previous_raw)
                history = self.folder / "history" / result["id"]
                history.mkdir(parents=True, exist_ok=True)
                prior = history / (str(previous["version"]) + ".json")
                if not prior.exists():
                    prior.write_text(previous_raw, encoding="utf-8", newline="\n")
            try:
                with open(temporary, "w", encoding="utf-8", newline="\n") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
            self.store.record_event(result["id"], kind,
                                    {"actor": actor, "case_id": result["case_id"], "version": result["version"],
                                     "status": result["status"], "analysis_sha256": hashlib.sha256(raw.encode()).hexdigest()})

    @staticmethod
    def _saved_hash_matches(raw, expected):
        # v2.0 on Windows saved CRLF while its audit digest used the LF string.
        # Accept that line-ending conversion; image and mask digests stay byte exact.
        return expected is not None and expected in {
            hashlib.sha256(raw).hexdigest(),
            hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest(),
        }

    def get(self, ident):
        path = self._path(ident)
        with self.lock:
            if not path.exists():
                raise KeyError(ident)
            raw = path.read_bytes()
            expected = next((e["data"]["analysis_sha256"] for e in reversed(self.store.events(ident))
                             if e["data"].get("analysis_sha256")), None)
            if not self._saved_hash_matches(raw, expected):
                raise ValueError("分析紀錄與保存時的雜湊不同；請恢復完整備份或重新分析。")
            return json.loads(raw.decode("utf-8"))

    def integrity(self, result):
        """Check current source compatibility without changing the historical record."""
        acute = (result.get("run_snapshot") or {}).get("acute") is True or (result.get("discussion") or {}).get("status") == "emergency"
        if result.get("run_id"):
            try:
                acute = acute or self.store.run(result["run_id"]).get("context", {}).get("acute") is True
            except KeyError:
                pass
        if acute:
            return {"source_checked": False, "source_current": None, "geometry_current": None,
                    "mask_valid": None, "audit_valid": None, "approvable": False,
                    "approval_blockers": ["已標示急性警訊，跳過一般影像檢核，不能核准一般分析或帶入一般報告。"],
                    "blocked_stages": [s["key"] for s in result.get("stages", []) if s["status"] == "blocked"],
                    "notice": "急性狀態優先；這次沒有讀取影像或遮罩作完整性檢查。"}
        issues, blockers = [], []
        source_current, geometry_current, mask_valid = True, True, None
        try:
            case = self.store.case(result["case_id"])
            hashes = [sha256(self._source_path(p)) for p in case["slices"]]
            source = result["source"]
            if hashes != source.get("slice_sha256") or len(hashes) != source.get("slice_count"):
                raise ValueError("來源影像已變更或尚未完成檢核，不能核准或帶入報告。")
            for key, value in (("modality", case["modality"]), ("synthetic", case.get("synthetic") is True),
                               ("is_hu", bool(case.get("is_hu")))):
                if source.get(key) != value:
                    raise ValueError("影像類型或來源宣告已變更，請重新分析。")
            geometry = case.get("geometry") or {"verified": False, "units": "voxel"}
            geometry_current = canonical(geometry) == canonical(source.get("geometry"))
            if not geometry_current:
                raise ValueError("空間幾何已變更，舊的三維尺寸不能帶入本次報告。")
            # Reference files are checked only if they contributed to these results.
            segmentation = result.get("segmentation") or {}
            reference_sha = ((result.get("comparison") or {}).get("segmentation") or {}).get("reference_sha256")
            supplied_sha = segmentation.get("label_sha256")
            if reference_sha or supplied_sha:
                reference_path = case.get("provided_labels") or case.get("reference_mask")
                if not reference_path:
                    raise ValueError("分析使用的參考標籤來源已遺失，請恢復來源或重新分析。")
                if sha256(self._source_path(reference_path)) != (supplied_sha or reference_sha):
                    raise ValueError("分析使用的參考標籤已變更，請重新分析及比對。")
        except (ValueError, KeyError, OSError, TypeError) as exc:
            source_current = False
            issues.append(str(exc)[:300])
        if result.get("segmentation"):
            try:
                self.mask(result["id"])
                mask_valid = True
            except (ValueError, KeyError, OSError, TypeError) as exc:
                mask_valid = False
                issues.append(str(exc)[:300])
        acute = (result.get("run_snapshot") or {}).get("acute") is True or (result.get("discussion") or {}).get("status") == "emergency"
        if result.get("run_id"):
            try:
                acute = acute or self.store.run(result["run_id"]).get("context", {}).get("acute") is True
            except KeyError:
                issues.append("原協作工作已遺失，無法核對來源狀態。")
        if acute:
            blockers.append("已標示急性警訊，不能核准一般分析或帶入一般報告。")
        for key, label in (("quality", "影像檢核"), ("recognition", "辨識／量測"), ("segmentation", "選定分割")):
            if result.get(key) is None:
                blockers.append(label + "尚未完成，請修正後重新分析。")
        audit_valid = self.store.verify()["valid"]
        if not audit_valid:
            issues.append("操作稽核鏈檢查未通過。")
        blockers = list(dict.fromkeys(issues + blockers))
        return {"source_checked": True, "source_current": source_current, "geometry_current": geometry_current,
                "mask_valid": mask_valid, "audit_valid": audit_valid,
                "approvable": not blockers, "approval_blockers": blockers,
                "blocked_stages": [s["key"] for s in result.get("stages", []) if s["status"] == "blocked"],
                "notice": "檢查目前來源是否符合保存的分析；不是臨床效能或外部可信簽章驗證。"}

    def assert_transferable(self, result):
        integrity = self.integrity(result)
        if not integrity["approvable"]:
            raise ValueError("；".join(integrity["approval_blockers"]))
        return integrity

    def list(self, case_id=None):
        if case_id is not None:
            case_id = self._text(case_id, 100, "病例代碼")
        with self.lock:
            results = []
            for path in self.folder.glob("*.json"):
                result = self.get(path.stem)
                if case_id is not None and result["case_id"] != case_id:
                    continue
                results.append({key: result.get(key) for key in
                                ("id", "case_id", "created_at", "updated_at", "status", "version", "settings", "review")})
            return sorted(results, key=lambda r: r["created_at"], reverse=True)

    def mask(self, ident):
        result = self.get(ident)
        metadata = result.get("segmentation")
        if not metadata or not metadata.get("mask_sha256"):
            raise ValueError("本次分析沒有有效分割遮罩。")
        path = self.folder / (ident + ".mask.npy")
        if not path.is_file() or sha256(path) != metadata["mask_sha256"]:
            raise ValueError("分割遮罩檔案遺失或雜湊不符。")
        case = self.store.case(result["case_id"])
        hashes = [sha256(self._source_path(p)) for p in case["slices"]]
        if hashes != result["source"].get("slice_sha256"):
            raise ValueError("目前病例影像與分析來源雜湊不同，不能疊合遮罩。")
        mask = np.load(path, allow_pickle=False)
        if mask.dtype != np.bool_ or list(mask.shape) != result["source"]["shape_zyx"]:
            raise ValueError("分割遮罩類型或尺寸與來源不同。")
        return mask

    def export(self, ident):
        result = self.get(ident)
        revisions = []
        history = self.folder / "history" / ident
        if history.is_dir():
            for path in sorted(history.glob("*.json"), key=lambda p: int(p.stem)):
                raw = path.read_bytes()
                expected = next((e["data"]["analysis_sha256"] for e in reversed(self.store.events(ident))
                                 if e["data"].get("version") == int(path.stem) and e["data"].get("analysis_sha256")), None)
                if not self._saved_hash_matches(raw, expected):
                    raise ValueError("分析歷史版本與保存雜湊不同，不能匯出混合證據。")
                prior = json.loads(raw.decode("utf-8"))
                revisions.append({k: prior.get(k) for k in ("version", "updated_at", "status", "settings", "discussion", "review")})
        return {"schema": "cardio-integrated-evidence/1", "analysis": result, "revisions": revisions,
                "current_source_integrity": self.integrity(result),
                "events": self.store.events(ident), "audit": self.store.verify(),
                "notice": "包含本機研究分析與人工紀錄，僅在授權範圍使用。"}

    @staticmethod
    def _stage(result, key, function):
        start = time.perf_counter()
        try:
            payload = function()
            stage = {"key": key, "status": "ready", "elapsed_ms": round((time.perf_counter() - start) * 1000)}
            result["stages"].append(stage)
            return payload
        except Exception as exc:
            result["stages"].append({"key": key, "status": "blocked",
                                     "elapsed_ms": round((time.perf_counter() - start) * 1000),
                                     "reason": str(exc)[:500]})
            return None

    def _load(self, case):
        slices = case.get("slices")
        if not isinstance(slices, list) or not 1 <= len(slices) <= 64:
            raise ValueError("序列須含 1–64 張切片。")
        arrays, hashes, pixels = [], [], 0
        for relative in slices:
            path = self._source_path(relative)
            a = np.load(path, allow_pickle=False, mmap_mode="r")
            if a.ndim != 2 or min(a.shape) < 8 or max(a.shape) > 4096 or a.dtype.kind not in "uif":
                raise ValueError("僅接受有限大小的二維數值切片。")
            pixels += a.size
            if pixels > 32_000_000:
                raise ValueError("體積像素上限為 3,200 萬，請分段分析。")
            if not np.isfinite(a).all():
                raise ValueError("來源影像含非有限像素。")
            arrays.append(a)
            hashes.append(sha256(path))
        return arrays, hashes

    @staticmethod
    def _quality(arrays, case):
        measured = quality(arrays)
        count, mean, m2 = 0, 0., 0.
        low, high = float("inf"), float("-inf")
        for a in arrays:
            n = a.size
            amean, avar = float(np.mean(a, dtype=np.float64)), float(np.var(a, dtype=np.float64))
            delta = amean - mean
            merged = count + n
            m2 += avar * n + delta * delta * count * n / merged
            mean += delta * n / merged
            count = merged
            low, high = min(low, float(a.min())), max(high, float(a.max()))
        stats = {"minimum": low, "maximum": high, "mean": mean, "std": math.sqrt(m2 / count),
                 "pixels": count, "unit": "HU" if case.get("is_hu") else "display_intensity_not_HU"}
        if not all(math.isfinite(stats[k]) for k in ("minimum", "maximum", "mean", "std")):
            raise ValueError("影像強度統計超出有效範圍。")
        return measured | {"intensity": stats, "scope": "pixel_readability_not_diagnostic_image_quality"}

    def _recognize(self, arrays, case, settings):
        if settings["mode"] == "inspect":
            return {"candidates": [], "engine": "measured image inspection", "trained": False, "device": "CPU",
                    "inference_executed": False, "scope": "image_inspection_only",
                    "warning": "僅量測像素與檢視閾值區域，未辨識冠狀動脈、狹窄或病灶。"}
        if case["modality"] != "CT":
            raise ValueError("此為 MRI／非 CT；不可使用冠狀動脈 CT 候選模型。")
        if settings["mode"] == "demo":
            found = demo_candidates(arrays[settings["center"]])
        else:
            found = self.detector.detect(arrays, settings["center"], case.get("is_hu", False))
        return found | {"inference_executed": settings["mode"] == "model",
                        "scope": "selected_slice_candidate_localization", "slice_index": settings["center"],
                        "whole_volume_detection": False}

    @staticmethod
    def _explain(result):
        recognition = result.get("recognition")
        if recognition is None:
            return {"kind": "source_facts_not_saliency", "summary": "候選辨識受阻，沒有可用的模型定位結果。",
                    "facts": [{"id": "recognition", "text": "模型未產生結果，不以合成框補上。"}],
                    "saliency_available": False, "anatomical_interpretation": False}
        facts = [{"id": "quality", "text": f"本次序列共 {result['source']['slice_count']} 張，中心切片為第 {result['settings']['center'] + 1} 張。"},
                 {"id": "recognition", "text": f"來源：{recognition['engine']}；候選 {len(recognition['candidates'])} 個；適用範圍：{recognition['scope']}。"}]
        for i, candidate in enumerate(recognition["candidates"], 1):
            facts.append({"id": f"candidate-{i}", "text": f"候選 {i} 的像素框：{candidate['xyxy']}；信心值：" +
                          ("不適用（合成區域）" if candidate.get("confidence") is None else str(candidate["confidence"])),
                          "xyxy": candidate["xyxy"], "confidence": candidate.get("confidence")})
        if recognition["scope"] == "image_inspection_only":
            segmentation_mode = result["settings"]["segmentation_mode"]
            if segmentation_mode == "provided_reference":
                summary = "本次為研究影像量測與來源參考標籤重建；未執行病灶辨識或已訓練模型推論。"
            elif segmentation_mode == "registered_model":
                summary = "本次為影像量測與選定分割模型檢核；未執行冠狀動脈候選辨識。"
            else:
                summary = "本次為影像量測與閾值預覽；沒有執行病灶辨識。"
        else:
            summary = f"已依來源紀錄整理 {len(recognition['candidates'])} 個中心切片候選；需對照原圖人工覆核。"
        return {"kind": "source_facts_not_saliency", "summary": summary, "facts": facts,
                "saliency_available": False, "anatomical_interpretation": False,
                "notice": "這是定位框與執行證據解釋，不是 Grad-CAM、注意力圖或病理因果解釋。"}

    def _comparison(self, result, arrays, case, compare_to, selected_mask=None):
        detection = {"available": False, "precision": None, "recall": None, "f1": None,
                     "reason": "未提供此切片的獨立參考框；不產生辨識準確度。"}
        recognition = result.get("recognition")
        reference = case.get("reference_boxes", {}).get(str(result["settings"]["center"]))
        if recognition and recognition["scope"] != "image_inspection_only" and case.get("synthetic") and reference is not None:
            detection = compare_boxes(recognition["candidates"], reference)
        segmentation = {"available": False, "dice": None, "iou": None,
                        "reason": "未提供獨立分割遮罩；閾值區域不等同已標註解剖。"}
        if result["settings"]["segmentation_mode"] == "provided_reference":
            segmentation["reason"] = "參考標籤作重建來源，不能對同一標籤回算準確率。"
            segmentation["scope"] = "provided_reference_reconstruction_no_self_scoring"
        elif selected_mask is not None and case.get("provided_labels"):
            from .reference_labels import load_reference_mask
            supplied = load_reference_mask(self.data, case, result["settings"]["reference_class"])
            segmentation = compare_masks(selected_mask, supplied["mask"]) | {
                "reference_sha256": supplied["metadata"]["label_sha256"],
                "reference_class": result["settings"]["reference_class"],
                "scope": "provided_research_label_agreement_not_clinical_accuracy",
                "reference_use": "independent_evaluation_only_not_used_for_selected_segmentation",
                "notice": "僅比較本次分割與來源研究標籤的一致性；類別映射、標註者及模型訓練資料獨立性未確認，不代表獨立測試集或臨床效能。"}
        elif selected_mask is not None and case.get("synthetic") is True and case.get("reference_mask"):
            reference_path = self._source_path(case["reference_mask"])
            actual_hash = sha256(reference_path)
            expected_hash = (case.get("reference_model") or {}).get("reference_sha256")
            if expected_hash and expected_hash != actual_hash:
                raise ValueError("合成參考遮罩 SHA256 不符，不計算參考一致性。")
            truth = np.load(reference_path, allow_pickle=False)
            segmentation = compare_masks(selected_mask, truth) | {"reference_sha256": actual_hash}
        previous = {"available": False, "reason": "未選擇前次分析；本次不推定跨次疾病變化。"}
        if compare_to:
            prior = self.get(compare_to)
            comparable = prior["case_id"] == result["case_id"] and prior["source"].get("slice_sha256") == result["source"].get("slice_sha256") and prior["settings"]["center"] == result["settings"]["center"]
            if not comparable:
                previous = {"available": False, "analysis_id": compare_to,
                            "reason": "病例、影像雜湊或切片不一致，禁止直接數值相減。"}
            elif prior.get("recognition") is None or recognition is None:
                previous = {"available": False, "analysis_id": compare_to, "candidate_count_delta": None,
                            "reason": "其中一筆候選辨識受阻，未量測結果不能視為零候選。"}
            elif prior["recognition"].get("scope") == "image_inspection_only" or recognition.get("scope") == "image_inspection_only":
                previous = {"available": False, "analysis_id": compare_to, "candidate_count_delta": None,
                            "reason": "未執行可比較候選辨識，影像檢視不回報診斷候選數差異。"}
            else:
                old_count = len(prior["recognition"]["candidates"])
                new_count = len(recognition["candidates"])
                previous = {"available": True, "analysis_id": compare_to,
                            "candidate_count_delta": new_count - old_count,
                            "same_settings": all(prior["settings"].get(k, "threshold" if k == "segmentation_mode" else 0 if k == "reference_class" else None) == result["settings"].get(k) for k in ("mode", "center", "threshold", "segmentation_mode", "reference_class")),
                            "scope": "same_source_software_configuration_comparison_not_disease_progression",
                            "notice": "同一來源影像的軟體設定比較，不是病程或療效評估。"}
        return {"detection": detection, "segmentation": segmentation, "prior": previous}

    @staticmethod
    def _graph(result):
        nodes = [{"id": "image", "label": "來源影像", "kind": "source", "detail": result["source"]}]
        edges = []
        for key, label in (("quality", "影像檢核"), ("recognition", "候選／量測"), ("segmentation", "分割來源"), ("comparison", "參考比對"), ("reconstruction", "表面 3D")):
            stage = next((s for s in result["stages"] if s["key"] == key), None)
            nodes.append({"id": key, "label": label, "kind": "tool", "detail": stage or {"status": "not_run"}})
            edges.append({"source": "image", "target": key, "label": "相同來源"})
        nodes.extend([{"id": "explanation", "label": "證據解釋", "kind": "explanation", "detail": {"saliency": False}},
                      {"id": "review", "label": "人工覆核", "kind": "approval", "detail": {"status": result["status"]}}])
        edges.extend([{"source": "recognition", "target": "explanation", "label": "定位事實"},
                      {"source": "comparison", "target": "explanation", "label": "已知參考"},
                      {"source": "reconstruction", "target": "review", "label": "預覽待核對"},
                      {"source": "explanation", "target": "review", "label": "需人工判讀"}])
        return {"nodes": nodes, "edges": edges, "kind": "provenance_graph_not_causal_medical_graph"}

    def _language(self, result, use_llm, model_id):
        start = time.perf_counter()
        text = (result.get("explanation") or {}).get("summary", "影像分析受阻，請先核對來源與執行紀錄。")
        output = {"source": "rules", "summary": text + " 所有內容待人工覆核。", "model": None,
                  "source_ids": [s["key"] for s in result["stages"] if s["key"] in SOURCE_IDS and s["status"] == "ready"],
                  "clinical_recommendation": False, "human_review_required": True}
        if use_llm:
            try:
                if not result["source"]["synthetic"]:
                    raise ValueError("本版僅向語言模型傳送合成分析旗標；研究或匯入影像採規則摘要。")
                if not model_id:
                    raise ValueError("尚未選擇已下載的本機語言模型。")
                model = self.planner.local_model(model_id)
                facts = {"synthetic": True, "source_ids": output["source_ids"],
                         "candidate_count": len((result.get("recognition") or {}).get("candidates", [])),
                         "inference_executed": (result.get("recognition") or {}).get("inference_executed", False),
                         "reconstruction_available": result.get("reconstruction") is not None,
                         "geometry_unit": result["source"].get("geometry", {}).get("units", "voxel"),
                         "stages": [{"key": s["key"], "status": s["status"]} for s in result["stages"]]}
                payload = {"model": model_id, "messages": [
                    {"role": "system", "content": 'Explain ONLY these synthetic software workflow facts in Traditional Chinese. No anatomy inference, diagnosis, treatment or surgery recommendations. Mention human review and synthetic scope. Return JSON only: {"summary":"...","source_ids":["..."]}. Use only supplied source_ids.'},
                    {"role": "user", "content": json.dumps(facts, ensure_ascii=False)}],
                    "temperature": 0, "max_tokens": 350, "stream": False}
                response = self.planner.request("/chat/completions", payload, timeout=self.planner.config.get("planner_timeout_s", 45))
                parsed = json.loads(response["choices"][0]["message"]["content"])
                if set(parsed) != {"summary", "source_ids"} or not isinstance(parsed["summary"], str) or not 1 <= len(parsed["summary"]) <= 2000 or not isinstance(parsed["source_ids"], list) or not all(isinstance(s, str) and s in output["source_ids"] for s in parsed["source_ids"]):
                    raise ValueError("語言模型回應的結構或來源識別無效。")
                output.update(source="lemonade", summary=parsed["summary"], source_ids=parsed["source_ids"],
                              model=model_id, recipe=model["recipe"], response_id=response.get("id"))
            except Exception as exc:
                output.update(source="rules_fallback", reason=str(exc)[:300])
        output["elapsed_ms"] = round((time.perf_counter() - start) * 1000)
        return output

    def execute(self, body, actor):
        if not isinstance(body, dict):
            raise ValueError("分析輸入須為 JSON 物件。")
        allowed = {"case_id", "run_id", "mode", "center", "threshold", "segmentation_mode",
                   "reference_class", "compare_to", "use_llm", "model"}
        if set(body) - allowed:
            raise ValueError("分析包含未定義欄位，請核對辨識與分割設定後再送出。")
        case_id = self._text(body.get("case_id"), 100, "病例代碼")
        actor = self._text(actor, 80, "操作帳號")
        case = self.store.case(case_id)
        mode = body.get("mode", "inspect")
        if mode not in {"demo", "model", "inspect"}:
            raise ValueError("分析模式只接受 demo、model 或 inspect。")
        segmentation_mode = body.get("segmentation_mode", "threshold")
        if segmentation_mode not in {"threshold", "registered_model", "provided_reference"}:
            raise ValueError("分割模式只接受 threshold、registered_model 或 provided_reference。")
        reference_class = body.get("reference_class", 0)
        if type(reference_class) is not int or reference_class not in (0, 1, 2, 3):
            raise ValueError("參考類別只接受 0（1–3 聯集）、1、2 或 3。")
        if mode == "demo" and not case.get("synthetic"):
            raise ValueError("合成辨識只適用合成資料，不可對研究或匯入影像產生假候選。")
        slice_count = len(case.get("slices", []))
        center = body.get("center", slice_count // 2)
        if type(center) is not int or not 0 <= center < slice_count:
            raise ValueError("中心切片索引無效。")
        threshold = body.get("threshold", 120 if case.get("reference_mask") else 240 if case.get("synthetic") else 128)
        if type(threshold) not in {int, float} or not math.isfinite(threshold) or not -32768 <= threshold <= 65535:
            raise ValueError("閾值須為 -32768–65535 的有限數值。")
        use_llm = body.get("use_llm", False)
        if type(use_llm) is not bool:
            raise ValueError("use_llm 須為布林值。")
        model_id = self._text(body.get("model", self.planner.config.get("planner_model", "")), 180, "語言模型代碼", empty=True)
        run_id = body.get("run_id") or None
        linked = None
        if run_id is not None:
            run_id = self._text(run_id, 100, "協作工作識別碼")
            linked = self.store.run(run_id)
            if linked["case_id"] != case_id:
                raise ValueError("分析與協作工作病例不一致。")
        compare_to = body.get("compare_to") or None
        if compare_to is not None:
            self._path(compare_to)
        ident = uuid.uuid4().hex
        created = now()
        result = {"schema": "cardio-integrated-analysis/1", "software_version": __version__, "id": ident, "case_id": case_id, "run_id": run_id,
                  "version": 1, "status": "pending_review", "created_at": created, "updated_at": created, "actor": actor,
                  "settings": {"mode": mode, "center": center, "threshold": float(threshold), "segmentation_mode": segmentation_mode, "reference_class": reference_class, "use_llm": use_llm,
                               "model": model_id, "compare_to": compare_to},
                  "run_snapshot": ({k: linked.get(k) for k in ("id", "version", "status", "updated_at")} |
                                   {"acute": linked.get("context", {}).get("acute")}) if linked else None,
                  "source": {"synthetic": case.get("synthetic", False) is True, "modality": case["modality"],
                             "slice_count": slice_count, "shape_zyx": None, "slice_sha256": [], "is_hu": bool(case.get("is_hu")),
                             "geometry": copy.deepcopy(case.get("geometry") or {"verified": False, "units": "voxel"})},
                  "stages": [], "quality": None, "recognition": None, "explanation": None,
                  "comparison": None, "segmentation": None, "reconstruction": None, "discussion": None, "review": None,
                  "notice": "研究與競賽分析；候選、閾值模型與人工紀錄須分別覆核，不作自動診斷或手術決策。",
                  "amd_hardware_verified": False, "npu_execution_verified": False}
        if linked and linked.get("context", {}).get("acute") is True:
            for key in ("quality", "recognition", "explanation", "segmentation", "comparison", "reconstruction", "language"):
                result["stages"].append({"key": key, "status": "blocked", "elapsed_ms": 0,
                                         "reason": "已標示急性警訊；優先由人員處理，跳過一般影像與模型等待。"})
            result["language"] = {"source": "rules", "summary": "已標示急性警訊，未執行一般分析。", "model": None,
                                  "elapsed_ms": 0, "clinical_recommendation": False, "human_review_required": True}
            result["graph"] = self._graph(result)
            self._save(result, "analysis_acute_blocked", actor)
            return result

        arrays, hashes, selected_mask = [], [], None
        def load_quality():
            nonlocal arrays, hashes
            arrays, hashes = self._load(case)
            result["source"].update(shape_zyx=[len(arrays), *arrays[0].shape], slice_sha256=hashes)
            if linked and linked.get("snapshot", {}).get("slice_sha256") != hashes:
                raise ValueError("影像雜湊與協作工作不同，請建立新的工作後分析。")
            return self._quality(arrays, case)
        result["quality"] = self._stage(result, "quality", load_quality)
        if result["quality"] is not None:
            result["recognition"] = self._stage(result, "recognition", lambda: self._recognize(arrays, case, result["settings"]))
            result["explanation"] = self._stage(result, "explanation", lambda: self._explain(result))
            def segment():
                nonlocal selected_mask
                if segmentation_mode == "provided_reference":
                    from .reference_labels import load_reference_mask
                    supplied = load_reference_mask(self.data, case, reference_class)
                    selected_mask = supplied["mask"]
                    metadata = copy.deepcopy(supplied["metadata"])
                elif segmentation_mode == "registered_model":
                    if self.segmenter is None:
                        from .segmentation import Segmenter
                        self.segmenter = Segmenter(self.root, self.planner.config)
                    segmented = self.segmenter.segment(arrays, case)
                    selected_mask = segmented["mask"]
                    metadata = copy.deepcopy(segmented["metadata"])
                else:
                    selected_mask = np.stack([a >= float(threshold) for a in arrays])
                    metadata = {"method": "intensity_threshold", "threshold": float(threshold),
                                "comparison": "greater_or_equal", "trained": False,
                                "anatomical_label": None, "accuracy_verified": False}
                if not isinstance(selected_mask, np.ndarray) or selected_mask.dtype != np.bool_ or list(selected_mask.shape) != result["source"]["shape_zyx"]:
                    selected_mask = None
                    raise ValueError("分割工具須回傳與原始序列同尺寸的 bool 遮罩。")
                path = self.folder / (ident + ".mask.npy")
                temporary = path.with_suffix(".tmp")
                try:
                    with open(temporary, "wb") as stream:
                        np.save(stream, selected_mask, allow_pickle=False)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, path)
                finally:
                    temporary.unlink(missing_ok=True)
                return metadata | {"mask_sha256": sha256(path), "shape_zyx": list(selected_mask.shape),
                                   "foreground_voxels": int(np.count_nonzero(selected_mask)),
                                   "mask_scope": "research_preview_not_validated_anatomy"}
            result["segmentation"] = self._stage(result, "segmentation", segment)
            if result["segmentation"] is None:
                selected_mask = None
            result["comparison"] = self._stage(result, "comparison", lambda: self._comparison(result, arrays, case, compare_to, selected_mask))
            def reconstruct():
                from .volume import reconstruct as volume_reconstruct
                if result["segmentation"] is None or selected_mask is None:
                    raise ValueError("選定分割工具未產生有效遮罩，不改用其他分割來源。")
                if segmentation_mode in {"registered_model", "provided_reference"}:
                    return volume_reconstruct(arrays, case, float(threshold), max_axis=64,
                                              mask=selected_mask, mask_metadata=result["segmentation"])
                return volume_reconstruct(arrays, case, float(threshold), max_axis=64)
            result["reconstruction"] = self._stage(result, "reconstruction", reconstruct)
        else:
            for key in ("recognition", "explanation", "segmentation", "comparison", "reconstruction"):
                result["stages"].append({"key": key, "status": "blocked", "elapsed_ms": 0,
                                         "reason": "影像來源檢核未通過，未執行下游工具。"})
        result["language"] = self._stage(result, "language", lambda: self._language(result, use_llm and result["quality"] is not None, model_id))
        result["graph"] = self._graph(result)
        result["updated_at"] = now()
        self._save(result, "analysis_completed", actor)
        return result

    def attach_discussion(self, ident, discussion, actor, version):
        begin = time.perf_counter()
        actor = self._text(actor, 80, "操作帳號")
        if not isinstance(discussion, dict):
            raise ValueError("術前討論須為結構化物件。")
        recommendation = discussion.get("recommendation", {})
        if not isinstance(recommendation, dict):
            raise ValueError("處置討論結構無效。")
        if (discussion.get("procedure") is not None or recommendation.get("procedure") is not None or
                discussion.get("surgery_planning_ready") is True or recommendation.get("surgery_planning_ready") is True):
            raise ValueError("此分析只接受專業討論清單，不能附加自動手術選擇或宣稱手術就緒。")
        # Reject objects that cannot be durably serialized before changing state.
        json.dumps(discussion, ensure_ascii=False, allow_nan=False)
        with self.lock:
            result = self.get(ident)
            if result["status"] != "pending_review" or type(version) is not int or version != result["version"]:
                raise ValueError("分析已覆核或版本已變更，禁止覆寫術前討論。")
            result["discussion"] = copy.deepcopy(discussion)
            result["version"] += 1
            result["updated_at"] = now()
            result["stages"] = [s for s in result["stages"] if s["key"] != "discussion"]
            result["stages"].append({"key": "discussion", "status": "ready", "elapsed_ms": round((time.perf_counter() - begin) * 1000),
                                     "timing_scope": "attachment_only", "scope": "source_linked_mdt_checklist_not_procedure_selection"})
            result["graph"]["nodes"] = [n for n in result["graph"]["nodes"] if n["id"] != "discussion"]
            result["graph"]["edges"] = [e for e in result["graph"]["edges"] if e["target"] != "discussion"]
            result["graph"]["nodes"].append({"id": "discussion", "label": "術前討論清單", "kind": "discussion",
                                             "detail": {"status": discussion.get("status"), "procedure": None}})
            result["graph"]["edges"].append({"source": "review", "target": "discussion", "label": "由專業團隊討論"})
            self._save(result, "analysis_discussion_attached", actor)
            return result

    def review(self, ident, body, actor):
        if not isinstance(body, dict):
            raise ValueError("覆核須為 JSON 物件。")
        actor = self._text(actor, 80, "覆核帳號")
        reason = self._text(body.get("reason", ""), 1000, "覆核說明", empty=True)
        decision = body.get("decision")
        if decision not in {"approve", "reject"}:
            raise ValueError("需選擇核准或退回。")
        if decision == "reject" and not reason:
            raise ValueError("退回須提供原因。")
        if decision == "approve" and body.get("review_confirmed") is not True:
            raise ValueError("請明確勾選已核對本次病例、影像與分析的覆核確認。")
        with self.lock:
            result = self.get(ident)
            if result["status"] != "pending_review" or type(body.get("version")) is not int or body["version"] != result["version"]:
                raise ValueError("分析已覆核或版本已變更。")
            integrity = self.assert_transferable(result) if decision == "approve" else None
            result["status"] = "approved" if decision == "approve" else "rejected"
            result["review"] = {"decision": decision, "actor": actor, "reason": reason, "at": now(),
                                "review_confirmed": decision == "approve", "source_integrity_checked": integrity,
                                "scope": "analysis_evidence_review_not_treatment_authorization"}
            result["version"] += 1
            result["updated_at"] = now()
            for node in result["graph"]["nodes"]:
                if node["id"] == "review":
                    node["detail"] = {"status": result["status"], "review": result["review"]}
            self._save(result, "analysis_human_review", actor)
            return result
