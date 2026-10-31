"""Explicitly registered per-slice ONNX segmentation, without fallback or resize."""
import json
from pathlib import Path
import re
import threading
import time

import numpy as np

from .imaging import sha256


def validate_manifest(value):
    if not isinstance(value, dict):
        raise ValueError("分割模型資訊須為 JSON 物件。")
    manifest = dict(value)
    for key, limit in (("name", 120), ("provenance", 8000)):
        if not isinstance(manifest.get(key), str) or not manifest[key].strip() or len(manifest[key]) > limit:
            raise ValueError(f"分割模型 {key} 必須是非空文字，長度上限 {limit}。")
    file = manifest.get("file")
    if not isinstance(file, str) or not file or Path(file).name != file or not file.lower().endswith(".onnx"):
        raise ValueError("分割模型檔名須為 models/ 內的 ONNX 檔。")
    if not isinstance(manifest.get("sha256"), str) or not re.fullmatch(r"[a-fA-F0-9]{64}", manifest["sha256"]):
        raise ValueError("分割模型 SHA256 格式無效。")
    manifest["sha256"] = manifest["sha256"].lower()
    if manifest.get("trained") is not True:
        raise ValueError("分割模型需明確聲明已訓練來源；此聲明不等於臨床驗證。")
    if manifest.get("modality") not in {"CT", "MRI"}:
        raise ValueError("分割模型影像類型只接受 CT 或 MRI。")
    size = manifest.get("input_size")
    if not isinstance(size, list) or len(size) != 2 or any(type(n) is not int or not 1 <= n <= 2048 for n in size):
        raise ValueError("分割模型 input_size 須為 1–2048 的 [height,width] 整數。")
    labels = manifest.get("class_labels")
    if (not isinstance(labels, list) or not 2 <= len(labels) <= 64 or
            any(not isinstance(s, str) or not s.strip() or len(s) > 100 for s in labels) or
            len(set(labels)) != len(labels)):
        raise ValueError("分割模型 class_labels 須含 2–64 個不重複的非空類別名稱。")
    if len(labels) * size[0] * size[1] > 16_000_000:
        raise ValueError("分割模型單片輸出不得超過 1600 萬個 logits。")
    target = manifest.get("target_class")
    if type(target) is not int or not 0 <= target < len(labels):
        raise ValueError("分割目標類別索引超出 class_labels 範圍。")
    window = manifest.get("window")
    if (not isinstance(window, list) or len(window) != 2 or
            any(type(n) not in (int, float) or not np.isfinite(n) for n in window) or
            not -1e6 <= window[0] < window[1] <= 1e6):
        raise ValueError("分割模型 window 須為有限且遞增的 [low,high]。")
    if manifest.get("output") != "logits-nchw":
        raise ValueError("分割模型輸出契約必須是 logits-nchw。")
    manifest["window"] = [float(n) for n in window]
    return manifest


def validate_model(model_path, manifest, provider="CPUExecutionProvider"):
    """Validate the declared contract with ORT and a bounded zero-input run."""
    manifest = validate_manifest(manifest)
    try:
        import onnxruntime as ort
    except ImportError as exc:
        raise ValueError("分割推論需要選用的 ONNX Runtime；請先安裝影像依賴。") from exc
    if not isinstance(provider, str) or provider not in ort.get_available_providers():
        raise ValueError(f"分割模型指定的執行器不可用：{provider}。")
    try:
        options = ort.SessionOptions()
        options.intra_op_num_threads = 4
        options.enable_mem_pattern = False
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        session = ort.InferenceSession(str(model_path), sess_options=options, providers=[provider])
        if provider not in session.get_providers():
            raise ValueError("分割執行器載入失敗，未接受靜默裝置接續。")
        h, w = manifest["input_size"]
        classes = len(manifest["class_labels"])
        inputs, outputs = session.get_inputs(), session.get_outputs()
        if len(inputs) != 1 or inputs[0].shape != [1, 1, h, w] or inputs[0].type != "tensor(float)":
            raise ValueError("分割輸入須為單一固定 float32 [1,1,H,W]，不可隱含縮放。")
        if len(outputs) != 1 or outputs[0].shape != [1, classes, h, w] or outputs[0].type != "tensor(float)":
            raise ValueError("分割輸出須為單一固定 float32 [1,C,H,W]，C 對應 class_labels。")
        result = session.run(None, {inputs[0].name: np.zeros((1, 1, h, w), np.float32)})
        if (len(result) != 1 or result[0].shape != (1, classes, h, w) or
                not np.isfinite(result[0]).all()):
            raise ValueError("分割模型試跑輸出形狀或數值無效。")
        return session
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("分割模型載入或契約試跑失敗：" + str(exc)[:350]) from exc


