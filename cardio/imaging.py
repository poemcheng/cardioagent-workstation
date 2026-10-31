import hashlib
import io
import json
from pathlib import Path
import numpy as np
from PIL import Image

Image.MAX_IMAGE_PIXELS = 16_000_000


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_upload(raw, filename, declared):
    """Return pixels without retaining raw DICOM headers or a user filename."""
    if filename.lower().endswith(".dcm"):
        import pydicom
        ds = pydicom.dcmread(io.BytesIO(raw))
        if ds.get("NumberOfFrames", 1) != 1:
            raise ValueError("僅接受單張 DICOM；請將多影格拆成有順序的序列。")
        if not 32 <= int(ds.get("Rows", 0)) <= 4096 or not 32 <= int(ds.get("Columns", 0)) <= 4096 or ds.get("SamplesPerPixel", 1) != 1:
            raise ValueError("DICOM 必須為 32–4096 的單色二維影像。")
        modality = str(ds.get("Modality", "UNKNOWN"))
        modality = "MRI" if modality == "MR" else modality
        if declared != "UNKNOWN" and modality != declared:
            raise ValueError("宣告的影像類型與 DICOM Modality 不符。")
        arr = ds.pixel_array
        if arr.ndim != 2 or arr.size > 16_000_000:
            raise ValueError("僅支援二維單色 DICOM。")
        pixels = arr.astype(np.float32)
        slope, intercept = ds.get("RescaleSlope"), ds.get("RescaleIntercept")
        is_hu = modality == "CT" and slope is not None and intercept is not None
        if is_hu:
            pixels = pixels * float(slope) + float(intercept)
        # Retain only numeric geometry and one-way UID digests. Patient names,
        # IDs, dates, free text, filenames, and complete headers are discarded.
        def geometry_numbers(keyword, count):
            try:
                values = [float(v) for v in ds.get(keyword, [])]
                return values if len(values) == count and np.isfinite(values).all() else None
            except (TypeError, ValueError, OverflowError):
                return None
        def uid_digest(keyword):
            value = str(ds.get(keyword, "")).strip()
            return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else None
        pixel_spacing = geometry_numbers("PixelSpacing", 2)
        geometry = {"pixel_spacing_yx": pixel_spacing,
                    "position_xyz": geometry_numbers("ImagePositionPatient", 3),
                    "orientation": geometry_numbers("ImageOrientationPatient", 6),
                    "series_hash": uid_digest("SeriesInstanceUID"),
                    "study_hash": uid_digest("StudyInstanceUID"),
                    "frame_hash": uid_digest("FrameOfReferenceUID"),
                    "rows": int(ds.Rows), "columns": int(ds.Columns)}
        meta = {"modality": modality, "is_hu": is_hu,
                "spacing_mm": pixel_spacing or [], "geometry": geometry,
                "source": "DICOM; only minimal geometry and hashed UIDs retained"}
    else:
        img = Image.open(io.BytesIO(raw))
        if img.format not in {"PNG", "JPEG"}:
            raise ValueError("僅接受 PNG、JPEG、DICOM。")
        if img.mode not in {"L", "RGB", "RGBA"}:
            raise ValueError("匯出影像需為 8-bit 灰階或 RGB；16-bit 影像須先依訓練窗位轉換。")
        img.load()
        pixels = np.asarray(img.convert("L"), dtype=np.float32)
        meta = {"modality": declared, "is_hu": False, "spacing_mm": [], "source": "image export; modality declared by operator"}
    if pixels.ndim != 2 or min(pixels.shape) < 32 or max(pixels.shape) > 4096 or not np.isfinite(pixels).all():
        raise ValueError("影像尺寸須介於 32–4096，像素須為有限值。")
    return pixels, meta


def display_pixels(arr, is_hu=False, center=40, width=400):
    if is_hu:
        low, high = center - width / 2, center + width / 2
    else:
        low, high = float(arr.min()), float(arr.max())
    return (np.clip((arr - low) / max(high - low, 1e-8), 0, 1) * 255).astype(np.uint8)


def png_bytes(arr, **kwargs):
    out = io.BytesIO()
    Image.fromarray(display_pixels(arr, **kwargs)).save(out, format="PNG")
    return out.getvalue()