class Segmenter:
    def __init__(self, root, config):
        self.root = Path(root)
        self.config = config
        self.session = None
        self.session_key = None
        self.lock = threading.RLock()

    def manifest(self):
        directory = (self.root / "models").resolve()
        manifest_file = directory / "segmentation.json"
        if not manifest_file.is_file():
            raise ValueError("尚未登錄解剖分割模型。請使用 scripts/register_segmentation.py；未改用閾值冒充模型。")
        try:
            manifest = validate_manifest(json.loads(manifest_file.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("分割模型資訊檔無法讀取。") from exc
        model = (directory / manifest["file"]).resolve()
        if not model.is_relative_to(directory) or not model.is_file():
            raise ValueError("分割模型路徑不存在或超出 models/。")
        if sha256(model) != manifest["sha256"]:
            raise ValueError("分割模型 SHA256 不符，拒絕執行。")
        return manifest, model

    def segment(self, arrays, case):
        if not isinstance(case, dict):
            raise ValueError("請提供本次影像類型與來源。")
        if (not isinstance(arrays, (list, tuple, np.ndarray)) or
                (isinstance(arrays, np.ndarray) and arrays.ndim == 0) or not 1 <= len(arrays) <= 128):
            raise ValueError("分割序列須有 1–128 張有序切片。")
        try:
            volume = np.asarray(arrays, dtype=np.float32)
        except (TypeError, ValueError) as exc:
            raise ValueError("分割序列須為相同尺寸的數值影像。") from exc
        if volume.ndim != 3 or volume.size > 32_000_000 or not np.isfinite(volume).all():
            raise ValueError("分割序列須為有限的 [D,H,W]，總量不超過 3200 萬體素。")
        manifest, path = self.manifest()
        if case.get("modality") != manifest["modality"]:
            raise ValueError("本次影像類型與已登錄的分割模型不一致。")
        if list(volume.shape[1:]) != manifest["input_size"]:
            raise ValueError("影像 H/W 與分割模型不符；未進行隱含縮放。")
        is_hu = case.get("is_hu", False)
        if type(is_hu) is not bool:
            raise ValueError("is_hu 須為布林值。")
        if not is_hu and (float(volume.min()) < 0 or float(volume.max()) > 255):
            raise ValueError("非 HU 分割輸入須為 8-bit 匯出範圍 0–255。")
        provider = self.config.get("segmentation_provider", self.config.get("vision_provider", "CPUExecutionProvider"))
        key = (manifest["sha256"], provider)
        begin = time.perf_counter()
        with self.lock:
            if self.session is None or self.session_key != key:
                self.session = validate_model(path, manifest, provider)
                self.session_key = key
            session = self.session
        inp = session.get_inputs()[0]
        classes = len(manifest["class_labels"])
        expected = (1, classes, *manifest["input_size"])
        low, high = manifest["window"]
        mask = np.zeros(volume.shape, dtype=bool)
        try:
            for i, image in enumerate(volume):
                tensor = np.clip((image - low) / (high - low), 0, 1).astype(np.float32)[None, None]
                pred = session.run(None, {inp.name: tensor})[0]
                if pred.shape != expected or pred.dtype != np.float32 or not np.isfinite(pred).all():
                    raise ValueError("分割模型輸出形狀、型別或數值無效。")
                mask[i] = np.argmax(pred[0], axis=0) == manifest["target_class"]
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("分割模型推論失敗：" + str(exc)[:350]) from exc
        return {"mask": mask, "metadata": {
            "method": "registered_onnx", "model": manifest["name"], "sha256": manifest["sha256"],
            "trained_declared": True, "accuracy_verified": False, "anatomy_verified": False,
            "provenance": manifest["provenance"], "modality": manifest["modality"],
            "provider": session.get_providers(), "requested_provider": provider,
            "target_class": manifest["target_class"], "anatomical_label": manifest["class_labels"][manifest["target_class"]],
            "class_labels": manifest["class_labels"], "input_size": manifest["input_size"],
            "preprocessing": {"window": manifest["window"], "normalization": "clip((pixel-low)/(high-low),0,1)",
                              "input": "float32 NCHW [1,1,H,W]", "resizing": False, "is_hu": is_hu},
            "decision": "argmax multiclass logits then exact target class", "threshold": None,
            "slice_count": len(volume), "elapsed_ms": round((time.perf_counter() - begin) * 1000),
            "synthetic_input": case.get("synthetic", False) is True,
            "notice": "模型及類別名稱依登錄來源聲明；本平台未驗證其解剖或臨床準確度。",
        }}