def quality(arrays):
    if len({a.shape for a in arrays}) != 1:
        raise ValueError("同一序列的影像尺寸不一致。")
    if any(not np.isfinite(a).all() or np.std(a) < 1e-6 for a in arrays):
        raise ValueError("影像含無效像素或無灰階變異。")
    return {"readable": True, "slices": len(arrays), "shape": list(arrays[0].shape), "center_std": float(np.std(arrays[len(arrays)//2]))}


def demo_candidates(arr):
    """Geometric connected regions for synthetic workflow checks only."""
    mask = arr > 240
    seen = set()
    boxes = []
    for y, x in zip(*np.where(mask)):
        if (int(y), int(x)) in seen:
            continue
        stack, points = [(int(y), int(x))], []
        seen.add(stack[0])
        while stack:
            yy, xx = stack.pop()
            points.append((yy, xx))
            for ny, nx in ((yy-1, xx), (yy+1, xx), (yy, xx-1), (yy, xx+1)):
                if 0 <= ny < arr.shape[0] and 0 <= nx < arr.shape[1] and mask[ny, nx] and (ny, nx) not in seen:
                    seen.add((ny, nx))
                    stack.append((ny, nx))
        if 3 <= len(points) <= 300:
            yy, xx = zip(*points)
            boxes.append({"xyxy": [min(xx), min(yy), max(xx)+1, max(yy)+1], "confidence": None, "class": "synthetic bright region"})
    return {"candidates": boxes, "engine": "synthetic geometric fixture", "trained": False, "device": "CPU", "warning": "合成流程測試，非模型結果。"}


def nms(boxes, scores, threshold=0.5):
    order = np.argsort(scores)[::-1]
    keep = []
    while order.size and len(keep) < 300:
        i = int(order[0]); keep.append(i)
        other = order[1:]
        left = np.maximum(boxes[i, :2], boxes[other, :2])
        right = np.minimum(boxes[i, 2:], boxes[other, 2:])
        intersection = np.prod(np.maximum(right - left, 0), axis=1)
        areas = np.prod(np.maximum(boxes[other, 2:] - boxes[other, :2], 0), axis=1)
        area = np.prod(np.maximum(boxes[i, 2:] - boxes[i, :2], 0))
        iou = intersection / np.maximum(area + areas - intersection, 1e-9)
        order = other[iou <= threshold]
    return keep


class Detector:
    def __init__(self, root, config):
        self.root = Path(root)
        self.config = config
        self.session = None
        self.session_hash = None

    def manifest(self):
        path = self.root / "models" / "detector.json"
        if not path.exists():
            raise ValueError("未配置 CT 模型。請使用 scripts/register_model.py 登錄 ST-CAC ONNX 與 SHA256。")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        model = (path.parent / manifest["file"]).resolve()
        if not model.is_relative_to(path.parent.resolve()) or not model.is_file():
            raise ValueError("模型路徑無效。")
        if sha256(model) != manifest["sha256"]:
            raise ValueError("模型 SHA256 不符，拒絕推論。")
        if manifest.get("input_size") != 512 or manifest.get("channels") != "previous-center-next" or manifest.get("output") != "yolov8-raw-one-class":
            raise ValueError("模型契約不符。")
        return manifest, model

    def detect(self, arrays, index, is_hu):
        if len(arrays) < 3:
            raise ValueError("2.5D 模型至少需要三張依序切片，禁止將單張假充相鄰序列。")
        if arrays[0].shape != (512, 512):
            raise ValueError("目前模型契約只接受 512×512 影像，避免未驗證的縮放誤差。")
        if is_hu:
            raise ValueError("目前 ST-CAC 模型使用 8-bit PNG；DICOM 可檢視，推論須先以訓練時相同窗位匯出及驗證。")
        manifest, path = self.manifest()
        import onnxruntime as ort
        wanted = self.config.get("vision_provider", "CPUExecutionProvider")
        if wanted not in ort.get_available_providers():
            raise ValueError(f"指定執行器不可用：{wanted}；未靜默改用其他裝置。")
        if self.session is None or self.session_hash != manifest["sha256"]:
            options = ort.SessionOptions()
            options.intra_op_num_threads = 4
            options.enable_mem_pattern = False
            options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            self.session = ort.InferenceSession(str(path), sess_options=options, providers=[wanted])
            self.session_hash = manifest["sha256"]
        inp = self.session.get_inputs()[0]
        if inp.shape != [1, 3, 512, 512] or inp.type != "tensor(float)":
            raise ValueError("ONNX 輸入須為 float32 [1,3,512,512]。")
        indices = [max(index-1, 0), index, min(index+1, len(arrays)-1)]
        tensor = np.stack([arrays[i] for i in indices]).astype(np.float32)[None] / 255.0
        if tensor.min() < 0 or tensor.max() > 1:
            raise ValueError("模型輸入須在 8-bit 0–255 範圍。")
        pred = self.session.run(None, {inp.name: tensor})[0]
        if pred.ndim != 3 or pred.shape[0] != 1 or pred.shape[1] != 5 or pred.shape[2] > 100000:
            raise ValueError("ONNX 輸出須為 [1,5,N]：xywh 與一類信心值，無內建 NMS。")
        rows = pred[0].T
        if not np.isfinite(rows).all() or np.any((rows[:,4]<0)|(rows[:,4]>1)):
            raise ValueError("模型輸出含無效數值或未 sigmoid 的 confidence。")
        rows = rows[(rows[:,4] >= self.config.get("confidence", .25)) & (rows[:,2]>0) & (rows[:,3]>0)]
        boxes = np.c_[rows[:,:2] - rows[:,2:4]/2, rows[:,:2] + rows[:,2:4]/2]
        boxes = np.clip(boxes, 0, 512)
        keep = nms(boxes, rows[:,4])
        return {"candidates": [{"xyxy": boxes[i].round(3).tolist(), "confidence": float(rows[i,4]), "class": "CAC candidate"} for i in keep], "engine": manifest["name"], "trained": manifest.get("trained", False), "device": self.session.get_providers(), "requested_provider": wanted, "model_sha256": manifest["sha256"], "center_index": index, "input_indices": indices, "threshold": self.config.get("confidence", .25), "scope": "candidate localization; no Agatston score", "warning": "訓練來源依登錄聲明；本平台未重新驗證其臨床效能。"}
